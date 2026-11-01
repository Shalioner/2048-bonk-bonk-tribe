"""Mocked партии проверяют реальный протокол/хеши без игры и обучения."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("learning_cycle", Path(__file__).parent / "experiments/learning/cycle.py")
cycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cycle)


def episode(seed, arm, *args, sample=False, **kwargs):
    teacher = arm == "teacher"
    row = dict(seed=seed, arm=arm, depth=2 if teacher else 8, endgame_depth=3 if teacher else None,
               think=None if teacher else .01, result="goal", moves=64, caps=3, max=11,
               board=[11, 11, 11] + [0] * 13, seconds=.2, search_wall_seconds=.1, search_calls=64,
               depth_counts={"2" if teacher else "3": 64}, requested_depth_counts={"2" if teacher else "8": 64},
               fallbacks=0, cuts=0, positions=None if teacher else 100,
               cap_reached_at={"3": 64}, cap_boards={"3": [11, 11, 11] + [0] * 13}, sampled_positions=2 if sample else 0,
               records=[])
    if sample:
        row["records"] = [dict(seed=seed, board=[rank] + [0] * 15, move=move, stage=0,
                               label=1, episode_weight=.5) for rank, move in ((1, 32), (2, 64))]
    return row


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.real_plan = cycle.plan()
        (self.root / "plan.json").write_text(json.dumps(self.real_plan))
        self.addCleanup(patch.stopall)
        patch.object(cycle, "ROOT", self.root).start()
        self.native = self.root / "native.dylib"
        self.original = self.root / "original.dylib"
        self.native.write_bytes(b"mock native binary")
        self.original.write_bytes(b"mock original binary")
        patch.object(cycle.bot, "NATIVE", cycle.bot.NATIVE).start()
        self.configurations = []

        def configure(arm, model_path=None):
            self.configurations.append(arm)
            binary = self.original if arm == "baseline_legacy" else self.native
            cycle.bot.NATIVE = SimpleNamespace(_name=str(binary))

        patch.object(cycle, "configure_arm", side_effect=configure).start()
        self.redirect = contextlib.redirect_stdout(io.StringIO())
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def prepare_teacher(self, split):
        config = cycle.teacher_config(split)
        schedule = [(seed, "teacher") for seed in range(config["seeds"][0], config["seeds"][1] + 1)]
        table = cycle.bot.spawn_table(cycle.bot.default_spawn_model())
        path = self.root / ("teacher-" + split + ".json")
        data = cycle.checkpoint(path, "teacher", config, cycle.source_hashes(["teacher"]), table, table, schedule, False)
        for seed, arm in schedule:
            row = episode(seed, arm, sample=True)
            data["records"].extend(row.pop("records"))
            data["games"].append(row)
        cycle.save(path, data, schedule)
        return data

    def fake_value_module(self, brier=.2, prior=.3, passed=True):
        source = self.root / "mock_value_model.py"
        source.write_text("# Явный stub вместо запуска обучения\n")
        calls = []

        class Model:
            def __init__(self):
                self.metadata = {}

            def fit(self, records, **kwargs):
                calls.append(kwargs)
                return dict(validation_brier=brier, stage_prior_validation_brier=prior,
                            validation_gate_passed=passed, best_epoch=1)

            def save(self, path):
                Path(path).write_bytes(b"mock fitted model")

        module = SimpleNamespace(ValueModel=Model, __file__=str(source))
        return module, calls

    def freeze(self, candidate="progressive"):
        relative = "experiments/learning/cycle.py"
        data = dict(finalist=candidate, hashes={relative: cycle.sha(cycle.WORK / relative)})
        (self.root / "finalist.json").write_text(json.dumps(data))
        return data

    def test_real_screen_schedule_interrupt_resume_and_completed_skip(self):
        calls = []

        def interrupted(seed, arm, *args, **kwargs):
            calls.append((seed, arm))
            if len(calls) == 3:
                raise KeyboardInterrupt
            return episode(seed, arm, **kwargs)

        with patch.object(cycle, "play", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                cycle.evaluate("screen")
        saved = json.loads((self.root / "screen.json").read_text())
        self.assertEqual([(g["seed"], g["arm"]) for g in saved["games"]],
                         [(202612300, "baseline_legacy"), (202612300, "cache_legacy")])
        self.assertFalse(saved["complete"])
        self.assertEqual(saved["config"]["think"], .01)
        with patch.object(cycle, "play", side_effect=episode) as play:
            complete = cycle.evaluate("screen", resume=True)
        self.assertEqual(play.call_count, 34)
        self.assertEqual(len(complete["games"]), 36)
        self.assertTrue(complete["complete"])
        self.assertEqual([(g["seed"], g["arm"]) for g in complete["games"][3:6]],
                         [(202612301, "cache_legacy"), (202612301, "progressive"), (202612301, "baseline_legacy")])
        before = (self.root / "screen.json").read_bytes()
        with patch.object(cycle, "play", side_effect=AssertionError("Повтор завершённой партии")):
            cycle.evaluate("screen", resume=True)
        self.assertEqual((self.root / "screen.json").read_bytes(), before)

    def test_external_output_locks_training_before_first_episode(self):
        with tempfile.TemporaryDirectory() as outside:
            output = Path(outside) / "screen-custom.json"
            with patch.object(cycle, "play", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    cycle.evaluate("screen", out=output)
            self.assertTrue((self.root / "evaluation-started.json").exists())
            marker = json.loads((self.root / "evaluation-started.json").read_text())
            self.assertEqual(marker["stage"], "screen")
            self.assertEqual(marker["output"], str(output.resolve()))
            self.assertFalse((self.root / "screen.json").exists())

    def test_evaluation_output_cannot_replace_its_own_marker(self):
        marker = self.root / "evaluation-started.json"
        with patch.object(cycle, "play", side_effect=AssertionError("Не начинать партии")):
            with self.assertRaisesRegex(ValueError, "зарезервирован"):
                cycle.evaluate("screen", out=marker)
        self.assertFalse(marker.exists())

    def test_initial_fit_after_external_evaluation_is_refused_even_with_resume(self):
        self.prepare_teacher("train")
        self.prepare_teacher("validation")
        module, calls = self.fake_value_module()
        with tempfile.TemporaryDirectory() as outside, patch.object(cycle, "play", side_effect=episode):
            cycle.evaluate("screen", out=Path(outside) / "screen-custom.json")
        with patch.dict(sys.modules, {"value_model": module}):
            for resume in (False, True):
                with self.assertRaisesRegex(ValueError, "отбора"):
                    cycle.fit(resume=resume)
        self.assertEqual(calls, [])
        self.assertFalse((self.root / "model.bin").exists())

    def test_existing_fit_can_be_verified_after_evaluation_without_refitting(self):
        self.prepare_teacher("train")
        self.prepare_teacher("validation")
        module, calls = self.fake_value_module()
        with patch.dict(sys.modules, {"value_model": module}):
            original = cycle.fit()
            (self.root / "evaluation-started.json").write_text("{}")
            self.assertEqual(cycle.fit(resume=True), original)
        self.assertEqual(len(calls), 1)

    def test_fingerprints_refuse_engine_and_environment_changes_without_overwrite(self):
        with patch.object(cycle, "play", side_effect=episode):
            cycle.evaluate("screen")
        path = self.root / "screen.json"
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "перезапись"):
            cycle.evaluate("screen")
        self.native.write_bytes(b"different loaded binary")
        with self.assertRaisesRegex(ValueError, "несовместим"):
            cycle.evaluate("screen", resume=True)
        self.assertEqual(path.read_bytes(), before)
        self.native.write_bytes(b"mock native binary")
        environment = cycle.bot.spawn_table(cycle.bot.default_spawn_model())
        environment[0][0] += 1
        with self.assertRaisesRegex(ValueError, "несовместим"):
            cycle.evaluate("screen", resume=True, environment_table=environment)
        self.assertEqual(path.read_bytes(), before)

    def test_duplicate_or_reordered_games_cannot_be_resumed(self):
        with patch.object(cycle, "play", side_effect=episode):
            cycle.evaluate("screen")
        path = self.root / "screen.json"
        data = json.loads(path.read_text())
        data["games"][1] = data["games"][0]
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "порядок"):
            cycle.evaluate("screen", resume=True)
        self.assertEqual(path.read_bytes(), before)

    def test_teacher_fingerprint_does_not_depend_on_later_model_creation(self):
        with patch.object(cycle, "play", side_effect=episode):
            data = cycle.collect("train")
        self.assertEqual(len(data["games"]), 128)
        self.assertEqual(len(data["records"]), 256)
        self.assertNotIn("model", data["source_hashes"])
        self.assertEqual(data["records"][0]["episode_weight"], .5)
        (self.root / "model.bin").write_bytes(b"a model produced later")
        before = (self.root / "teacher-train.json").read_bytes()
        with patch.object(cycle, "play", side_effect=AssertionError("Teacher повторился")):
            cycle.collect("train", resume=True)
        self.assertEqual((self.root / "teacher-train.json").read_bytes(), before)

    def test_fit_reads_completed_datasets_and_uses_protocol_not_hidden_collection(self):
        self.prepare_teacher("train")
        self.prepare_teacher("validation")
        module, calls = self.fake_value_module()
        with patch.dict(sys.modules, {"value_model": module}), patch.object(cycle, "play", side_effect=AssertionError("fit не собирает партии")):
            metrics = cycle.fit()
            self.assertTrue(metrics["validation_gate_passed"])
            self.assertEqual(len(calls), 1)
            self.assertEqual({k: calls[0][k] for k in ("epochs", "learning_rate", "l2", "patience", "min_delta")},
                             dict(epochs=16, learning_rate=.25, l2=1e-4, patience=4, min_delta=.001))
            self.assertEqual(metrics["config"]["min_delta"], .001)
            self.assertEqual(len(calls[0]["validation_records"]), 64)
            cycle.fit(resume=True)
            self.assertEqual(len(calls), 1)
            with patch.object(cycle, "play", side_effect=episode):
                screen = cycle.evaluate("screen")
            self.assertEqual(screen["config"]["arms"], list(cycle.ARMS))
            self.assertEqual(len(screen["games"]), 48)
            self.assertEqual(screen["source_hashes"]["model"]["sha256"], cycle.sha(self.root / "model.bin"))
            before = (self.root / "screen.json").read_bytes()
            (self.root / "model.bin").write_bytes(b"different model")
            with self.assertRaisesRegex(ValueError, "Модель"):
                cycle.evaluate("screen", resume=True)
            self.assertEqual((self.root / "screen.json").read_bytes(), before)

    def test_brier_gate_requires_full_point_zero_zero_one_improvement(self):
        self.prepare_teacher("train")
        self.prepare_teacher("validation")
        module, _ = self.fake_value_module(brier=.2995, prior=.3, passed=True)
        with patch.dict(sys.modules, {"value_model": module}):
            metrics = cycle.fit()
        self.assertFalse(metrics["validation_gate_passed"])
        self.assertNotIn("value_progressive", cycle.evaluation_config("screen")["arms"])

    def test_partial_fit_dataset_cannot_implicitly_start_more_games(self):
        self.prepare_teacher("train")
        self.prepare_teacher("validation")
        path = self.root / "teacher-validation.json"
        data = json.loads(path.read_text())
        removed = data["games"].pop()["seed"]
        data["records"] = [row for row in data["records"] if row["seed"] != removed]
        data["complete"] = False
        path.write_text(json.dumps(data))
        module, calls = self.fake_value_module()
        with patch.dict(sys.modules, {"value_model": module}), patch.object(cycle, "play", side_effect=AssertionError("Неявный сбор")):
            with self.assertRaisesRegex(ValueError, "не завершены"):
                cycle.fit()
        self.assertEqual(calls, [])

    def test_teacher_records_require_every_sample_in_every_nonlimit_episode(self):
        original = self.prepare_teacher("train")
        first_seed = original["games"][0]["seed"]
        path = self.root / "teacher-train.json"
        for drop in (1, 2):
            with self.subTest(dropped_records=drop):
                data = json.loads(json.dumps(original))
                indexes = [i for i, row in enumerate(data["records"]) if row["seed"] == first_seed][:drop]
                data["records"] = [row for i, row in enumerate(data["records"]) if i not in indexes]
                path.write_text(json.dumps(data))
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, "полнота"):
                    cycle.load_teacher("train")
                self.assertEqual(path.read_bytes(), before)

    def test_holdout_uses_exact_frozen_candidate_and_alternates_pair_order(self):
        self.freeze("cache_legacy")
        with patch.object(cycle, "play", side_effect=episode):
            data = cycle.evaluate("holdout")
        self.assertEqual(len(data["games"]), 120)
        self.assertEqual([(g["seed"], g["arm"]) for g in data["games"][:4]],
                         [(202612400, "baseline_legacy"), (202612400, "cache_legacy"),
                          (202612401, "cache_legacy"), (202612401, "baseline_legacy")])
        self.freeze("progressive")
        before = (self.root / "holdout.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "несовместим"):
            cycle.evaluate("holdout", resume=True)
        self.assertEqual((self.root / "holdout.json").read_bytes(), before)

    def test_holdout_refuses_missing_or_incorrect_frozen_hashes(self):
        (self.root / "finalist.json").write_text(json.dumps(dict(finalist="progressive")))
        with self.assertRaisesRegex(ValueError, "hashes"):
            cycle.evaluate("holdout")
        frozen = self.freeze()
        frozen["hashes"]["experiments/learning/cycle.py"] = "0" * 64
        (self.root / "finalist.json").write_text(json.dumps(frozen))
        with self.assertRaisesRegex(ValueError, "заморозке"):
            cycle.evaluate("holdout")
        self.assertFalse((self.root / "holdout.json").exists())

    def test_limit_is_saved_as_limit_and_never_labeled_or_continued(self):
        def limited(seed, arm, *args, **kwargs):
            row = episode(seed, arm, **kwargs)
            row.update(result="limit", board=[1] + [0] * 15, caps=0, max=1, records=[])
            return row

        with patch.object(cycle, "play", side_effect=limited) as play:
            with self.assertRaisesRegex(ValueError, "Limit"):
                cycle.collect("train")
        self.assertEqual(play.call_count, 1)
        saved = json.loads((self.root / "teacher-train.json").read_text())
        self.assertEqual(saved["records"], [])
        self.assertEqual(saved["games"][0]["result"], "limit")

    def test_afterstate_sample_precedes_spawn_and_has_episode_label(self):
        solver = cycle.bot.spawn_table(cycle.bot.default_spawn_model())
        environment = [list(row) for row in solver]
        environment[11][0] += 1
        start = (11, 10, 10) + (0,) * 13
        after = cycle.bot.move(start, 0)
        final = (11, 11, 11) + (0,) * 13
        with patch.object(cycle.benchmark, "apply_table") as apply, \
             patch.object(cycle.benchmark, "spawn", side_effect=[start, start, final]) as spawn, \
             patch.object(cycle.benchmark, "choose_fixed", return_value=0):
            row = cycle.play(123, "teacher", solver, environment, max_moves=1, sample=True)
        self.assertEqual(row["result"], "goal")
        self.assertEqual(row["records"][0]["board"], list(after))
        self.assertEqual((row["records"][0]["label"], row["records"][0]["episode_weight"]), (1, 1))
        self.assertEqual(row["depth_counts"], {"2": 1})
        apply.assert_called_once_with(solver)
        self.assertTrue(all(call.args[-1] is environment for call in spawn.call_args_list))

    def test_baseline_snapshot_build_is_idempotent_and_refuses_changed_artifact(self):
        source = subprocess.check_output(["git", "show", self.real_plan["baseline_commit"] + ":vendor/engine_adapted.cpp"], cwd=cycle.WORK)

        def compile_fake(args, **kwargs):
            Path(args[-1]).write_bytes(b"mock compiled baseline")

        with patch.object(cycle.subprocess, "check_output", return_value=source), patch.object(cycle.subprocess, "run", side_effect=compile_fake) as compile:
            manifest = cycle.build_baseline()
            self.assertEqual(cycle.build_baseline(), manifest)
            self.assertEqual(compile.call_count, 1)
        cpp = self.root / "build/baseline/engine.cpp"
        self.assertEqual(cpp.read_bytes(), source)
        cpp.write_bytes(b"changed source")
        with patch.object(cycle.subprocess, "check_output", return_value=source):
            with self.assertRaisesRegex(ValueError, "несовместима"):
                cycle.build_baseline()


if __name__ == "__main__":
    unittest.main()
