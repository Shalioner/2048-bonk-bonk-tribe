"""Isolated native search checks, no full games or product-core mutation."""
import ctypes
from functools import lru_cache
import importlib.util
import itertools
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
import unittest

WORK = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("native_search_variants", WORK / "experiments/search_alternatives/variants.py")
variants = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(variants)
DEBUG = b'''
extern "C" float debug_leaf(board_t b) { return score_heur_board(b); }
extern "C" float debug_cutoff(board_t b) { eval_state st; st.depth_limit=0; return score_tilechoose_node(st,b,1.0f); }
'''
CANONICAL_DEBUG = b'''
extern "C" board_t debug_transpose(board_t b) {return bonk_search::transpose(b);}
extern "C" board_t debug_horizontal(board_t b) {return bonk_search::horizontal(b);}
extern "C" board_t debug_vertical(board_t b) {return bonk_search::vertical(b);}
extern "C" board_t debug_canonical(board_t b) {return bonk_search::canonical(b);}
'''


def pack(board):
    return sum(rank << (4*i) for i, rank in enumerate(board))


def unpack(board):
    return tuple((board >> (4*i)) & 15 for i in range(16))


def move(board, direction):
    result = list(board)
    for line in range(4):
        cells = [r*4+line for r in range(4)] if direction < 2 else [line*4+c for c in range(4)]
        if direction in (1, 3):
            cells.reverse()
        values = [board[c] for c in cells if board[c]]
        output, i = [], 0
        while i < len(values):
            if i+1 < len(values) and values[i] == values[i+1] and values[i] < 11:
                output.append(values[i]+1)
                i += 2
            else:
                output.append(values[i])
                i += 1
        for cell, value in zip(cells, output+[0]*(4-len(output))):
            result[cell] = value
    return tuple(result)


PERMS = []
for transposed in (False, True):
    for horizontal, vertical in itertools.product((False, True), repeat=2):
        permutation = []
        for i in range(16):
            r, c = divmod(i, 4)
            if transposed:
                r, c = c, r
            if horizontal:
                c = 3-c
            if vertical:
                r = 3-r
            permutation.append(r*4+c)
        PERMS.append(permutation)


def transform(board, permutation):
    result = [0]*16
    for i, value in enumerate(board):
        result[permutation[i]] = value
    return tuple(result)


def canonical(board):
    return min((transform(board, p) for p in PERMS), key=pack)


DIRECTIONS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def mapped_direction(direction, permutation):
    dr, dc = DIRECTIONS[direction]
    a, b = permutation[5], permutation[(1+dr)*4+1+dc]
    ar, ac = divmod(a, 4)
    br, bc = divmod(b, 4)
    return DIRECTIONS.index((br-ar, bc-ac))


def leaf_reference(board):
    if board.count(11) >= 3:
        return 1e9
    def row(values):
        compact = [x for x in values if x]
        merges = sum(len(g) for rank, group in itertools.groupby(compact) for g in [list(group)] if rank < 11 and len(g) >= 2)
        left = sum(max(a**4-b**4, 0) for a, b in zip(values, values[1:]))
        right = sum(max(b**4-a**4, 0) for a, b in zip(values, values[1:]))
        return 200000+270*values.count(0)+700*merges-47*min(left, right)-11*sum(x**3.5 for x in values)
    return sum(row(board[4*r:4*r+4]) for r in range(4)) + sum(row(board[c::4]) for c in range(4))


def value_reference(board, direction, depth, threshold, table):
    f32 = lambda x: ctypes.c_float(x).value
    @lru_cache(None)
    def expand(b, remaining, probability):
        empty = [i for i, value in enumerate(b) if value == 0]
        cp = f32(probability/len(empty))
        total = 0.0
        for cell in empty:
            for rank, p in enumerate(table[max(b)], 1):
                if p <= 0:
                    continue
                child = list(b)
                child[cell] = rank
                child = tuple(child)
                best = 0.0  # Preserve the existing signed-max convention.
                for d in range(4):
                    after = move(child, d)
                    if after != child:
                        best = max(best, chance(after, remaining-1, f32(cp*p)))
                total += p*best
        return total/len(empty)
    def chance(b, remaining, probability):
        if b.count(11) >= 3:
            return 1e9
        if probability < threshold or remaining <= 0:
            return leaf_reference(b)
        return expand(canonical(b), remaining, probability)
    after = move(board, direction)
    return chance(after, depth, 1.0)+1e-6 if after != board else 0.0


class SearchVariantTests(unittest.TestCase):
    WITNESS = (0,0,0,0,0,1,1,0,0,1,3,0,2,3,2,11)
    CAP1 = (0,0,0,2,1,1,3,5,0,3,5,3,0,0,1,11)
    CAP2 = (0,6,1,11,1,4,5,11,0,2,4,3,1,2,3,1)
    GENERIC = (15,7,6,4,4,3,1,2,3,2,1,0,2,1,0,11)
    DENSE = (11,2,1,2,2,1,2,1,1,2,1,2,2,1,0,11)

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix=".search-variant-test-", dir=WORK)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = (WORK / "vendor/engine_adapted.cpp").read_bytes()
        cls.libs = {}
        flags = ["clang++", "-O3", "-std=c++11", "-dynamiclib", "-pthread"] if sys.platform == "darwin" else ["g++", "-O3", "-std=c++11", "-shared", "-fPIC", "-pthread"]
        for kind in ("baseline", "canonical", "serial_shallow"):
            cpp = Path(cls.temp.name) / (kind+".cpp")
            library = cpp.with_suffix(".dylib" if sys.platform == "darwin" else ".so")
            cpp.write_bytes(variants.variant_source(cls.base, dict(kind=kind)) + DEBUG + (CANONICAL_DEBUG if kind == "canonical" else b""))
            built = subprocess.run(flags+["-I", str(WORK / "vendor"), str(cpp), "-o", str(library)], capture_output=True, text=True)
            if built.returncode:
                raise RuntimeError(built.stdout+built.stderr)
            lib = ctypes.CDLL(str(library))
            score_args = [ctypes.c_uint64, ctypes.c_int, ctypes.c_int, ctypes.c_float]
            for name, arguments, returns in (("execute_move", [ctypes.c_int, ctypes.c_uint64], ctypes.c_uint64),
                    ("score_levels_thresh", score_args, ctypes.c_float), ("score_levels_reference", score_args, ctypes.c_float),
                    ("debug_leaf", [ctypes.c_uint64], ctypes.c_float), ("debug_cutoff", [ctypes.c_uint64], ctypes.c_float),
                    ("set_spawn_table", [ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_int], ctypes.c_int),
                    ("get_spawn_prob", [ctypes.c_int, ctypes.c_int], ctypes.c_float)):
                function = getattr(lib, name)
                function.argtypes, function.restype = arguments, returns
            lib.solve_budget_progressive.argtypes = [ctypes.c_uint64, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int,
                                                    ctypes.c_double, ctypes.c_double, ctypes.POINTER(ctypes.c_double)]
            lib.solve_budget_progressive.restype = ctypes.c_int
            if kind == "canonical":
                for name in ("debug_transpose", "debug_horizontal", "debug_vertical", "debug_canonical"):
                    function = getattr(lib, name)
                    function.argtypes, function.restype = [ctypes.c_uint64], ctypes.c_uint64
            lib.init_tables()
            cls.libs[kind] = lib
        cls.table = [[1]+[0]*14 for _ in range(16)]
        cls.table[11] = [.8,.2]+[0]*13
        cls.table[15] = [.25]+[0]*13+[.75]
        cls.max_value_difference = 0.0
        cls.max_reference_difference = 0.0
        cls.max_budget_excess = 0.0

    @classmethod
    def tearDownClass(cls):
        print(json.dumps(dict(max_baseline_value_delta=cls.max_value_difference,
                              max_python_reference_delta=cls.max_reference_difference, max_budget_excess_s=cls.max_budget_excess)))

    def setUp(self):
        for lib in self.libs.values():
            flat = [x for row in self.table for x in row]
            self.assertEqual(lib.set_spawn_table((ctypes.c_float*240)(*flat), 16, 15), 0)

    @staticmethod
    def scores(lib, board, depth, threshold, reference=False):
        function = lib.score_levels_reference if reference else lib.score_levels_thresh
        return {d: function(pack(board), d, depth, threshold) for d in range(4) if move(board, d) != board}

    @staticmethod
    def solve(lib, board, budget, depth=8, thresholds=(1e-4,1e-5,1e-6), growth=5):
        info = (ctypes.c_double*10)()
        allowed = sum(1 << d for d in range(4) if move(board, d) != board)
        ts = (ctypes.c_float*len(thresholds))(*thresholds)
        started = time.monotonic()
        direction = lib.solve_budget_progressive(pack(board), allowed, depth, ts, len(thresholds), budget, growth, info)
        return direction, list(info), time.monotonic()-started

    def test_source_identity_and_no_combinations(self):
        for kind in ("baseline", "precision", "banked_endgame"):
            self.assertIs(variants.variant_source(self.base, dict(kind=kind)), self.base)
        serial = variants.variant_source(self.base, dict(kind="serial_shallow"))
        split = b'extern "C" int solve_budget_progressive('
        self.assertEqual(serial.split(split)[0].replace(b"// BONK_SEARCH_VARIANT: serial_shallow\n", b""), self.base.split(split)[0])
        self.assertIn(b"if (pass_depth <= 2)", serial)
        for kind in ("canonical", "serial_shallow"):
            source = variants.variant_source(self.base, dict(kind=kind))
            with self.assertRaises(ValueError):
                variants.variant_source(source, dict(kind=kind))
        for arm in (dict(kind="other"), dict(kind="serial_shallow", serial_max_depth=3)):
            with self.assertRaises(ValueError):
                variants.variant_source(self.base, arm)

    def test_canonical_all_d4_and_direction_equivariance(self):
        lib = self.libs["canonical"]
        rng = random.Random(31102)
        boards = [self.WITNESS, self.CAP2, self.GENERIC, tuple(range(16))]
        boards += [unpack(1 << bit) for bit in range(64)]
        boards += [unpack(rng.getrandbits(64)) for _ in range(128)]
        for board in boards:
            self.assertEqual(unpack(lib.debug_canonical(pack(board))), canonical(board))
            self.assertEqual(unpack(lib.debug_transpose(pack(board))), transform(board, PERMS[4]))
            self.assertEqual(unpack(lib.debug_horizontal(pack(board))), transform(board, PERMS[2]))
            self.assertEqual(unpack(lib.debug_vertical(pack(board))), transform(board, PERMS[1]))
            for permutation in PERMS:
                changed = transform(board, permutation)
                self.assertEqual(lib.debug_canonical(pack(changed)), lib.debug_canonical(pack(board)))
                for d in range(4):
                    mapped = mapped_direction(d, permutation)
                    self.assertEqual(unpack(lib.execute_move(mapped, pack(changed))), transform(move(board, d), permutation))

    def test_canonical_score_and_argmax_sets_are_d4_equivariant(self):
        lib = self.libs["canonical"]
        symmetric = (0,0,0,0,0,1,1,0,0,1,1,0,0,0,0,0)
        for board in (self.WITNESS, self.CAP2, symmetric):
            original = self.scores(lib, board, 2, 1e-4)
            best = {d for d, value in original.items() if value == max(original.values())}
            for permutation in PERMS:
                changed = self.scores(lib, transform(board, permutation), 2, 1e-4)
                self.assertEqual({mapped_direction(d, permutation): value for d, value in original.items()}, changed)
                self.assertEqual({mapped_direction(d, permutation) for d in best}, {d for d, value in changed.items() if value == max(changed.values())})

    def test_cached_reference_exact_and_baseline_value_equivalence(self):
        base, canonical_lib, serial = (self.libs[k] for k in ("baseline", "canonical", "serial_shallow"))
        for board in (self.WITNESS, self.CAP1, self.CAP2, self.GENERIC):
            for depth in (2,3,4):
                for threshold in (0.0,1e-4,1e-5):
                    original = self.scores(base, board, depth, threshold)
                    self.assertEqual(self.scores(serial, board, depth, threshold), original)
                    actual = self.scores(canonical_lib, board, depth, threshold)
                    self.assertEqual(actual, self.scores(canonical_lib, board, depth, threshold, reference=True))
                    for d, value in actual.items():
                        difference = abs(value-original[d])
                        type(self).max_value_difference = max(self.max_value_difference, difference)
                        self.assertLessEqual(difference, max(1, abs(original[d])*1e-6))

    def test_canonical_matches_independent_python_reference(self):
        lib = self.libs["canonical"]
        table = [[lib.get_spawn_prob(stage, rank) for rank in range(1,16)] for stage in range(16)]
        for board in (self.DENSE, self.GENERIC):
            for depth in (2,3):
                for threshold in (0.0,1e-4):
                    for d in self.scores(lib, board, depth, threshold):
                        expected = value_reference(board, d, depth, threshold, table)
                        actual = lib.score_levels_thresh(pack(board), d, depth, threshold)
                        type(self).max_reference_difference = max(self.max_reference_difference, abs(actual-expected))
                        self.assertAlmostEqual(actual, expected, delta=max(1, abs(expected)*1e-6))

    def test_leaf_cutoff_unchanged_and_goal_bypass(self):
        base = self.libs["baseline"]
        for lib in self.libs.values():
            for board in (self.WITNESS, self.CAP2, self.GENERIC, (11,11,11)+(0,)*13):
                self.assertEqual(lib.debug_cutoff(pack(board)), base.debug_leaf(pack(board)))

    def assert_matches_completed_pass(self, lib, board, direction, info, first_threshold):
        threshold = first_threshold if info[7] else info[1]
        scores = self.scores(lib, board, int(info[8]), threshold)
        self.assertIn(direction, {d for d, value in scores.items() if value == max(scores.values())})

    def test_zero_fallback_and_successful_refinement(self):
        for lib in self.libs.values():
            for thresholds in ((1e-4,1e-5,1e-6), (1e-5,1e-6)):
                d, info, elapsed = self.solve(lib, self.CAP2, 0, thresholds=thresholds)
                self.assertLessEqual(elapsed, .05)
                self.assertEqual((info[0], info[7], info[8]), (0,1,1))
                self.assert_matches_completed_pass(lib, self.CAP2, d, info, thresholds[0])
            d, info, elapsed = self.solve(lib, self.CAP2, 1, depth=3, thresholds=(1e-4,1e-5), growth=0)
            self.assertEqual((info[0], info[7], info[8], info[9]), (3,0,3,3))
            self.assert_matches_completed_pass(lib, self.CAP2, d, info, 1e-4)
            d, info, elapsed = self.solve(lib, self.CAP2, 1, depth=1, thresholds=(1e-4,1e-5), growth=0)
            self.assertEqual((info[0], info[7], info[8], info[9]), (1,0,1,1))
            self.assert_matches_completed_pass(lib, self.CAP2, d, info, 1e-4)

    def test_abort_retains_last_completed_depth(self):
        for kind in ("canonical", "serial_shallow"):
            lib = self.libs[kind]
            d, info, elapsed = self.solve(lib, self.CAP2, .02, depth=30, thresholds=(1e-12,), growth=0)
            self.assertLessEqual(elapsed, .07)
            self.assertTrue(info[4])
            self.assertFalse(info[7])
            self.assertGreaterEqual(info[8], 2)
            self.assertLess(info[8], 30)
            self.assert_matches_completed_pass(lib, self.CAP2, d, info, 1e-12)

    def test_hard_budgets_small_corpus_without_slack_change(self):
        for kind, lib in self.libs.items():
            for board in (self.WITNESS, self.CAP2, self.GENERIC):
                for budget in (.001,.005,.01,.02,.04):
                    with self.subTest(kind=kind, budget=budget):
                        d, info, elapsed = self.solve(lib, board, budget)
                        type(self).max_budget_excess = max(self.max_budget_excess, elapsed-budget)
                        self.assertLessEqual(elapsed, budget+.05)
                        self.assertEqual(info[9], 8)
                        self.assertGreaterEqual(info[8], 1)
                        self.assertLessEqual(info[8], 8)
                        self.assert_matches_completed_pass(lib, board, d, info, 1e-4)


if __name__ == "__main__":
    unittest.main()
