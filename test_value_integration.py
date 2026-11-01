"""Интеграция шкалы learned value с шанс-узлами, terminal и старым API2."""
import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bot
from value_model import ValueModel


class ValueIntegrationTests(unittest.TestCase):
    def setUp(self):
        bot.configure_value_model(None)
        bot.apply_spawn_model(bot.default_spawn_model())

    def tearDown(self):
        bot.configure_value_model(None)

    def model_file(self, directory):
        path = Path(directory) / "neutral.bin"
        ValueModel().save(path)
        return path

    def test_learned_goal_and_ordinary_scores_share_binary_scale(self):
        goal_move = (11, 11, 10, 10) + (0,) * 12
        ordinary = (1, 1) + (0,) * 14
        with tempfile.TemporaryDirectory() as directory:
            bot.configure_value_model(self.model_file(directory))
            self.assertEqual(bot.NATIVE.value_model_loaded(), 1)
            for board, expected in ((goal_move, 1.0), (ordinary, .5)):
                score = bot.NATIVE.score_levels(bot.pack(board), bot.DIRECTIONS[0], 1)
                self.assertAlmostEqual(score, expected + 1e-6, places=6)
            info = {}
            with patch.object(bot, "THRESHOLDS", (1e-4,)):
                direction = bot.choose(goal_move, 2, .5, info, policy="progressive")
            self.assertTrue(bot.reached_goal(bot.move(goal_move, direction)))

    def test_clear_restores_heuristic_without_reloading_library(self):
        board = (11, 11, 10, 10) + (0,) * 12
        before = bot.NATIVE.score_levels(bot.pack(board), bot.DIRECTIONS[0], 1)
        with tempfile.TemporaryDirectory() as directory:
            bot.configure_value_model(self.model_file(directory))
            bot.configure_value_model(None)
        self.assertEqual(bot.NATIVE.value_model_loaded(), 0)
        self.assertEqual(bot.NATIVE.score_levels(bot.pack(board), bot.DIRECTIONS[0], 1), before)
        self.assertEqual(before, 1e9)

    def test_rejected_model_does_not_replace_current_model(self):
        with tempfile.TemporaryDirectory() as directory:
            good = self.model_file(directory)
            bot.configure_value_model(good)
            bad = Path(directory) / "bad.bin"
            bad.write_bytes(b"not a model")
            with self.assertRaises((ValueError, OSError)):
                bot.configure_value_model(bad)
            self.assertEqual(bot.NATIVE.value_model_loaded(), 1)

    def test_old_engine_allows_default_and_rejects_model_opt_in(self):
        with patch.object(bot, "NATIVE", SimpleNamespace()):
            bot.configure_value_model(None)
            with self.assertRaisesRegex(RuntimeError, "не поддерживает value-модель"):
                bot.configure_value_model("unused.bin")

    def test_cli_rejects_missing_model_before_lock_and_adb(self):
        with patch.object(bot.sys, "argv", ["bot.py", "--value-model", "/missing/bonk-model.bin"]), \
             patch.object(bot.subprocess, "run") as command, \
             patch("builtins.open") as lock_open, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                bot.main()
            self.assertEqual(exc.exception.code, 2)
            command.assert_not_called()
            lock_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
