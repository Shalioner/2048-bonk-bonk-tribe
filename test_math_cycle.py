"""Синтетические проверки математического протокола; полные партии не запускаются."""
import contextlib
import copy
import importlib.util
import io
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bot

PATH = Path(__file__).resolve().parent / "experiments/math/run.py"
SPEC = importlib.util.spec_from_file_location("math_cycle_under_test", PATH)
cycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cycle)
REAL_WORK, REAL_PLAN = cycle.WORK, cycle.plan()
REAL_PLAY = cycle.play
PURE = (REAL_WORK / "vendor/engine_adapted.cpp").read_bytes()
GOAL, LOSS = [11] * 3 + [0] * 13, [1, 2, 1, 2, 2, 1, 2, 1] * 2


class MathCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.root = self.work / "experiments/math"
        self.root.mkdir(parents=True)
        self.p = copy.deepcopy(REAL_PLAN)
        self.p["screen"]["games"] = 2
        self.p["control"]["games"] = 6
        cycle.atomic(self.root / "plan.json", self.p)
        ranges = {s: [self.p[s]["seed_start"], self.p[s]["seed_start"] + self.p[s]["games"] - 1] for s in ("screen", "control")}
        cycle.atomic(self.root / "seed-inventory.json", dict(version=1, archive_commit=self.p["archive_commit"],
                     reserved_ranges=ranges, errors=[], overlap={s: [] for s in ranges}, seen_seeds=[]))
        for relative in ("bot.py", "benchmark.py", "spawn-model.default.json", "vendor/engine_adapted.cpp",
                         "vendor/2048.h", "vendor/config.h", "vendor/platdefs.h", "experiments/math/run.py"):
            target = self.work / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REAL_WORK / relative, target)
        for name, value in (("ROOT", self.root), ("WORK", self.work), ("__file__", str(self.root / "run.py")), ("pinned_source", lambda: PURE)):
            patcher = patch.object(cycle, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        engines = {}
        for arm in self.p["arms"]:
            binary = cycle.engine_path(arm["name"])
            binary.parent.mkdir(parents=True)
            binary.write_bytes(("mock native " + arm["name"]).encode())
            cpp = binary.with_name("engine.cpp")
            cpp.write_bytes(cycle.variant_source(PURE, arm))
            engines[arm["name"]] = dict(sha256=cycle.sha(binary), source_sha256=cycle.sha(cpp))
        cycle.atomic(self.root / "build/manifest.json", dict(version=1, sources=cycle.sources(), engines=engines))
        self.events = []
        patcher = patch.object(cycle, "play", self.game)
        patcher.start()
        self.addCleanup(patcher.stop)
        silence = contextlib.redirect_stdout(io.StringIO())
        silence.__enter__()
        self.addCleanup(silence.__exit__, None, None, None)

    def game(self, seed, label, arm):
        self.events.append((seed, label, arm))
        goal = arm == "empty_r1"
        board = GOAL if goal else LOSS
        return dict(seed=seed, arm=label, source_arm=arm, result="goal" if goal else "loss", moves=2, board=board,
                    caps=board.count(11), seconds=.03, search_wall_seconds=.02, search_calls=2,
                    depth_counts={"3": 2}, requested_depth_counts={"8": 2}, fallbacks=0, cuts=0, positions=42, cap_boards={})

    def screened(self):
        return cycle.checkpoint("screen", False)

    def test_real_protocol_and_single_constant_changes(self):
        self.assertEqual((REAL_PLAN["screen"]["games"], REAL_PLAN["control"]["games"]), (8, 60))
        self.assertEqual(REAL_PLAN["search"], dict(policy="progressive", depth=8, think=.01, max_moves=6000))
        self.assertEqual(cycle.variant_source(PURE, self.p["arms"][0]), PURE)
        for arm in self.p["arms"][1:]:
            changed = cycle.variant_source(PURE, arm).decode().splitlines()
            differences = [(a, b) for a, b in zip(PURE.decode().splitlines(), changed) if a != b]
            self.assertEqual(len(differences), 1)
            self.assertIn(arm["constant"], differences[0][1])
            self.assertNotIn("SCORE_SUM_WEIGHT", differences[0][1])
        self.assertEqual(len({a["constant"] for a in self.p["arms"][1:]}), 3)

    def test_build_validation_checks_actual_source_and_binary(self):
        cycle.build()  # existing compatible build is validation-only
        binary = cycle.engine_path("empty_r1")
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
        self.assertEqual(len(data["games"]), 14)
        self.assertEqual(len(self.events), 14)
        self.assertEqual([row["arm"] for row in data["games"][:7]], [a["name"] for a in self.p["arms"]])
        self.assertEqual(data["games"][7]["arm"], "empty_r1")
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
                               default_spawn_model=bot.default_spawn_model, spawn_table=bot.spawn_table)
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
        self.assertEqual(frozen["finalist"], "empty_r1")
        control = cycle.checkpoint("control", False)
        self.assertEqual(len(control["games"]), 12)
        self.assertEqual([g["arm"] for g in control["games"][:4]], ["baseline", "finalist", "finalist", "baseline"])
        self.assertEqual({g["seed"] for g in control["games"]}, set(range(1700000000, 1700000006)))
        result = cycle.finalize()
        self.assertTrue(result["confirmed_improvement"])
        self.assertEqual(result["stop_reason"], "bounded_neighborhood_exhausted")
        self.assertEqual(result["paired"]["one_sided_p"], 1 / 64)
        self.assertTrue(result["actual_cost_within_10_percent"])
        with self.assertRaisesRegex(ValueError, "Повторная"):
            cycle.freeze()

    def test_baseline_tie_priority_keeps_separate_control_labels(self):
        screen = self.screened()
        for row in screen["games"]:
            row.update(result="goal", board=GOAL, caps=3)
            if row["arm"] == "baseline":
                row["search_wall_seconds"] *= 2
        cycle.atomic(self.root / "screen.json", screen)
        self.assertEqual(cycle.freeze()["finalist"], "baseline")
        control = cycle.checkpoint("control", False)
        self.assertEqual(len(control["games"]), 12)
        self.assertEqual({g["source_arm"] for g in control["games"]}, {"baseline"})
        self.assertEqual({g["arm"] for g in control["games"]}, {"baseline", "finalist"})
        result = cycle.finalize()
        self.assertFalse(result["confirmed_improvement"])
        self.assertEqual(result["stop_reason"], "local_plateau")

    def test_last_round_growth_cannot_be_called_plateau(self):
        table = {a["name"]: dict(goals=1) for a in self.p["arms"]}
        self.assertEqual(cycle.round_stop(table)[0], "local_plateau")
        table["merges_r2"]["goals"] = 2
        reason, rounds = cycle.round_stop(table)
        self.assertEqual(reason, "bounded_neighborhood_exhausted")
        self.assertEqual([r["improved"] for r in rounds], [False, True])

    def test_freeze_rejects_changed_selection_policy_and_raw_screen(self):
        self.screened()
        frozen = cycle.freeze()
        path = self.root / "finalist.json"
        for mutation in (dict(finalist="merges_r2"), dict(policy={}), dict(screen={})):
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
        inventory["seen_seeds"] = [1600000000]
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
