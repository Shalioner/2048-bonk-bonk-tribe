"""Активная версия: pure search, CLI progressive, совместимый legacy API."""
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bot


class PureMathTests(unittest.TestCase):
    def test_no_active_ml_files_or_exports(self):
        for name in ("value_model.py", "td_model.py", "vendor/value_model.h", "vendor/td_model.h",
                     "vendor/td_train.cpp", "experiments/learning", "experiments/td"):
            self.assertFalse((bot.ROOT / name).exists(), name)
        for symbol in ("load_value_model", "load_td_model", "clear_value_model", "clear_td_model"):
            self.assertFalse(hasattr(bot.NATIVE, symbol), symbol)
        self.assertFalse(hasattr(bot, "configure_value_model"))

    def test_cli_progressive_default_checks_optional_symbol_before_io(self):
        with patch.object(bot.sys, "argv", ["bot.py"]), patch.object(bot, "NATIVE", SimpleNamespace()), \
             patch("builtins.open") as opened, patch.object(bot.subprocess, "run") as command, \
             contextlib.redirect_stderr(io.StringIO()) as error:
            with self.assertRaises(SystemExit) as stopped:
                bot.main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("не поддерживает progressive", error.getvalue())
        opened.assert_not_called()
        command.assert_not_called()

    def test_cli_explicit_legacy_reaches_lock_without_progressive_symbol(self):
        class BeforeIO(Exception):
            pass
        with patch.object(bot.sys, "argv", ["bot.py", "--search-policy", "legacy"]), \
             patch.object(bot, "NATIVE", SimpleNamespace()), patch("builtins.open", side_effect=BeforeIO), \
             patch.object(bot.subprocess, "run") as command:
            with self.assertRaises(BeforeIO):
                bot.main()
        command.assert_not_called()

    def test_depth_think_and_spawn_frequency_learning_preserved(self):
        self.assertEqual((bot.DEFAULT_DEPTH, bot.DEFAULT_THINK), (8, 10))
        model = bot.default_spawn_model()
        before = bot.stage_row(model, 11)[0]
        bot.record_spawn(model, 11, 1)
        self.assertEqual(bot.stage_row(model, 11)[0], before + 1)


if __name__ == "__main__":
    unittest.main()
