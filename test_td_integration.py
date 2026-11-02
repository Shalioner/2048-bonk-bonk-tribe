"""Интеграция отдельного TD-формата с существующим выбором хода и MC."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bot
from td_model import TDModel
from value_model import ValueModel


class TDIntegrationTests(unittest.TestCase):
    def setUp(self):
        bot.configure_value_model(None)
        bot.apply_spawn_model(bot.default_spawn_model())

    def tearDown(self):
        bot.configure_value_model(None)

    def test_td_goal_scale_and_search_choose_actual_goal(self):
        board=(11,11,10,10)+(0,)*12
        ordinary=(1,1)+(0,)*14
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"td.bin"
            TDModel().save(path)
            bot.configure_value_model(path)
            self.assertEqual(bot.NATIVE.td_model_loaded(),1)
            self.assertEqual(bot.NATIVE.value_model_loaded(),0)
            self.assertAlmostEqual(bot.NATIVE.score_levels(bot.pack(ordinary),bot.DIRECTIONS[0],0),.5+1e-6,places=6)
            self.assertAlmostEqual(bot.NATIVE.score_levels(bot.pack(board),bot.DIRECTIONS[0],1),1,places=5)
            direction=bot.choose(board,2,.1,policy="progressive")
            self.assertTrue(bot.reached_goal(bot.move(board,direction)))

    def test_switching_formats_and_clear_preserves_heuristic(self):
        board=(1,1)+(0,)*14
        original=bot.NATIVE.score_levels(bot.pack(board),bot.DIRECTIONS[0],0)
        with tempfile.TemporaryDirectory() as directory:
            td=Path(directory)/"td.bin"
            mc=Path(directory)/"mc.bin"
            TDModel().save(td)
            ValueModel().save(mc)
            for path,expected in ((td,(1,0)),(mc,(0,1)),(td,(1,0))):
                bot.configure_value_model(path)
                self.assertEqual((bot.NATIVE.td_model_loaded(),bot.NATIVE.value_model_loaded()),expected)
            bot.configure_value_model(None)
        self.assertEqual((bot.NATIVE.td_model_loaded(),bot.NATIVE.value_model_loaded()),(0,0))
        self.assertEqual(bot.NATIVE.score_levels(bot.pack(board),bot.DIRECTIONS[0],0),original)

    def test_invalid_python_or_native_load_preserves_active_td(self):
        with tempfile.TemporaryDirectory() as directory:
            good=Path(directory)/"good.bin"
            bad=Path(directory)/"bad.bin"
            TDModel().save(good)
            bot.configure_value_model(good)
            bad.write_bytes(b"BONKTD01broken")
            with self.assertRaises((ValueError,OSError)):
                bot.configure_value_model(bad)
            self.assertEqual(bot.NATIVE.td_model_loaded(),1)
            self.assertEqual(bot.NATIVE.load_value_model(str(bad).encode()),-1)
            self.assertEqual(bot.NATIVE.td_model_loaded(),1)
            self.assertEqual(bot.NATIVE.load_td_model(str(bad).encode()),-1)
            self.assertEqual(bot.NATIVE.td_model_loaded(),1)

    def test_old_mc_engine_rejects_td_feature_before_native_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"td.bin"
            TDModel().save(path)
            fake=SimpleNamespace(load_value_model=lambda p:0,clear_value_model=lambda:None,
                                 value_model_loaded=lambda:0,value_model_error=lambda:b"")
            with patch.object(bot,"NATIVE",fake):
                with self.assertRaisesRegex(RuntimeError,"не поддерживает TD-модель"):
                    bot.configure_value_model(path)


if __name__=="__main__":
    unittest.main()
