"""Регрессии заморозки статистики и терминальных снимков корпуса."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

DIRECTORY = Path(__file__).parent / "experiments" / "learning"
sys.path.insert(0, str(DIRECTORY))
import cycle
import position_study
SPEC = importlib.util.spec_from_file_location("learning_evaluate", DIRECTORY / "evaluate.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class PositionTests(unittest.TestCase):
    def corpus(self, root, boards):
        data = dict(complete=True, games=[dict(seed=9, cap_boards=boards)])
        (root / "corpus-source.json").write_text(json.dumps(data))

    def terminal_snapshot(self):
        before = (11, 10, 10, 1, 2, 1, 2, 1, 1, 2, 1, 2, 2, 1, 2, 1)
        board = list(cycle.bot.move(before, 0))
        self.assertEqual(board[3], 0)
        board[3] = 2
        self.assertFalse(cycle.bot.reached_goal(tuple(board)))
        self.assertFalse(cycle.bot.legal_moves(tuple(board)))
        return board

    def test_terminal_loss_is_not_a_search_position(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(position_study, "ROOT", Path(directory)):
            self.corpus(Path(directory), {"2": self.terminal_snapshot()})
            with self.assertRaisesRegex(ValueError, "Нет незавершённых"):
                position_study.positions()

    def test_terminal_is_removed_before_stratification(self):
        good = [11, 1] + [0] * 14
        with tempfile.TemporaryDirectory() as directory, patch.object(position_study, "ROOT", Path(directory)):
            self.corpus(Path(directory), {"1": good, "2": self.terminal_snapshot()})
            rows = position_study.positions()
            self.assertEqual([r["caps"] for r in rows], [1])
            self.assertEqual(rows[0]["board"], good)

    def test_corrupt_snapshot_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(position_study, "ROOT", Path(directory)):
            self.corpus(Path(directory), {"2": [11] * 17})
            with self.assertRaises(ValueError):
                position_study.positions()


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.root = self.work / "experiments" / "learning"
        self.root.mkdir(parents=True)
        self.real_plan = cycle.plan()
        for name in ("bot.py", "baseline.bin", "native.bin"):
            (self.work / name).write_text(name)
        for name in ("evaluate.py", "position_study.py"):
            (self.root / name).write_text("# frozen source " + name)
        (self.root / "screen.json").write_text("{}")
        self.addCleanup(patch.stopall)
        patch.object(cycle, "WORK", self.work).start()
        patch.object(cycle, "ROOT", self.root).start()
        patch.object(evaluation, "ROOT", self.root).start()
        patch.object(cycle, "plan", return_value=self.real_plan).start()
        patch.object(cycle, "model_gate", return_value=dict(passed=False)).start()
        patch.object(evaluation.subprocess, "check_output", return_value="synthetic-commit").start()
        rows = [dict(seed=1, arm=arm, result=result, moves=1, search_wall_seconds=.01,
                     seconds=.02, positions=1, fallbacks=0, cuts=0, depth_counts={"3": 1})
                for arm, result in (("baseline_legacy", "loss"), ("progressive", "goal"))]
        data = dict(config=dict(arms=["baseline_legacy", "progressive"]), games=rows,
                    source_hashes=dict(files={"bot.py": cycle.sha(self.work / "bot.py")},
                        engines={arm: dict(path=path, sha256=cycle.sha(self.work / path))
                                 for arm, path in (("baseline_legacy", "baseline.bin"), ("progressive", "native.bin"))}))
        patch.object(evaluation, "read_stage", return_value=data).start()

    def test_statistics_and_study_sources_are_frozen(self):
        evaluation.freeze()
        frozen = json.loads((self.root / "finalist.json").read_text())
        for name in ("evaluate.py", "position_study.py"):
            relative = "experiments/learning/" + name
            self.assertEqual(frozen["hashes"].get(relative), cycle.sha(self.root / name))

    def test_holdout_refuses_changed_statistics_source(self):
        evaluation.freeze()
        (self.root / "evaluate.py").write_text("# different decision rule")
        with self.assertRaisesRegex(ValueError, "не соответствует заморозке"):
            cycle.evaluation_config("holdout")


if __name__ == "__main__":
    unittest.main()
