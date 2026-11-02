"""Короткие mocked проверки протокола, без native-сборок/обучения/партий."""
from array import array
import copy
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parent / "experiments/td/run.py"
SPEC = importlib.util.spec_from_file_location("td_cycle_under_test", PATH)
cycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cycle)
REAL_PLAN = cycle.plan()
GOAL = [11, 11, 11] + [0] * 13
LOSS = [1, 2, 1, 2, 2, 1, 2, 1, 1, 2, 1, 2, 2, 1, 2, 1]


class FakeModel:
    def __init__(self, initial_value=.5):
        self.bias = array("f", [initial_value] * 3)
        self.weights = array("f", [0])
        self.metadata, self.training = {}, {}

    def save(self, path, metadata=None):
        extra = dict(self.metadata, **(metadata or {}))
        cycle.atomic(path, dict(bias=list(self.bias), weights=list(self.weights), metadata=extra, training=self.training))

    @classmethod
    def load(cls, path):
        data = cycle.read(path)
        model = cls()
        model.bias, model.weights = array("f", data["bias"]), array("f", data["weights"])
        model.metadata, model.training = data["metadata"], data["training"]
        return model


class FakeTrainer:
    events = []
    observer_mutates = False
    limit_seed = None
    diverge = False
    crash_seed = None

    def __init__(self, path):
        self.value = FakeModel()

    def reset(self, initial_value):
        self.value = FakeModel(initial_value)

    def set_spawn_table(self, table):
        self.table = table

    def set_model(self, model):
        self.value = copy.deepcopy(model)

    def model(self):
        return copy.deepcopy(self.value)

    def episode(self, seed, **kwargs):
        algorithm = kwargs["algorithm"]
        self.events.append(dict(seed=seed, **kwargs))
        if seed == self.crash_seed and algorithm != "observe":
            FakeTrainer.crash_seed = None
            raise KeyboardInterrupt("injected before episode")
        if algorithm != "observe" or self.observer_mutates:
            self.value.weights[0] += 1 if algorithm == "mc" else 2
        snapshots = {"1": [11, 1, 1 + seed % 8] + [0] * 13,
                     "2": [11, 11, 1 + seed % 8] + [0] * 13}
        limited = seed == self.limit_seed
        board = [1] + [0] * 15 if limited else GOAL
        return dict(seed=seed, result="limit" if limited else "goal", board=board, caps=board.count(11),
                    moves=4, first_caps=snapshots, updates=0 if algorithm == "observe" else 4,
                    trajectory_digest=str(seed) + ("-different" if self.diverge and algorithm == "td0" else ""))


class FakeHelpers:
    @staticmethod
    def validate_games(games, schedule, config):
        if len(games) > len(schedule):
            raise ValueError("long prefix")
        for row, expected in zip(games, schedule):
            if (row["seed"], row["arm"]) != expected or row["result"] not in ("goal", "loss", "limit"):
                raise ValueError("invalid game")


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.p = copy.deepcopy(REAL_PLAN)
        self.p["pool"].update(games=4, capacity_per_stage=2)
        self.p["training"].update(warmup_episodes=2, milestones=[2, 4, 6, 8], checkpoint_every=2)
        self.p["validation"]["games_per_stage"] = 2
        self.p["screen"]["games"] = 3
        self.p["holdout"]["games"] = 6
        cycle.atomic(self.root / "plan.json", self.p)
        self.producer = self.root / "producer.py"
        self.producer.write_text("# fixed mocked producer\n")
        self.engine = self.root / "engine.bin"
        self.engine.write_bytes(b"fixed engine")
        self.old = self.root / "old-mc.bin"
        self.old.write_bytes(b"fixed historic MC")
        FakeTrainer.events = []
        FakeTrainer.observer_mutates = FakeTrainer.diverge = False
        FakeTrainer.limit_seed = FakeTrainer.crash_seed = None
        self.builds = []
        def build_fake(path):
            self.builds.append(str(path))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixed mocked trainer")
            return path
        self.backend = SimpleNamespace(TDModel=FakeModel, NativeTrainer=FakeTrainer, build_trainer=build_fake)
        for target, value in (("ROOT", self.root), ("backend", lambda: self.backend),
                              ("producer_paths", lambda: [PATH, self.root / "plan.json", self.producer]),
                              ("learning_helpers", lambda: FakeHelpers)):
            patcher = patch.object(cycle, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        silence = patch("builtins.print")
        silence.start()
        self.addCleanup(silence.stop)
        cycle.build()

    def pool(self):
        return cycle.pool()

    def trained(self):
        self.pool()
        return cycle.train("matched"), cycle.train("selfplay")

    def eval_patches(self):
        original = cycle.selected_models
        def models():
            result = original()
            result["old_mc"] = self.old
            return result
        def sources(models):
            return dict(producers=cycle.source_fingerprint(), engine=dict(path=str(self.engine), sha256=cycle.sha(self.engine)),
                        models={arm: dict(path=str(path), sha256=cycle.sha(path)) for arm, path in models.items()})
        def play(seed, arm, models, config):
            goal = arm.startswith("td_")
            board = GOAL if goal else LOSS
            return dict(seed=seed, arm=arm, result="goal" if goal else "loss", board=board, caps=board.count(11),
                        moves=4, seconds=.08, search_wall_seconds=.04, depth_counts={"8": 4},
                        requested_depth_counts={"8": 4}, fallbacks=0, cuts=0, positions=8, max=max(board))
        for name, function in (("selected_models", models), ("evaluation_sources", sources), ("timed_play", play)):
            patcher = patch.object(cycle, name, function)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_real_protocol_ranges_and_lazy_help(self):
        self.assertEqual(REAL_PLAN["holdout"]["games"], 60)
        self.assertEqual(REAL_PLAN["screen"]["games"], 24)
        with patch.object(cycle, "plan", lambda: REAL_PLAN):
            data = {"starts": {"1": [{"board": [11, 1] + [0] * 14}], "2": [{"board": [11, 11, 1] + [0] * 13}]}}
            schedule = cycle.validation_schedule(data)
        self.assertEqual(len(schedule), 192)
        self.assertEqual(len({seed for _, seed, _ in schedule}), 192)
        with self.assertRaises(SystemExit) as result:
            cycle.main(["--help"])
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(self.builds, [str(cycle.trainer_path())])

    def test_build_no_implicit_rebuild_and_source_fingerprint(self):
        cycle.evaluation_marker("screen", self.root / "elsewhere.json")
        cycle.build()  # strict finished-checkpoint validation remains legal
        self.assertEqual(len(self.builds), 1)
        self.engine.write_bytes(b"engine change is eval-specific")
        self.producer.write_text("changed producer")
        with self.assertRaisesRegex(ValueError, "несовместима"):
            cycle.build()
        self.assertEqual(len(self.builds), 1)

    def test_pool_after_spawn_unique_and_no_implicit_collection(self):
        data = self.pool()
        self.assertEqual(len(data["starts"]["1"]), 2)
        self.assertTrue(all(cycle.valid_start(row["board"], 2) for row in data["starts"]["2"]))
        data["games"].pop()
        data["complete"] = False
        data["starts"] = cycle.extract_pool(data["games"])
        cycle.atomic(self.root / "pool.json", data)
        count = len(FakeTrainer.events)
        with self.assertRaisesRegex(ValueError, "неявное"):
            cycle.load_pool()
        self.assertEqual(len(FakeTrainer.events), count)

    def test_matched_joint_earliest_stop_and_curriculum(self):
        self.pool()
        state = cycle.train("matched")
        self.assertEqual(state["stop_reason"], "plateau")
        self.assertEqual([e["episodes"] for e in state["checks"]], [0, 2, 4, 6])
        self.assertEqual(state["selected"]["episodes"], 0)
        self.assertEqual(state["checkpoint_episodes"], 6)
        records = cycle.blocks(cycle.group_directory(self.root / "train-matched.json"), state["run_id"])
        self.assertEqual(len(records), 6)
        self.assertEqual(records[4]["start_board"].count(11), 1)
        self.assertEqual(records[5]["start_board"].count(11), 2)
        for row in records:
            self.assertEqual(row["rows"]["mc_teacher"]["trajectory_digest"], row["rows"]["td_teacher"]["trajectory_digest"])
        trained = [e for e in FakeTrainer.events if e["algorithm"] != "observe"]
        self.assertTrue(all(e["behavior"] == "teacher" and e["epsilon"] == 0 for e in trained))
        self.assertEqual(len(trained), 12)
        count = len(FakeTrainer.events)
        cycle.evaluation_marker("screen", self.root / "custom.json")
        cycle.train("matched", resume=True)
        self.assertEqual(len(FakeTrainer.events), count)

    def test_replay_tail_without_double_updates(self):
        self.pool()
        FakeTrainer.crash_seed = self.p["training"]["seed_start"] + 1
        with self.assertRaises(KeyboardInterrupt):
            cycle.train("matched")
        state = cycle.train("matched", resume=True)
        models = cycle.current_models(state)
        self.assertEqual(models["mc_teacher"].weights[0], 6)
        self.assertEqual(models["td_teacher"].weights[0], 12)

    def test_resume_pending_validation_before_more_training(self):
        self.pool()
        original = cycle.validate_model
        failed = []
        def interrupt(path, data, out):
            if Path(out).parent.name == "2" and not failed:
                failed.append(True)
                raise KeyboardInterrupt("during milestone validation")
            return original(path, data, out)
        with patch.object(cycle, "validate_model", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                cycle.train("matched")
        pending = cycle.read(self.root / "train-matched.json")
        self.assertEqual(pending["pending_validation"], 2)
        self.assertEqual(len(pending["checks"]), 1)
        state = cycle.train("matched", resume=True)
        self.assertEqual([e["episodes"] for e in state["checks"]], [0, 2, 4, 6])
        self.assertEqual(cycle.current_models(state)["mc_teacher"].weights[0], 6)

    def test_limit_retained_rollback_and_matching_failure(self):
        self.pool()
        FakeTrainer.limit_seed = self.p["training"]["seed_start"]
        with self.assertRaisesRegex(ValueError, "limit"):
            cycle.train("matched")
        state = cycle.read(self.root / "train-matched.json")
        rows = cycle.blocks(cycle.group_directory(self.root / "train-matched.json"), state["run_id"])
        self.assertEqual(rows[0]["invalid"], "limit")
        self.assertEqual(rows[0]["rows"]["mc_teacher"]["result"], "limit")
        self.assertEqual(cycle.current_models(state)["mc_teacher"].weights[0], 0)
        with self.assertRaisesRegex(ValueError, "сохранённый limit"):
            cycle.train("matched", resume=True)

    def test_trajectory_mismatch_retained_and_never_selected(self):
        self.pool()
        FakeTrainer.diverge = True
        with self.assertRaisesRegex(ValueError, "trajectory mismatch"):
            cycle.train("matched")
        state = cycle.read(self.root / "train-matched.json")
        records = cycle.blocks(cycle.group_directory(self.root / "train-matched.json"), state["run_id"])
        self.assertEqual(records[0]["invalid"], "trajectory mismatch")
        self.assertEqual(len(records[0]["rows"]), 2)
        self.assertEqual(cycle.current_models(state)["td_teacher"].weights[0], 0)

    def test_selfplay_warmup_then_exploration_and_independent_stop(self):
        self.pool()
        state = cycle.train("selfplay")
        events = [e for e in FakeTrainer.events if e["algorithm"] == "td0"]
        self.assertEqual([(e["behavior"], e["epsilon"]) for e in events[:2]], [("teacher", 0)] * 2)
        self.assertTrue(all(e["behavior"] == "selfplay" and e["epsilon"] == .05 for e in events[2:]))
        self.assertEqual(state["checkpoint_episodes"], 6)

    def test_observe_and_terminal_snapshot_fail_closed(self):
        FakeTrainer.observer_mutates = True
        with self.assertRaisesRegex(ValueError, "Observe изменил"):
            self.pool()
        FakeTrainer.observer_mutates = False
        row = FakeTrainer(None).episode(1, algorithm="observe")
        row.update(seconds=.1, first_caps={"2": GOAL})
        with self.assertRaisesRegex(ValueError, "first_caps"):
            cycle.check_episode(row, 1)

    def test_custom_eval_barrier_and_partial_freeze_never_plays(self):
        self.trained()
        self.eval_patches()
        out = self.root / "custom-screen.json"
        with patch.object(cycle, "timed_play", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                cycle.evaluate("screen", out=out)
        self.assertTrue((self.root / "evaluation-started.json").exists())
        count = len(FakeTrainer.events)
        with self.assertRaisesRegex(ValueError, "После начала"):
            cycle.train("matched", out=self.root / "brand-new-training.json")
        self.assertEqual(len(FakeTrainer.events), count)
        cycle.atomic(self.root / "screen.json", cycle.read(out))
        with patch.object(cycle, "timed_play", side_effect=AssertionError("must never play")):
            with self.assertRaisesRegex(ValueError, "неявное"):
                cycle.freeze()

    def test_reserved_marker_output_rejected_before_stage_side_effects(self):
        self.trained()
        self.eval_patches()
        marker = self.root / "evaluation-started.json"
        alias = self.root / "unused" / ".." / marker.name
        actions = (("pool", lambda: cycle.pool(out=alias)),
                   ("train", lambda: cycle.train("matched", out=alias)),
                   ("screen", lambda: cycle.evaluate("screen", out=alias)),
                   ("holdout", lambda: cycle.evaluate("holdout", out=alias)),
                   ("freeze", lambda: cycle.freeze(out=alias)))
        marker_bytes = b'{"sources":{"read_only":"preserve exactly"}}\n'
        protected = {path: path.read_bytes() for path in (self.producer, self.engine, self.old,
                     self.root / "plan.json", self.root / "pool.json", self.root / "train-matched.json")}
        for exists in (False, True):
            for stage, action in actions:
                with self.subTest(stage=stage, existing_marker=exists):
                    if exists:
                        marker.write_bytes(marker_bytes)
                    elif marker.exists():
                        marker.unlink()
                    with patch.object(cycle, "new_trainer") as native, patch.object(cycle, "timed_play") as timed:
                        native.side_effect = RuntimeError("no native episode permitted")
                        timed.side_effect = RuntimeError("no timed game permitted")
                        try:
                            with self.assertRaisesRegex(ValueError, "зарезервирован"):
                                action()
                        finally:
                            self.assertEqual(marker.exists(), exists)
                            if exists:
                                self.assertEqual(marker.read_bytes(), marker_bytes)
                            self.assertFalse(native.called)
                            self.assertFalse(timed.called)
                            self.assertTrue(all(path.read_bytes() == content for path, content in protected.items()))

    def test_timed_goal_does_not_spawn_and_telemetry_is_actual(self):
        spawns, chosen = [], []
        start = tuple([11, 11, 10, 10] + [0] * 12)
        def spawn(board, rng, table):
            spawns.append(board)
            return start
        def choose(board, depth, think, info, policy):
            chosen.append(policy)
            info.update(depth=3, requested_depth=8, positions=12, cut=True, fallback=False)
            return 0
        bot = SimpleNamespace(CAP=11, reached_goal=lambda board: board.count(11) >= 3,
                              choose=choose, legal_moves=lambda board: [(0, tuple(GOAL))], move=lambda board, direction: tuple(GOAL))
        bench = SimpleNamespace(spawn=spawn, apply_table=lambda table: None)
        original_import = cycle.importlib.import_module
        def imports(name):
            return bench if name == "benchmark" else original_import(name)
        with patch.object(cycle, "configure_arm", return_value=bot), patch.object(cycle.importlib, "import_module", imports):
            row = cycle.timed_play(12, "td_teacher", {}, dict(depth=8, think=.01, max_moves=6000))
        self.assertEqual(len(spawns), 2)  # только две начальные плитки
        self.assertEqual(chosen, ["progressive"])
        self.assertEqual(row["board"], GOAL)
        self.assertEqual(row["depth_counts"], {"3": 1})
        self.assertEqual(row["requested_depth_counts"], {"8": 1})
        self.assertEqual(row["cap_boards"], {})
        self.assertEqual((row["cuts"], row["positions"]), (1, 12))

    def test_finalizer_does_not_continue_partial_control(self):
        self.trained()
        self.eval_patches()
        cycle.evaluate("screen")
        cycle.freeze()
        original = cycle.timed_play
        calls = []
        def interrupt(seed, arm, models, config):
            if calls:
                raise KeyboardInterrupt
            calls.append(True)
            return original(seed, arm, models, config)
        with patch.object(cycle, "timed_play", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                cycle.evaluate("holdout")
        with patch.object(cycle, "timed_play", side_effect=AssertionError("finalize must never play")):
            with self.assertRaisesRegex(ValueError, "неявное"):
                cycle.finalize()

    def test_eval_resume_checks_actual_engine_and_no_limit_relabel(self):
        self.trained()
        self.eval_patches()
        original = cycle.timed_play
        calls = []
        def limit(seed, arm, models, config):
            row = original(seed, arm, models, config)
            row.update(result="limit", board=[1] + [0] * 15, caps=0, max=1)
            calls.append(True)
            return row
        with patch.object(cycle, "timed_play", limit):
            with self.assertRaisesRegex(ValueError, "limit сохранён"):
                cycle.evaluate("screen")
        data = cycle.read(self.root / "screen.json")
        self.assertEqual(data["games"][0]["result"], "limit")
        self.assertEqual(len(data["games"]), 1)
        self.engine.write_bytes(b"changed actual engine")
        with patch.object(cycle, "timed_play", side_effect=AssertionError("must not play")):
            with self.assertRaisesRegex(ValueError, "несовместим"):
                cycle.evaluate("screen", resume=True)

    def test_historical_mc_participates_in_screen_gate(self):
        self.trained()
        self.eval_patches()
        data = cycle.evaluate("screen")
        # Два TD имеют2/3, historic MC3/3: TD выбрать можно, promoted запрещён.
        for row in data["games"]:
            if row["arm"] == "old_mc":
                row.update(result="goal", board=GOAL, caps=3, max=11)
            elif row["arm"].startswith("td_") and row["seed"] == self.p["screen"]["seed_start"]:
                row.update(result="loss", board=LOSS, caps=0, max=2)
        cycle.atomic(self.root / "screen.json", data)
        frozen = cycle.freeze()
        self.assertFalse(frozen["promoted"])
        with self.assertRaisesRegex(ValueError, "заморозк"):
            cycle.verify_freeze(dict(frozen, promoted=True))

    def test_complete_serial_cycle_freeze_hashes_and_exact_tests(self):
        self.trained()
        self.eval_patches()
        screen = cycle.evaluate("screen")
        self.assertEqual(len(screen["games"]), 15)
        self.assertEqual([g["arm"] for g in screen["games"][:5]], list(cycle.ARMS))
        self.assertEqual(screen["games"][5]["arm"], "old_mc")
        frozen = cycle.freeze()
        self.assertEqual(frozen["finalist"], "td_teacher")
        self.assertTrue(frozen["promoted"])
        holdout = cycle.evaluate("holdout")
        self.assertEqual(len(holdout["games"]), 18)
        count = len(FakeTrainer.events)
        result = cycle.finalize()
        self.assertTrue(result["joint_confirmed"])
        self.assertEqual([e["one_sided_p"] for e in result["comparisons"]], [1 / 64, 1 / 64])
        self.assertTrue(all(e["actual_cost_within_10_percent"] for e in result["comparisons"]))
        self.assertEqual(len(FakeTrainer.events), count)
        self.producer.write_text("mutated frozen producer")
        with self.assertRaisesRegex(ValueError, "Замороженный файл"):
            cycle.verify_freeze(frozen)

    def test_freeze_read_side_rejects_semantic_tampering(self):
        self.trained()
        self.eval_patches()
        cycle.evaluate("screen")
        frozen = cycle.freeze()
        mutations = {"version": dict(version=2), "non_td": dict(finalist="old_mc"),
                     "wrong_td": dict(finalist="td_selfplay"), "gate": dict(promoted=False),
                     "policy": dict(policy=dict(frozen["policy"], think=1)),
                     "screen": dict(screen={}), "models": dict(models={})}
        count = len(FakeTrainer.events)
        with patch.object(cycle, "timed_play", side_effect=AssertionError("must reject before playing")) as timed:
            for name, mutation in mutations.items():
                with self.subTest(field=name):
                    with self.assertRaisesRegex(ValueError, "заморозк"):
                        cycle.verify_freeze(dict(frozen, **mutation))
            timed.assert_not_called()
        self.assertEqual(len(FakeTrainer.events), count)

    def test_bad_finalist_rejected_by_holdout_and_finalizer(self):
        self.trained()
        self.eval_patches()
        cycle.evaluate("screen")
        frozen = cycle.freeze()
        cycle.atomic(self.root / "finalist.json", dict(frozen, finalist="old_mc"))
        count = len(FakeTrainer.events)
        with patch.object(cycle, "timed_play", side_effect=AssertionError("must reject before playing")) as timed:
            for action in (lambda: cycle.evaluate("holdout"), cycle.finalize):
                with self.subTest(action=action):
                    with self.assertRaisesRegex(ValueError, "заморозк"):
                        action()
            timed.assert_not_called()
        self.assertEqual(len(FakeTrainer.events), count)
        self.assertFalse((self.root / "holdout.json").exists())
        self.assertFalse((self.root / "summary.json").exists())

    def test_freeze_read_requires_complete_screen_without_continuation(self):
        self.trained()
        self.eval_patches()
        screen = cycle.evaluate("screen")
        frozen = cycle.freeze()
        screen["games"].pop()
        screen["complete"] = False
        path = self.root / "screen.json"
        cycle.atomic(path, screen)
        frozen["hashes"][str(path.resolve())] = cycle.sha(path)
        before = path.read_bytes()
        count = len(FakeTrainer.events)
        with patch.object(cycle, "timed_play", side_effect=AssertionError("must not complete screen")) as timed:
            with self.assertRaisesRegex(ValueError, "неявное продолжение"):
                cycle.verify_freeze(frozen)
            timed.assert_not_called()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(FakeTrainer.events), count)

    def test_duplicate_seed_or_model_tamper_refused(self):
        games = [dict(seed=1, arm=arm, result="goal") for arm in ("td_teacher", "heuristic")]
        self.assertEqual(cycle.paired(games, "td_teacher", "heuristic")["one_sided_p"], 1)
        with self.assertRaisesRegex(ValueError, "Непарные"):
            cycle.paired(games + games[:1], "td_teacher", "heuristic")
        self.pool()
        state = cycle.train("matched")
        selected = Path(state["selected"]["models"]["td_teacher"]["path"])
        selected.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "Milestone model"):
            cycle.train("matched", resume=True)


if __name__ == "__main__":
    unittest.main()
