"""Синтетические проверки математического протокола; полные партии не запускаются."""
import ast
import contextlib
import copy
import importlib.util
import io
import os
from pathlib import Path
import random
import shutil
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

# Load the real Python rule functions without the module's eager native load.
# This test suite owns only protocol mocks and must never build/load a variant.
BOT_PATH = Path(__file__).resolve().parent / "bot.py"
_tree = ast.parse(BOT_PATH.read_text(), filename=str(BOT_PATH))
_removed = 0
for _index, _node in enumerate(_tree.body):
    if (isinstance(_node, ast.Try) and _node.body and isinstance(_node.body[0], ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "NATIVE" for target in _node.body[0].targets)):
        _tree.body[_index] = ast.copy_location(ast.Assign(targets=[ast.Name(id="NATIVE", ctx=ast.Store())],
                                                        value=ast.Name(id="_TEST_NATIVE", ctx=ast.Load())), _node)
        _removed += 1
assert _removed == 1, "Native initialization seam changed; update the test fixture explicitly"
ast.fix_missing_locations(_tree)
bot = ModuleType("search_cycle_python_rules")
bot.__file__ = str(BOT_PATH)
bot._TEST_NATIVE = SimpleNamespace(set_spawn_table=lambda *args: 0)
exec(compile(_tree, str(BOT_PATH), "exec"), bot.__dict__)

PATH = Path(__file__).resolve().parent / "experiments/search_alternatives/run.py"
SPEC = importlib.util.spec_from_file_location("search_cycle_under_test", PATH)
cycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cycle)
REAL_WORK, REAL_PLAN = cycle.WORK, cycle.plan()
REAL_PLAY = cycle.play
PURE = cycle.pinned_source()
GOAL, LOSS = [11] * 3 + [0] * 13, [1, 2, 1, 2, 2, 1, 2, 1] * 2


class SearchCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.root = self.work / "experiments/search_alternatives"
        self.root.mkdir(parents=True)
        self.p = copy.deepcopy(REAL_PLAN)
        self.p["screen"]["games"] = 2
        self.p["control"]["games"] = 6
        cycle.atomic(self.root / "plan.json", self.p)
        ranges = {s: [self.p[s]["seed_start"], self.p[s]["seed_start"] + self.p[s]["games"] - 1] for s in ("screen", "control")}
        cycle.atomic(self.root / "seed-inventory.json", dict(version=1, archive_commit=self.p["archive_commit"],
                     reserved_ranges=ranges, errors=[], overlap={s: [] for s in ranges}, history=self.p["history"],
                     seen_seeds=sorted({seed for lo, hi in self.p["required_seen_ranges"].values() for seed in range(lo, hi + 1)})))
        for relative in ("bot.py", "benchmark.py", "spawn-model.default.json", "vendor/engine_adapted.cpp",
                         "vendor/2048.h", "vendor/config.h", "vendor/platdefs.h", "experiments/search_alternatives/run.py"):
            target = self.work / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REAL_WORK / relative, target)
        for name, value in (("ROOT", self.root), ("WORK", self.work), ("__file__", str(self.root / "run.py")), ("pinned_source", lambda: PURE)):
            patcher = patch.object(cycle, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.root / "variants.py").write_text("def variant_source(base, arm):\n    return base + ('\\n// ' + arm['kind']).encode()\n")
        (self.work / "vendor/search_variants.h").write_text("// synthetic feature header\n")
        engines = {}
        for arm in self.p["arms"]:
            binary = cycle.engine_path(arm["name"])
            binary.parent.mkdir(parents=True)
            binary.write_bytes(("mock native " + arm["name"]).encode())
            cpp = binary.with_name("engine.cpp")
            cpp.write_bytes(cycle.variant_source(PURE, arm))
            engines[arm["name"]] = dict(sha256=cycle.sha(binary), source_sha256=cycle.sha(cpp))
        cycle.atomic(self.root / "build/manifest.json", dict(version=1, sources=cycle.sources(), engines=engines))
        real_import = cycle.importlib.import_module
        import_patch = patch.object(cycle.importlib, "import_module", lambda name: bot if name == "bot" else real_import(name))
        import_patch.start()
        self.addCleanup(import_patch.stop)
        self.events = []
        patcher = patch.object(cycle, "play", self.game)
        patcher.start()
        self.addCleanup(patcher.stop)
        silence = contextlib.redirect_stdout(io.StringIO())
        silence.__enter__()
        self.addCleanup(silence.__exit__, None, None, None)

    def game(self, seed, label, arm):
        self.events.append((seed, label, arm))
        goal = arm == "canonical"
        board = GOAL if goal else LOSS
        return dict(seed=seed, arm=label, source_arm=arm, result="goal" if goal else "loss", moves=2, board=board,
                    caps=board.count(11), seconds=.03, search_wall_seconds=.02, wall_samples=[.01,.01], search_cpu_seconds=.03,
                    thresholds=cycle.arm_config(arm)["thresholds"], budget_profile=cycle.arm_config(arm)["budget_profile"],
                    budget_samples=[.01,.01], decision_caps=[0,0], credit_balance=0.0 if arm == "banked_endgame" else None,
                    threshold_counts={format(cycle.arm_config(arm)["thresholds"][0], ".0e"):2}, search_calls=2,
                    depth_counts={"3": 2}, requested_depth_counts={"8": 2}, fallbacks=0, cuts=0, positions=42, cap_boards={})

    def screened(self):
        return cycle.checkpoint("screen", False)

    def test_real_protocol_five_arms_thresholds_and_source_contract(self):
        self.assertEqual((REAL_PLAN["screen"]["games"], REAL_PLAN["control"]["games"]), (16, 100))
        self.assertEqual((REAL_PLAN["screen"]["seed_start"], REAL_PLAN["control"]["seed_start"]), (2400000000, 2500000000))
        self.assertGreater(REAL_PLAN["screen"]["seed_start"], 2**31)
        self.assertEqual(REAL_PLAN["search"], dict(policy="progressive", depth=8, think=.01, max_moves=6000))
        self.assertEqual([(a["round"], a["kind"]) for a in self.p["arms"]],
                         [(0,"baseline"), (1,"canonical"), (2,"serial_shallow"), (3,"precision"), (4,"banked_endgame")])
        for arm in self.p["arms"]:
            if arm["kind"] in ("baseline", "precision", "banked_endgame"):
                self.assertEqual(cycle.variant_source(PURE, arm), PURE)
            else:
                self.assertEqual(cycle.variant_source(PURE, arm), PURE + ("\n// " + arm["kind"]).encode())
            self.assertEqual(arm["thresholds"], [1e-5,1e-6] if arm["kind"] == "precision" else [1e-4,1e-5,1e-6])

    def test_full_control_schedule_is_100_fresh_pairs_fixed_before_data(self):
        with patch.object(cycle, "plan", return_value=REAL_PLAN):
            schedule = cycle.schedule("control", "banked_endgame")
            screen = cycle.schedule("screen")
        self.assertEqual(len(schedule), 200)
        self.assertEqual(len(screen), 80)
        self.assertEqual({seed for seed, _, _ in schedule}, set(range(2500000000,2500000100)))
        self.assertEqual(schedule[:4], [(2500000000,"baseline","baseline"),
                                        (2500000000,"finalist","banked_endgame"),
                                        (2500000001,"finalist","banked_endgame"),
                                        (2500000001,"baseline","baseline")])

    def test_feature_source_producer_reads_current_bytes_without_module_cache(self):
        arm = self.p["arms"][1]
        before = cycle.variant_source(PURE, arm)
        source = self.root / "variants.py"
        stamp = source.stat()
        changed = source.read_text().replace("// ", "## ")
        source.write_text(changed)
        os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertNotEqual(cycle.variant_source(PURE, arm), before)
        with self.assertRaisesRegex(ValueError, "sources несовместимы"):
            cycle.check_build()

    def test_relative_feature_fingerprints_and_header_changes_are_required(self):
        fingerprint = cycle.sources()
        self.assertIn("experiments/search_alternatives/variants.py", fingerprint)
        self.assertIn("vendor/search_variants.h", fingerprint)
        self.assertTrue(all(not Path(key).is_absolute() for key in fingerprint))
        (self.work / "vendor/search_variants.h").write_text("changed header")
        with self.assertRaisesRegex(ValueError, "sources несовместимы"):
            cycle.check_build()

    def test_build_refuses_source_change_during_mock_compilation(self):
        manifest = self.root / "build/manifest.json"
        manifest.unlink()
        cycle.atomic(self.root / "build/intent.json", cycle.sources())
        calls = []
        def compile_stub(command, check):
            calls.append(command)
            Path(command[-1]).write_bytes(b"synthetic compiled artifact")
            (self.work / "vendor/search_variants.h").write_text("changed during mock compile")
        with patch.object(cycle.subprocess, "check_output", return_value="mock compiler"), \
             patch.object(cycle.subprocess, "run", compile_stub):
            with self.assertRaisesRegex(ValueError, "во время сборки"):
                cycle.build()
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(command[command.index("-I") + 1] == str(self.work / "vendor") for command in calls))
        self.assertFalse(manifest.exists())

    def test_saved_inventory_explicitly_covers_history_and_new_ranges(self):
        inventory = cycle.read(REAL_WORK / "experiments/search_alternatives/seed-inventory.json")
        self.assertEqual(inventory["history"], REAL_PLAN["history"])
        self.assertEqual(inventory["errors"], [])
        self.assertEqual(inventory["overlap"], {"screen": [], "control": []})
        self.assertEqual(inventory["reserved_ranges"], {"screen": [2400000000, 2400000015],
                                                       "control": [2500000000, 2500000099]})
        seen = set(inventory["seen_seeds"])
        for lo, hi in REAL_PLAN["required_seen_ranges"].values():
            self.assertTrue(set(range(lo, hi + 1)).issubset(seen))
        for lo, hi in inventory["reserved_ranges"].values():
            self.assertFalse(seen.intersection(range(lo, hi + 1)))
        for item in inventory["history"]:
            self.assertTrue(any(Path(row["path"]).is_relative_to(Path(item["root"])) for row in inventory["files"]))

    def test_build_validation_checks_actual_source_and_binary(self):
        cycle.build()  # existing compatible build is validation-only
        binary = cycle.engine_path("canonical")
        binary.write_bytes(b"other native engine")
        with self.assertRaisesRegex(ValueError, "бинарник/исходник"):
            cycle.check_build()

    def test_relative_module_filename_has_identical_source_fingerprint(self):
        expected = cycle.sources()
        relative = os.path.relpath(self.root / "run.py", Path.cwd())
        with patch.object(cycle, "__file__", relative):
            self.assertEqual(cycle.sources(), expected)

    def test_serial_rotation_and_resume_exact_prefix(self):
        original = cycle.play
        def interrupt(seed, label, arm):
            if self.events:
                raise KeyboardInterrupt
            return original(seed, label, arm)
        with patch.object(cycle, "play", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.screened()
        self.assertEqual(len(cycle.read(self.root / "screen.json")["games"]), 1)
        data = cycle.checkpoint("screen", True)
        self.assertEqual(len(data["games"]), 10)
        self.assertEqual(len(self.events), 10)
        self.assertEqual([row["arm"] for row in data["games"][:5]], [a["name"] for a in self.p["arms"]])
        self.assertEqual(data["games"][5]["arm"], "canonical")
        with patch.object(cycle, "play", side_effect=AssertionError("completed game repeated")):
            self.assertEqual(cycle.checkpoint("screen", True), data)

    def test_synthetic_goal_uses_progressive_and_stops_before_spawn(self):
        spawns, calls = [], []
        def spawn(board, rng, table):
            spawns.append(board)
            return (11, 11, 10, 10) + (0,) * 12
        def choose(board, depth, think, info, policy):
            calls.append((depth, think, policy))
            info.update(depth=3, requested_depth=8, positions=16, fallback=False, cut=True)
            return 0
        fake = SimpleNamespace(CAP=11, reached_goal=bot.reached_goal, legal_moves=lambda board: [(0, tuple(GOAL))],
                               move=lambda board, direction: tuple(GOAL), choose=choose,
                               default_spawn_model=bot.default_spawn_model, spawn_table=bot.spawn_table, THRESHOLDS=(1e-4,1e-5,1e-6))
        benchmark = SimpleNamespace(spawn=spawn, apply_table=lambda table: None)
        original_import = cycle.importlib.import_module
        def imports(name):
            return benchmark if name == "benchmark" else original_import(name)
        # setUp mocks play; execute the original function explicitly for one synthetic move.
        with patch.object(cycle, "configure", return_value=fake), patch.object(cycle.importlib, "import_module", imports):
            result = REAL_PLAY(42, "baseline", "baseline")
        self.assertEqual(len(spawns), 2)
        self.assertEqual(calls, [(8, .01, "progressive")])
        self.assertEqual(result["board"], GOAL)
        self.assertEqual(result["depth_counts"], {"3": 1})
        self.assertEqual(result["cap_boards"], {})

    def test_each_game_explicitly_resets_thresholds_from_its_source_arm(self):
        marker = object()
        loaded = []
        def load(path):
            loaded.append(path)
            return marker
        with patch.object(bot, "load_engine", load), patch.object(bot, "THRESHOLDS", (9,)):
            for name in ("precision", "baseline", "canonical", "precision", "serial_shallow"):
                configured = cycle.configure(name)
                self.assertIs(configured, bot)
                self.assertIs(configured.NATIVE, marker)
                self.assertEqual(configured.THRESHOLDS, tuple(cycle.arm_config(name)["thresholds"]))
                self.assertEqual(loaded[-1], cycle.engine_path(name))

    def test_choose_samples_cpu_and_terminal_none_keep_all_observations(self):
        seed = 2400000005
        before = (1, 1) + (0,) * 14
        spawns, rng_draws, calls = [], [], []
        def spawn(board, rng, table):
            spawns.append(board)
            rng_draws.append(rng.random())
            return before if len(spawns) <= 2 else tuple(LOSS)
        def legal(board):
            return [] if tuple(board) == tuple(LOSS) else [(0, before)]
        def choose(board, depth, think, info, policy):
            calls.append(tuple(board))
            if len(calls) == 2:
                return None
            info.update(depth=3, requested_depth=8, positions=16, fallback=False, cut=True)
            return 0
        fake = SimpleNamespace(CAP=11, reached_goal=bot.reached_goal, legal_moves=legal,
                               move=lambda board, direction: before, choose=choose,
                               default_spawn_model=bot.default_spawn_model, spawn_table=bot.spawn_table,
                               THRESHOLDS=(1e-5, 1e-6))
        benchmark = SimpleNamespace(spawn=spawn, apply_table=lambda table: None)
        original_import = cycle.importlib.import_module
        def imports(name):
            return benchmark if name == "benchmark" else original_import(name)
        with patch.object(cycle, "configure", return_value=fake), patch.object(cycle.importlib, "import_module", imports), \
             patch.object(cycle.time, "monotonic", side_effect=[0, 1, 1.02, 2, 2.09, 3]), \
             patch.object(cycle.time, "process_time", side_effect=[10, 10.03, 11, 11.14]):
            row = REAL_PLAY(seed, "finalist", "precision")
        self.assertEqual(rng_draws[0], random.Random(seed).random())
        self.assertEqual((row["result"], row["moves"], row["search_calls"]), ("loss", 1, 2))
        self.assertEqual(row["thresholds"], [1e-5, 1e-6])
        self.assertEqual(row["wall_samples"], [1.02 - 1, 2.09 - 2])
        self.assertEqual(sum(row["wall_samples"]), row["search_wall_seconds"])
        self.assertAlmostEqual(row["search_cpu_seconds"], .17)
        self.assertGreater(row["wall_samples"][-1], .01 + .05) # reporting never censors outliers
        cycle.validate_games([row], [(seed, "finalist", "precision")])

    def test_latency_nearest_rank_and_flattened_samples_cpu_are_separate(self):
        measured = cycle.wall_statistics(list(range(1, 21)), 10)
        self.assertEqual(measured, dict(count=20, median_seconds=10.5, p95_nearest_rank_seconds=19,
                                        max_seconds=20, above_nominal_rate=.5, nominal_seconds=10))
        self.assertEqual(cycle.wall_statistics([], .01)["count"], 0)
        self.assertIsNone(cycle.wall_statistics([], .01)["p95_nearest_rank_seconds"])
        self.assertEqual(cycle.wall_statistics([.01, .02], .01)["above_nominal_rate"], .5)
        first = self.game(1, "baseline", "baseline")
        first.update(moves=1, search_calls=1, depth_counts={"3":1}, requested_depth_counts={"8":1}, threshold_counts={"1e-04":1},
                     budget_samples=[.01], decision_caps=[0],
                     wall_samples=[.001], search_wall_seconds=.001, search_cpu_seconds=.03)
        second = self.game(2, "baseline", "baseline")
        second.update(search_calls=3, budget_samples=[.01,.01,.01], decision_caps=[0,0,0], wall_samples=[.002, .03, .04], search_wall_seconds=sum([.002, .03, .04]),
                      search_cpu_seconds=.06)
        result = cycle.summarize([first, second])["baseline"]
        self.assertEqual(result["wall_latency"]["count"], 4)
        self.assertAlmostEqual(result["wall_latency"]["median_seconds"], .016)
        self.assertEqual(result["wall_latency"]["p95_nearest_rank_seconds"], .04)
        self.assertEqual(result["wall_latency"]["above_nominal_rate"], .5)
        self.assertAlmostEqual(result["search_cpu_seconds"], .09)
        self.assertAlmostEqual(result["choose_cpu_per_move"], .03)
        self.assertAlmostEqual(result["choose_wall_per_move"], .073 / 3)

    def test_threshold_samples_and_cpu_corruption_are_rejected_before_resume(self):
        expected = cycle.schedule("screen")
        row = self.game(*expected[0])
        changes = [dict(thresholds=[1e-5,1e-6]), dict(wall_samples=[.02]),
                   dict(wall_samples=[.01,.02]), dict(wall_samples=[-.01,.03]),
                   dict(wall_samples=[float("nan"),.01]), dict(wall_samples=[True,.01]),
                   dict(search_cpu_seconds=-1), dict(search_cpu_seconds=float("inf")), dict(search_cpu_seconds=True)]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                cycle.validate_games([dict(row, **change)], expected)
        # A large but finite wall observation stays visible, without adaptive exclusion.
        slow = dict(row, wall_samples=[.01,.2], search_wall_seconds=sum([.01,.2]))
        cycle.validate_games([slow], expected)

    def test_budget_helpers_phase_cap_available_credit_debt_and_reset(self):
        fixed, bank = (self.p["budget_profiles"][key] for key in ("fixed", "banked_endgame"))
        for caps in (0, 1, 2):
            self.assertEqual(cycle.allocate_budget(fixed, None, caps), (.01, None))
        for caps in (0, 1):
            budget, credit = cycle.allocate_budget(bank, .1, caps)
            self.assertEqual(budget, .01)
            self.assertAlmostEqual(credit, .11)
        self.assertEqual(cycle.allocate_budget(bank, .1, 2)[0], .04)
        self.assertAlmostEqual(cycle.allocate_budget(bank, .005, 2)[0], .015)
        self.assertEqual(cycle.allocate_budget(bank, 0.0, 2), (.01, .01)) # fresh game reset
        budget, credit = cycle.allocate_budget(bank, -.03, 2)
        self.assertEqual(budget, 0)
        self.assertAlmostEqual(credit, -.02)
        credit = cycle.charge_budget(credit, .001)
        self.assertAlmostEqual(credit, -.021)
        budget, credit = cycle.allocate_budget(bank, credit, 0)
        self.assertEqual(budget, 0)
        self.assertAlmostEqual(credit, -.011) # later allowance repays, rather than erases, debt
        self.assertIsNone(cycle.charge_budget(None, .2))
        with self.assertRaises(ValueError):
            cycle.allocate_budget(bank, 0, 3)
        with self.assertRaises(ValueError):
            cycle.charge_budget(0, float("nan"))

    def test_ledger_replay_validates_policy_even_when_native_source_is_baseline(self):
        row = self.game(2500000000, "finalist", "banked_endgame")
        board = [11,11] + LOSS[2:]
        caps, walls, budgets, credit = [0,2,2], [.001,.05,.02], [], 0.0
        for count, elapsed in zip(caps, walls):
            budget, credit = cycle.allocate_budget(self.p["budget_profiles"]["banked_endgame"], credit, count)
            budgets.append(budget)
            credit = cycle.charge_budget(credit, elapsed)
        row.update(board=board, caps=2, search_calls=3, decision_caps=caps, wall_samples=walls,
                   search_wall_seconds=sum(walls), budget_samples=budgets, credit_balance=credit)
        expected = [(row["seed"], "finalist", "banked_endgame")]
        cycle.validate_games([row], expected)
        self.assertAlmostEqual(budgets[1], .019)
        self.assertEqual(budgets[2], 0)
        self.assertAlmostEqual(credit, -.041)
        self.assertEqual(cycle.variant_source(PURE, cycle.arm_config("banked_endgame")), PURE)
        for change in (dict(budget_profile="fixed"), dict(credit_balance=0),
                       dict(budget_samples=[.01,.04,0]), dict(decision_caps=[0,2,1]),
                       dict(decision_caps=[0,2,3]), dict(budget_samples=budgets[:-1])):
            with self.subTest(change=change), self.assertRaises(ValueError):
                cycle.validate_games([dict(row, **change)], expected)

    def test_banked_play_allocates_late_time_and_charges_terminal_none(self):
        early = (1,1) + (0,)*14
        one = (11,1,1) + (0,)*13
        two = (11,11,1,1) + (0,)*12
        loss = tuple([11,11] + LOSS[2:])
        spawned = iter((early, early, early, one, one, two, loss))
        budgets = []
        def spawn(board, rng, table):
            return next(spawned)
        def legal(board):
            return [] if tuple(board) == loss else [(0, tuple(board))]
        def choose(board, depth, think, info, policy):
            budgets.append(think)
            if len(budgets) == 6:
                return None
            threshold = (None, 1e-4, 1e-5, 1e-6, 1e-5)[len(budgets)-1]
            info.update(depth=3, requested_depth=8, positions=16, fallback=threshold is None, threshold=threshold)
            return 0
        fake = SimpleNamespace(CAP=11, reached_goal=bot.reached_goal, legal_moves=legal,
                               move=lambda board, direction: board, choose=choose,
                               default_spawn_model=bot.default_spawn_model, spawn_table=bot.spawn_table,
                               THRESHOLDS=(1e-4, 1e-5, 1e-6))
        benchmark = SimpleNamespace(spawn=spawn, apply_table=lambda table: None)
        original_import = cycle.importlib.import_module
        def imports(name):
            return benchmark if name == "benchmark" else original_import(name)
        elapsed = [.001,.001,.001,.001,.05,.02]
        clocks = [0] + [value for step, wall in enumerate(elapsed,1) for value in (step, step+wall)] + [10]
        cpu = [value for step in range(6) for value in (10+step, 10+step+.001)]
        with patch.object(cycle, "configure", return_value=fake), patch.object(cycle.importlib, "import_module", imports), \
             patch.object(cycle.time, "monotonic", side_effect=clocks), patch.object(cycle.time, "process_time", side_effect=cpu):
            row = REAL_PLAY(2500000000, "finalist", "banked_endgame")
        self.assertEqual(row["decision_caps"], [0,0,1,1,2,2])
        self.assertEqual(budgets[:4], [.01]*4)
        self.assertEqual(budgets[4], .04)
        self.assertAlmostEqual(budgets[5], .006)
        self.assertAlmostEqual(row["credit_balance"], -.014) # None call is charged too
        self.assertEqual(row["threshold_counts"], {"fallback":1,"1e-04":1,"1e-05":2,"1e-06":1})
        self.assertEqual(sum(row["threshold_counts"].values()), row["moves"])
        self.assertEqual(row["search_calls"], row["moves"]+1)
        cycle.validate_games([row], [(2500000000,"finalist","banked_endgame")])

    def test_control_keeps_banked_policy_distinct_from_identical_native_source(self):
        def bank_wins(seed, label, arm):
            row = self.game(seed, label, arm)
            won = arm == "banked_endgame"
            return dict(row, result="goal" if won else "loss", board=GOAL if won else LOSS, caps=3 if won else 0)
        with patch.object(cycle, "play", bank_wins):
            self.screened()
            self.assertEqual(cycle.freeze()["finalist"], "banked_endgame")
            control = cycle.checkpoint("control", False)
        for row in control["games"]:
            self.assertEqual(row["budget_profile"], "fixed" if row["arm"] == "baseline" else "banked_endgame")
        self.assertEqual(cycle.engine_path("baseline").with_name("engine.cpp").read_bytes(),
                         cycle.engine_path("banked_endgame").with_name("engine.cpp").read_bytes())
        summary = cycle.finalize()
        self.assertTrue(summary["confirmed_improvement"])
        self.assertEqual(summary["arms"]["finalist"]["maximum_profile_call_cap_seconds"], .04)
        self.assertEqual(set(summary["arms"]["finalist"]["budget_profiles"]), {"banked_endgame"})

    def test_completed_threshold_buckets_and_atomic_goal_with_four_caps(self):
        row = self.game(2400000000, "precision", "precision")
        cycle.validate_games([row], [(row["seed"], "precision", "precision")])
        for buckets in ({"1e-04":2}, {"1e-05":1}, {"1e-05":-1,"fallback":3}, {"0.00001":2}):
            with self.subTest(buckets=buckets), self.assertRaises(ValueError):
                cycle.validate_games([dict(row, threshold_counts=buckets)], [(row["seed"], "precision", "precision")])
        cycle.validate_games([dict(row, threshold_counts={"fallback":2})], [(row["seed"], "precision", "precision")])
        before = (11,11,10,10,10,10,0,0)+(0,)*8
        after = list(bot.move(before, 0))
        self.assertEqual(after.count(11), 4)
        self.assertTrue(bot.reached_goal(after))
        goal = self.game(2400000000, "baseline", "baseline")
        goal.update(result="goal", board=after, caps=4, moves=1, search_calls=1, depth_counts={"3":1},
                    requested_depth_counts={"8":1}, threshold_counts={"fallback":1}, wall_samples=[.01],
                    search_wall_seconds=.01, budget_samples=[.01], decision_caps=[2])
        cycle.validate_games([goal], [(goal["seed"], "baseline", "baseline")])

    def test_malformed_telemetry_and_terminal_cap_snapshot_rejected(self):
        expected = cycle.schedule("screen")
        seed, label, arm = expected[0]
        original = self.game(seed, label, arm)
        for mutation in (dict(positions=-1), dict(search_calls=0), dict(cap_boards={"2": GOAL})):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                cycle.validate_games([dict(original, **mutation)], expected)

    def test_changed_config_source_or_order_refuses_resume(self):
        self.screened()
        path = self.root / "screen.json"
        original = path.read_bytes()
        data = cycle.read(path)
        data["games"][0]["seed"] += 1
        cycle.atomic(path, data)
        with self.assertRaisesRegex(ValueError, "seed/arm"):
            cycle.checkpoint("screen", True)
        path.write_bytes(original)
        plan_path = self.root / "plan.json"
        plan_bytes = plan_path.read_bytes()
        changed = copy.deepcopy(self.p)
        changed["search"]["think"] = .02
        cycle.atomic(plan_path, changed)
        with self.assertRaisesRegex(ValueError, "sources несовместимы"):
            cycle.checkpoint("screen", True)
        plan_path.write_bytes(plan_bytes)
        (self.work / "bot.py").write_text("changed source")
        with self.assertRaisesRegex(ValueError, "sources несовместимы"):
            cycle.checkpoint("screen", True)
        self.assertEqual(path.read_bytes(), original)

    def test_limit_is_saved_and_halts_without_loss_or_continuation(self):
        def limited(seed, label, arm):
            row = self.game(seed, label, arm)
            return dict(row, result="limit", board=[1] + [0] * 15, caps=0)
        with patch.object(cycle, "play", limited):
            with self.assertRaisesRegex(ValueError, "Limit сохранён"):
                self.screened()
        data = cycle.read(self.root / "screen.json")
        self.assertEqual(len(data["games"]), 1)
        self.assertEqual(data["games"][0]["result"], "limit")
        with patch.object(cycle, "play", side_effect=AssertionError("must not continue")):
            with self.assertRaisesRegex(ValueError, "limit"):
                cycle.checkpoint("screen", True)

    def test_full_pipeline_fresh_control_and_exact_test(self):
        self.screened()
        frozen = cycle.freeze()
        self.assertEqual(frozen["finalist"], "canonical")
        control = cycle.checkpoint("control", False)
        self.assertEqual(len(control["games"]), 12)
        self.assertEqual([g["arm"] for g in control["games"][:4]], ["baseline", "finalist", "finalist", "baseline"])
        self.assertEqual({g["seed"] for g in control["games"]}, set(range(2500000000, 2500000006)))
        result = cycle.finalize()
        self.assertTrue(result["confirmed_improvement"])
        self.assertEqual(result["stop_reason"], "local_plateau")
        self.assertEqual(result["paired"]["one_sided_p"], 1 / 64)
        self.assertTrue(result["actual_cost_within_10_percent"])
        with self.assertRaisesRegex(ValueError, "Повторная"):
            cycle.freeze()

    def test_baseline_tie_priority_keeps_separate_control_labels(self):
        screen = self.screened()
        for row in screen["games"]:
            row.update(result="goal", board=GOAL, caps=3)
            if row["arm"] == "baseline":
                row["wall_samples"] = [sample * 2 for sample in row["wall_samples"]]
                row["search_wall_seconds"] = sum(row["wall_samples"])
        cycle.atomic(self.root / "screen.json", screen)
        self.assertEqual(cycle.freeze()["finalist"], "baseline")
        control = cycle.checkpoint("control", False)
        self.assertEqual(len(control["games"]), 12)
        self.assertEqual({g["source_arm"] for g in control["games"]}, {"baseline"})
        self.assertEqual({g["arm"] for g in control["games"]}, {"baseline", "finalist"})
        result = cycle.finalize()
        self.assertFalse(result["confirmed_improvement"])
        self.assertEqual(result["stop_reason"], "local_plateau")

    def test_generic_four_round_stop_requires_two_final_stalls(self):
        table = {a["name"]: dict(goals=1) for a in self.p["arms"]}
        self.assertEqual(cycle.round_stop(table)[0], "local_plateau")
        table["canonical"]["goals"] = 2
        reason, rounds = cycle.round_stop(table)
        self.assertEqual(reason, "local_plateau")
        self.assertEqual([r["improved"] for r in rounds], [True, False, False, False])
        table["serial_shallow"]["goals"] = 3
        self.assertEqual(cycle.round_stop(table)[0], "local_plateau")
        table["precision"]["goals"] = 4
        self.assertEqual(cycle.round_stop(table)[0], "bounded_neighborhood_exhausted")
        table["banked_endgame"]["goals"] = 5
        reason, rounds = cycle.round_stop(table)
        self.assertEqual(reason, "bounded_neighborhood_exhausted")
        self.assertEqual([r["improved"] for r in rounds], [True, True, True, True])

    def test_freeze_rejects_changed_selection_policy_and_raw_screen(self):
        self.screened()
        frozen = cycle.freeze()
        path = self.root / "finalist.json"
        for mutation in (dict(finalist="precision"), dict(policy={}), dict(screen={})):
            with self.subTest(mutation=mutation):
                cycle.atomic(path, dict(frozen, **mutation))
                with patch.object(cycle, "play", side_effect=AssertionError("malformed freeze must not play")):
                    with self.assertRaises(ValueError):
                        cycle.checkpoint("control", False)
        cycle.atomic(path, frozen)
        (self.root / "screen.json").write_text("edited raw screen")
        with self.assertRaisesRegex(ValueError, "Замороженный файл"):
            cycle.verify_freeze()

    def test_freeze_requires_exact_hash_key_set(self):
        self.screened()
        frozen = cycle.freeze()
        extra = self.work / "unrelated.txt"
        extra.write_text("outside the required freeze set")
        cases = {"trimmed": {"benchmark.py": frozen["hashes"]["benchmark.py"]},
                 "extra": dict(frozen["hashes"], **{"unrelated.txt": cycle.sha(extra)})}
        for name, hashes in cases.items():
            with self.subTest(case=name):
                cycle.atomic(self.root / "finalist.json", dict(frozen, hashes=hashes))
                with self.assertRaisesRegex(ValueError, "набор.*freeze"):
                    cycle.verify_freeze()

    def test_raw_telemetry_cannot_hide_behind_trimmed_freeze(self):
        screen = self.screened()
        frozen = cycle.freeze()
        screen["games"][0]["positions"] += 1  # итоговый summary/selection не меняется
        cycle.atomic(self.root / "screen.json", screen)
        with self.assertRaisesRegex(ValueError, "Замороженный файл"):
            cycle.verify_freeze()  # полный набор SHA уже должен ловить raw mutation
        trimmed = {"benchmark.py": frozen["hashes"]["benchmark.py"]}
        cycle.atomic(self.root / "finalist.json", dict(frozen, hashes=trimmed))
        with patch.object(cycle, "play", side_effect=AssertionError("trimmed freeze must not play")) as played:
            with self.assertRaisesRegex(ValueError, "набор.*freeze"):
                cycle.checkpoint("control", False)
            played.assert_not_called()
        self.assertFalse((self.root / "control.json").exists())

    def test_freeze_and_finalize_never_continue_incomplete_stage(self):
        data = self.screened()
        path = self.root / "screen.json"
        cycle.atomic(path, dict(data, games=data["games"][:-1], complete=False))
        with patch.object(cycle, "play", side_effect=AssertionError("freeze must not play")):
            with self.assertRaisesRegex(ValueError, "неявное продолжение"):
                cycle.freeze()
        cycle.atomic(path, data)
        cycle.freeze()
        with patch.object(cycle, "play", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                cycle.checkpoint("control", False)
        with patch.object(cycle, "play", side_effect=AssertionError("finalize must not play")):
            with self.assertRaisesRegex(ValueError, "неявное продолжение"):
                cycle.finalize()

    def test_seed_extraction_prior_ranges_and_hidden_overlap(self):
        seen = cycle.seed_values(dict(seed=3, nested=[dict(seed_start=5, games=2)], seen_seeds=[8], new_ranges={"old": [9, 11]}))
        self.assertEqual(seen, {3, 5, 6, 8, 9, 10, 11})
        inventory = cycle.read(self.root / "seed-inventory.json")
        inventory["seen_seeds"] = [2400000000]
        cycle.atomic(self.root / "seed-inventory.json", inventory)
        with self.assertRaisesRegex(ValueError, "seed audit"):
            cycle.sources()

    def test_paired_duplicate_or_missing_seed_and_negative_outcomes(self):
        rows = [dict(seed=s, arm=a, result="loss") for s in (1, 2) for a in ("baseline", "finalist")]
        self.assertFalse(cycle.paired(rows)["improved"])
        self.assertEqual(cycle.paired(rows)["one_sided_p"], 1)
        for malformed in (rows[:-1], rows + rows[:1]):
            with self.assertRaisesRegex(ValueError, "пары"):
                cycle.paired(malformed)


if __name__ == "__main__":
    unittest.main()
