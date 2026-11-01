"""Контракт opt-in углубления: полный уровень, отмена и старый движок."""
import contextlib
import io
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bot


class ProgressiveSearchTests(unittest.TestCase):
    BOARD = (10, 7, 6, 4, 4, 3, 1, 2, 3, 2, 1, 0, 2, 1, 0, 1)

    def setUp(self):
        bot.apply_spawn_model(bot.default_spawn_model())

    def best_fixed(self, depth, threshold):
        scores = {d: bot.NATIVE.score_levels_thresh(bot.pack(self.BOARD), bot.DIRECTIONS[d], depth, threshold)
                  for d, _ in bot.legal_moves(self.BOARD)}
        return max(scores, key=scores.get)

    def test_target_depth_matches_fixed_search(self):
        info = {}
        with patch.object(bot, "THRESHOLDS", (1e-4,)):
            direction = bot.choose(self.BOARD, 3, 1, info, policy="progressive")
        self.assertEqual((info["depth"], info["requested_depth"], info["passes"]), (3, 3, 2))
        self.assertFalse(info["fallback"])
        self.assertEqual(info["threshold"], 1e-4)
        self.assertEqual(direction, self.best_fixed(3, 1e-4))

    def test_refines_threshold_only_after_target(self):
        info = {}
        with patch.object(bot, "THRESHOLDS", (1e-4, 1e-5)):
            direction = bot.choose(self.BOARD, 2, 1, info, policy="progressive")
        self.assertEqual((info["depth"], info["passes"], info["threshold"]), (2, 2, 1e-5))
        self.assertEqual(direction, self.best_fixed(2, 1e-5))

    def test_zero_threshold_is_distinct_from_missing_completed_pass(self):
        info = {}
        with patch.object(bot, "THRESHOLDS", (0,)):
            direction = bot.choose(self.BOARD, 2, 1, info, policy="progressive")
        self.assertEqual(info["threshold"], 0)
        self.assertIsNotNone(info["threshold"])
        self.assertEqual((info["depth"], info["passes"], info["fallback"]), (2, 1, False))
        self.assertEqual(direction, self.best_fixed(2, 0))

    def test_zero_budget_has_legal_depth_one_fallback(self):
        info = {}
        started = time.monotonic()
        direction = bot.choose(self.BOARD, 8, 0, info, policy="progressive")
        elapsed = time.monotonic() - started
        self.assertLessEqual(elapsed, .05)
        self.assertEqual(direction, self.best_fixed(1, 1e-4))
        self.assertEqual((info["depth"], info["requested_depth"], info["passes"]), (1, 8, 0))
        self.assertTrue(info["fallback"])

    def test_aborted_deeper_pass_retains_completed_depth(self):
        info = {}
        budget = .04
        started = time.monotonic()
        # Малый порог не даёт отсечению превратить глубину30 в мелкий поиск.
        with patch.object(bot, "THRESHOLDS", (1e-10,)), patch.object(bot, "GROWTH", 0):
            direction = bot.choose(self.BOARD, 30, budget, info, policy="progressive")
        elapsed = time.monotonic() - started
        self.assertLessEqual(elapsed, budget + .05)
        self.assertTrue(info["cut"])
        self.assertFalse(info["fallback"])
        self.assertGreaterEqual(info["depth"], 2)
        self.assertLess(info["depth"], 30)
        self.assertEqual(direction, self.best_fixed(info["depth"], 1e-10))

    def test_short_budgets_are_hard(self):
        for budget in (.001, .005, .02):
            with self.subTest(budget=budget):
                info = {}
                started = time.monotonic()
                with patch.object(bot, "THRESHOLDS", (1e-6,)), patch.object(bot, "GROWTH", 0):
                    direction = bot.choose(self.BOARD, 16, budget, info, policy="progressive")
                self.assertLessEqual(time.monotonic() - started, budget + .05)
                self.assertIn(direction, [d for d, _ in bot.legal_moves(self.BOARD)])
                self.assertGreaterEqual(info["depth"], 1)
                self.assertLessEqual(info["depth"], 16)
                self.assertEqual(info["requested_depth"], 16)

    def test_legacy_engine_is_supported_until_progressive_is_requested(self):
        calls = []

        def legacy(*args):
            calls.append("legacy")
            info = args[-1]
            info[0], info[1], info[7] = 1, 1e-4, 0
            return bot.DIRECTIONS[0]

        with patch.object(bot, "NATIVE", SimpleNamespace(solve_budget=legacy)):
            info = {}
            self.assertEqual(bot.choose(self.BOARD, info=info), 0)
            self.assertEqual(calls, ["legacy"])
            self.assertNotIn("requested_depth", info)
            with self.assertRaisesRegex(RuntimeError, "не поддерживает progressive.*пересоберите"):
                bot.choose(self.BOARD, policy="progressive")
        self.assertEqual(calls, ["legacy"])

    def test_invalid_policy_and_progressive_depth_are_rejected(self):
        with self.assertRaises(ValueError):
            bot.choose(self.BOARD, policy="unknown")
        with self.assertRaises(ValueError):
            bot.choose(self.BOARD, depth=0, policy="progressive")

    def test_cli_rejects_unsupported_or_invalid_progressive_before_io(self):
        for args, native in ((["--depth", "0"], bot.NATIVE), ([], SimpleNamespace())):
            with self.subTest(args=args), patch.object(bot.sys, "argv", ["bot.py", "--search-policy", "progressive", *args]), \
                 patch.object(bot, "NATIVE", native), patch("builtins.open") as opened, \
                 patch.object(bot.subprocess, "run") as command, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    bot.main()
                self.assertEqual(error.exception.code, 2)
                opened.assert_not_called()
                command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
