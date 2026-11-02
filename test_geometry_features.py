"""Finite structural-feature checks; isolated native builds, no complete games."""
import ctypes
import importlib.util
import itertools
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

WORK = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("geometry_features", WORK / "experiments/geometry/features.py")
features = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(features)
ARMS = [dict(kind="baseline", strength=0)] + [dict(kind=kind, strength=strength)
        for kind, strengths in (("segments", (.5, 1)), ("geometry", (2000, 4000)), ("risk", (.5, 1))) for strength in strengths]
COMMON_DEBUG = b'''
extern "C" float debug_eval(board_t b) { return score_heur_board(b); }
extern "C" const float* debug_rows() { return heur_score_table; }
'''
EXTRA_DEBUG = b'''
extern "C" unsigned debug_mask(board_t b) { return bonk_geometry::cap_mask(b); }
extern "C" int debug_exact_caps(board_t b) { return bonk_geometry::exact_cap_count(b); }
extern "C" const unsigned char* debug_edges() { bonk_geometry::init_geometry_tables(); return bonk_geometry::free_edges_table; }
extern "C" const unsigned char* debug_max_edges() { bonk_geometry::init_geometry_tables(); return bonk_geometry::max_edges_table; }
extern "C" float debug_segment(unsigned row, float strength) {
    unsigned line[4] = {row&15, (row>>4)&15, (row>>8)&15, (row>>12)&15};
    float left=0, right=0;
    for(int i=1;i<4;++i) {
        if(line[i-1]>line[i]) left += pow(line[i-1],4.0f)-pow(line[i],4.0f);
        else right += pow(line[i],4.0f)-pow(line[i-1],4.0f);
    }
    return bonk_geometry::segmented_penalty(line,4.0f,left,right,strength);
}
extern "C" float debug_risk(board_t b) { return bonk_geometry::immediate_loss_risk(b,spawn_table[get_max_rank(b)]); }
extern "C" float debug_adjust(board_t b,float raw,float strength) {
    return bonk_geometry::risk_adjusted(b,raw,strength,spawn_table[get_max_rank(b)]);
}
extern "C" float debug_risk_enumerated(board_t b) {
    if(bonk_geometry::exact_cap_count(b)!=2) return 0;
    int empty=0; for(int c=0;c<16;++c) empty += ((b>>(4*c))&15)==0;
    if(empty==0 || empty>3) return 0;
    double risk=0; bonk_geometry::move_function move=execute_move;
    const float* probabilities=spawn_table[get_max_rank(b)];
    for(int c=0;c<16;++c) if(((b>>(4*c))&15)==0) for(int r=1;r<=15;++r) {
        board_t child=b | (static_cast<board_t>(r)<<(4*c));
        if(probabilities[r-1]<=0 || bonk_geometry::exact_cap_count(child)>=3) continue;
        bool legal=false; for(int d=0;d<4;++d) if(move(d,child)!=child) {legal=true;break;}
        if(!legal) risk += static_cast<double>(probabilities[r-1])/empty;
    }
    return static_cast<float>(std::max(0.0,std::min(1.0,risk)));
}
'''


def pack(board):
    return sum(rank << (4 * cell) for cell, rank in enumerate(board))


def unpack(board):
    return tuple((board >> (4 * cell)) & 15 for cell in range(16))


def move(board, direction):
    """Independent ordinary Python move: native U,D,L,R; ranks>=11 never merge."""
    result = list(board)
    for line in range(4):
        cells = [row * 4 + line for row in range(4)] if direction < 2 else [line * 4 + col for col in range(4)]
        if direction in (1, 3):
            cells.reverse()
        values = [board[cell] for cell in cells if board[cell]]
        merged, index = [], 0
        while index < len(values):
            if index + 1 < len(values) and values[index] == values[index+1] and values[index] < 11:
                merged.append(values[index] + 1)
                index += 2
            else:
                merged.append(values[index])
                index += 1
        for cell, rank in zip(cells, merged + [0] * (4-len(merged))):
            result[cell] = rank
    return tuple(result)


def row_penalty(line):
    left = sum(max(a**4-b**4, 0) for a, b in zip(line, line[1:]))
    right = sum(max(b**4-a**4, 0) for a, b in zip(line, line[1:]))
    return min(left, right)


def segmented_reference(line, strength):
    groups = [tuple(g) for cap, group in itertools.groupby(line, lambda v: v >= 11) if not cap for g in [group]]
    return (1-strength) * row_penalty(line) + strength * sum(row_penalty(g) for g in groups)


def edge_reference(mask):
    return sum(not (mask & (1 << a) or mask & (1 << b)) for a, b in EDGES)


EDGES = [(r*4+c, r*4+c+1) for r in range(4) for c in range(3)] + [(r*4+c, (r+1)*4+c) for r in range(3) for c in range(4)]
PERMUTATIONS = []
for mirror in (False, True):
    for turns in range(4):
        permutation = []
        for index in range(16):
            r, c = divmod(index, 4)
            if mirror:
                c = 3-c
            for _ in range(turns):
                r, c = c, 3-r
            permutation.append(4*r+c)
        PERMUTATIONS.append(permutation)


def transform(board, permutation):
    result = [0] * 16
    for index, rank in enumerate(board):
        result[permutation[index]] = rank
    return tuple(result)


def risk_reference(board, probabilities):
    empty = [i for i, rank in enumerate(board) if rank == 0]
    if board.count(11) != 2 or not 0 < len(empty) <= 3:
        return 0.0
    result = 0.0
    for cell in empty:
        for rank, probability in enumerate(probabilities, 1):
            spawned = list(board)
            spawned[cell] = rank
            spawned = tuple(spawned)
            if spawned.count(11) < 3 and all(move(spawned, d) == spawned for d in range(4)):
                result += probability / len(empty)
    return min(1, max(0, result))


class GeometryFeatureTests(unittest.TestCase):
    POSITIVE = (11,2,1,2, 2,1,2,1, 1,2,1,2, 2,1,0,11)
    DENSE = (0,6,1,11, 1,4,5,11, 0,2,4,3, 1,2,3,1)

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix=".geometry-test-", dir=WORK)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = (WORK / "vendor/engine_adapted.cpp").read_bytes()
        cls.libs = []
        flags = ["clang++", "-O3", "-std=c++11", "-dynamiclib", "-pthread"] if sys.platform == "darwin" else ["g++", "-O3", "-std=c++11", "-shared", "-fPIC", "-pthread"]
        for index, arm in enumerate(ARMS):
            cpp = Path(cls.temp.name) / (str(index) + ".cpp")
            library = cpp.with_suffix(".dylib" if sys.platform == "darwin" else ".so")
            generated = features.variant_source(cls.base, arm)
            cpp.write_bytes(generated + COMMON_DEBUG + (EXTRA_DEBUG if index else b""))
            subprocess.run(flags + ["-I", str(WORK / "vendor"), str(cpp), "-o", str(library)], check=True, capture_output=True)
            lib = ctypes.CDLL(str(library))
            for name, arguments, returns in (("debug_eval", [ctypes.c_uint64], ctypes.c_float),
                    ("debug_rows", [], ctypes.POINTER(ctypes.c_float)), ("execute_move", [ctypes.c_int, ctypes.c_uint64], ctypes.c_uint64),
                    ("set_spawn_table", [ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_int], ctypes.c_int),
                    ("get_spawn_prob", [ctypes.c_int, ctypes.c_int], ctypes.c_float),
                    ("score_levels_thresh", [ctypes.c_uint64, ctypes.c_int, ctypes.c_int, ctypes.c_float], ctypes.c_float),
                    ("score_levels_reference", [ctypes.c_uint64, ctypes.c_int, ctypes.c_int, ctypes.c_float], ctypes.c_float)):
                function = getattr(lib, name)
                function.argtypes, function.restype = arguments, returns
            if index:
                for name, arguments, returns in (("debug_mask", [ctypes.c_uint64], ctypes.c_uint),
                        ("debug_exact_caps", [ctypes.c_uint64], ctypes.c_int),
                        ("debug_edges", [], ctypes.POINTER(ctypes.c_ubyte)), ("debug_max_edges", [], ctypes.POINTER(ctypes.c_ubyte)),
                        ("debug_segment", [ctypes.c_uint, ctypes.c_float], ctypes.c_float),
                        ("debug_risk", [ctypes.c_uint64], ctypes.c_float),
                        ("debug_risk_enumerated", [ctypes.c_uint64], ctypes.c_float),
                        ("debug_adjust", [ctypes.c_uint64, ctypes.c_float, ctypes.c_float], ctypes.c_float)):
                    function = getattr(lib, name)
                    function.argtypes, function.restype = arguments, returns
            lib.init_tables()
            cls.libs.append(lib)
        model = json.loads((WORK / "spawn-model.default.json").read_text())["stages"]
        stages = sorted(map(int, model))
        cls.default_table = [model[str(max(s for s in stages if s <= stage))] for stage in range(16)]
        cls.max_symmetry_error = 0.0
        cls.max_row_reverse_error = 0.0

    @classmethod
    def tearDownClass(cls):
        print(json.dumps(dict(native_variants=7, max_d4_eval_spread=cls.max_symmetry_error,
                              max_row_reverse_spread=cls.max_row_reverse_error)))

    def setUp(self):
        for lib in self.libs:
            self.set_table(lib, self.default_table)

    @staticmethod
    def set_table(lib, table):
        values = [float(x) for row in table for x in row]
        if lib.set_spawn_table((ctypes.c_float * 240)(*values), 16, 15) != 0:
            raise AssertionError("native spawn table rejected")

    def test_generator_baseline_identity_and_strict_independent_kinds(self):
        self.assertIs(features.variant_source(self.base, ARMS[0]), self.base)
        for arm in ARMS[1:]:
            generated = features.variant_source(self.base, arm)
            self.assertEqual(generated.count(b'#include "geometry_features.h"'), 1)
            self.assertIn(b"if(goal(board)) return 1e9f;", generated)
            self.assertIn(b"struct search_cache_key", generated)
            self.assertNotIn(b'extern "C" float debug_', generated)
            with self.assertRaises(ValueError):
                features.variant_source(generated, arm)
        for arm in (dict(kind="risk", strength=2), dict(kind="baseline", strength=1), dict(kind="unknown", strength=0), dict(kind="segments", strength=float("nan"))):
            with self.assertRaises(ValueError):
                features.variant_source(self.base, arm)
        with self.assertRaises(ValueError):
            features.variant_source(b"not an engine", ARMS[1])

    def test_all_rows_segments_reverse_and_noncap_bit_identity(self):
        original = self.libs[0].debug_rows()
        modified = [self.libs[i].debug_rows() for i in (1, 2)]
        for row in range(65536):
            line = tuple((row >> (4*i)) & 15 for i in range(4))
            reverse = sum(rank << (4*i) for i, rank in enumerate(reversed(line)))
            for index, strength in enumerate((.5, 1)):
                actual = self.libs[index+1].debug_segment(row, strength)
                self.assertEqual(actual, segmented_reference(line, strength))
                self.assertEqual(actual, self.libs[index+1].debug_segment(reverse, strength))
                if max(line) < 11:
                    self.assertEqual(modified[index][row], original[row])
                # Full row also contains a floating rank**3.5 sum; it is not bitwise reversal invariant even upstream.
                spread = abs(modified[index][row] - modified[index][reverse])
                type(self).max_row_reverse_error = max(self.max_row_reverse_error, spread)
                self.assertLessEqual(spread, 1.0)
                for direction in (2, 3):
                    self.assertEqual(self.libs[index+1].execute_move(direction, row), self.libs[0].execute_move(direction, row))

    def test_geometry_all_masks_reference_maximum_and_d4(self):
        lib = self.libs[3]
        edges, maximum = lib.debug_edges(), lib.debug_max_edges()
        reference_max = [0] * 17
        transforms = []
        for permutation in PERMUTATIONS:
            low = [sum(((n >> i) & 1) << permutation[i] for i in range(8)) for n in range(256)]
            high = [sum(((n >> i) & 1) << permutation[i+8] for i in range(8)) for n in range(256)]
            transforms.append((low, high))
        for mask in range(65536):
            expected = edge_reference(mask)
            self.assertEqual(edges[mask], expected)
            caps = bin(mask).count("1")
            reference_max[caps] = max(reference_max[caps], expected)
            for low, high in transforms:
                self.assertEqual(edges[low[mask & 255] | high[mask >> 8]], expected)
        self.assertEqual(list(maximum[:17]), reference_max)
        self.assertTrue(all(edges[m] <= maximum[bin(m).count("1")] for m in range(65536)))

    def test_fast_mask_all_rank_cell_pairs_and_random_generic_boards(self):
        lib = self.libs[3]
        for cell in range(16):
            for rank in range(16):
                self.assertEqual(lib.debug_mask(rank << (4*cell)), (1 << cell) if rank >= 11 else 0)
                self.assertEqual(lib.debug_exact_caps(rank << (4*cell)), int(rank == 11))
        for count in range(17):
            self.assertEqual(lib.debug_exact_caps(pack((11,) * count + (0,) * (16-count))), count)
        rng = random.Random(30119)
        for _ in range(4096):
            packed = rng.getrandbits(64)
            expected = sum((rank >= 11) << i for i, rank in enumerate(unpack(packed)))
            self.assertEqual(lib.debug_mask(packed), expected)
            self.assertEqual(lib.debug_exact_caps(packed), unpack(packed).count(11))

    def test_risk_python_moves_boundary_custom_spawn_and_goal(self):
        lib = self.libs[6]
        boards = [self.POSITIVE, self.DENSE, tuple(0 if i in (2, 9, 14) else rank for i, rank in enumerate(self.POSITIVE)),
                  tuple(3 if rank == 0 else rank for rank in self.POSITIVE),
                  (11,12,13,14, 15,1,2,11, 1,2,3,4, 2,3,4,0), (11,11,11) + (1,2,3,4) * 3 + (0,)]
        tables = [self.default_table, [[0]*10+[1]+[0]*4 for _ in range(16)],
                  [[.5]+[0]*9+[.3,.1,0,0,.1] for _ in range(16)]]
        staged = [[1]+[0]*14 for _ in range(16)]
        staged[11] = [0,0,1]+[0]*12
        staged[15] = [0]*10+[1]+[0]*4
        tables.append(staged)  # Generic rank15 boards must use stage15, not hardcoded11.
        for table in tables:
            self.set_table(lib, table)
            for board in boards:
                self.assertEqual(len(board), 16)
                probabilities = [lib.get_spawn_prob(max(board), rank) for rank in range(1, 16)]
                expected = risk_reference(board, probabilities)
                for permutation in PERMUTATIONS:
                    changed = transform(board, permutation)
                    self.assertAlmostEqual(lib.debug_risk(pack(changed)), expected, delta=1e-7)
                    self.assertEqual(lib.debug_risk(pack(changed)), lib.debug_risk_enumerated(pack(changed)))
                    for direction in range(4):
                        self.assertEqual(unpack(lib.execute_move(direction, pack(changed))), move(changed, direction))
                for raw in (-100.0, 0.0, 100.0):
                    for strength in (.5, 1):
                        adjusted = raw if raw < 0 else raw * (1-strength*expected)
                        self.assertAlmostEqual(lib.debug_adjust(pack(board), raw, strength), adjusted, delta=1e-5)

    def test_risk_positive_raw_is_soft_and_negative_raw_unchanged(self):
        packed = pack(self.POSITIVE)
        raw = self.libs[0].debug_eval(packed)
        self.assertGreater(raw, 0)
        for index, strength in ((5, .5), (6, 1)):
            lib = self.libs[index]
            risk = lib.debug_risk(packed)
            self.assertGreater(risk, 0)
            self.assertAlmostEqual(lib.debug_eval(packed), raw * (1-strength*risk), delta=max(1, abs(raw)*1e-6))
            self.assertEqual(lib.debug_adjust(packed, -123.0, strength), -123.0)
            negative = (11,15,1,15,15,1,15,1,1,15,1,15,15,1,0,11)
            negative_raw = self.libs[0].debug_eval(pack(negative))
            self.assertLess(negative_raw, 0)
            self.assertEqual(lib.debug_eval(pack(negative)), negative_raw)

    def test_closed_form_risk_matches_native_and_python_enumeration(self):
        lib = self.libs[6]
        rng = random.Random(97031)
        other_ranks = list(range(1, 11)) + list(range(12, 16))
        for _ in range(300):
            selected = rng.sample(range(16), 3)
            board = [rng.choice(other_ranks) for _ in range(16)]
            board[selected[0]] = 0
            board[selected[1]] = board[selected[2]] = 11
            table = [[rng.randrange(6) for rank in range(15)] for stage in range(16)]
            self.set_table(lib, table)
            board = tuple(board)
            probabilities = [lib.get_spawn_prob(max(board), rank) for rank in range(1, 16)]
            expected = risk_reference(board, probabilities)
            actual = lib.debug_risk(pack(board))
            self.assertEqual(actual, lib.debug_risk_enumerated(pack(board)))
            self.assertAlmostEqual(actual, expected, delta=1e-7)

    def test_two_or_three_empties_exact_zero_shortcut_generic_movable_caps(self):
        lib = self.libs[6]
        self.set_table(lib, [[1] * 15 for _ in range(16)])
        rng = random.Random(20261102)
        other_ranks = list(range(1, 11)) + list(range(12, 16))
        for empty_count in (2, 3):
            for _ in range(100):
                selected = rng.sample(range(16), empty_count + 2)
                board = [rng.choice(other_ranks) for _ in range(16)]
                for cell in selected[:empty_count]:
                    board[cell] = 0
                for cell in selected[empty_count:]:
                    board[cell] = 11
                board = tuple(board)
                probabilities = [lib.get_spawn_prob(max(board), rank) for rank in range(1, 16)]
                self.assertEqual(risk_reference(board, probabilities), 0)
                self.assertEqual(lib.debug_risk(pack(board)), 0)
                self.assertEqual(lib.debug_risk_enumerated(pack(board)), 0)

    def test_geometry_score_exact_soft_penalty_and_all_variant_move_parity(self):
        central = tuple(11 if i in (5, 10) else 1+i%2 for i in range(16))
        boards = (self.POSITIVE, self.DENSE, central, (11,12,13,14,15,1,2,11,1,2,3,4,2,3,4,0))
        nonzero = False
        for board in boards:
            packed = pack(board)
            for lib in self.libs:
                for direction in range(4):
                    self.assertEqual(unpack(lib.execute_move(direction, packed)), move(board, direction))
            for index, strength in ((3, 2000), (4, 4000)):
                lib = self.libs[index]
                mask = lib.debug_mask(packed)
                penalty = lib.debug_edges()[mask] - lib.debug_max_edges()[bin(mask).count("1")]
                self.assertLessEqual(penalty, 0)
                nonzero = nonzero or penalty < 0
                raw = self.libs[0].debug_eval(packed)
                self.assertAlmostEqual(lib.debug_eval(packed), raw+strength*penalty, delta=max(1, abs(raw)*1e-6))
        self.assertTrue(nonzero)

    def test_all_variant_eval_d4_and_goal_bypass(self):
        boards = [self.POSITIVE, self.DENSE, (0,0,0,2,1,1,3,5,0,3,5,3,0,0,1,11),
                  (11,12,13,14,15,1,2,11,1,2,3,4,2,3,4,0)]
        for lib in self.libs:
            for board in boards:
                scores = [lib.debug_eval(pack(transform(board, p))) for p in PERMUTATIONS]
                spread = max(scores) - min(scores)
                type(self).max_symmetry_error = max(self.max_symmetry_error, spread)
                self.assertLessEqual(spread, max(2, max(map(abs, scores))*2e-6))
            for board in ((11,11,11)+(0,)*13, (11,11,11,15)+(1,2,3,4)*3):
                for permutation in PERMUTATIONS:
                    self.assertEqual(lib.debug_eval(pack(transform(board, permutation))), 1e9)

    def test_every_new_variant_cache_equals_no_cache_reference(self):
        for arm, lib in zip(ARMS[1:], self.libs[1:]):
            for board in (self.POSITIVE, self.DENSE):
                for depth in (2, 3):
                    for threshold in (0.0, 1e-4):
                        for direction in range(4):
                            with self.subTest(arm=arm, depth=depth, threshold=threshold, direction=direction):
                                self.assertEqual(lib.score_levels_thresh(pack(board), direction, depth, threshold),
                                                 lib.score_levels_reference(pack(board), direction, depth, threshold))


if __name__ == "__main__":
    unittest.main()
