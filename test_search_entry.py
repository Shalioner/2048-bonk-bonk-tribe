"""Mocked banked entry routing/ledger checks; no native, phone or new games."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import copy
import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import play_search_alternative as entry

SPEC = importlib.util.spec_from_file_location("entry_ledger_reference", Path(__file__).resolve().parent / "experiments/search_alternatives/run.py")
REFERENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REFERENCE)
EARLY = (1,) + (0,)*15
LATE = (11,11,1) + (0,)*13


class SearchEntryTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.locked = False
        self.original_choose = Mock(return_value=0)
        self.original_native = object()
        self.original_thresholds = (.7,)
        self.selected_native = SimpleNamespace(solve_budget_progressive=object())
        self.bot = SimpleNamespace(choose=self.original_choose, NATIVE=self.original_native,
                                   THRESHOLDS=self.original_thresholds, DEFAULT_THINK=10., DEFAULT_DEPTH=8,
                                   main=Mock(return_value="done"))
        self.summary = dict(finalist=entry.ARM, confirmed_improvement=True, paired=dict(improved=True, one_sided_p=.027),
                            freeze_sha256="frozen-sha", control_sha256="control-sha")
        self.plan = dict(search=dict(think=.01, depth=8, policy="progressive"), budget_profiles={entry.ARM:dict(entry.PROFILE)})
        @contextmanager
        def lease():
            self.assertFalse(self.locked)
            self.locked = True
            self.events.append("lease-in")
            try:
                yield
            finally:
                self.locked = False
                self.events.append("lease-out")
        def frozen():
            self.assertTrue(self.locked)
            self.events.append("freeze")
            return dict(finalist=entry.ARM)
        def control(*args, **kwargs):
            self.assertTrue(self.locked)
            self.events.append("control")
            return dict(complete=True, games=["validated existing control rows"])
        def configure(arm):
            self.assertTrue(self.locked)
            self.assertEqual(arm, entry.ARM)
            self.events.append("configure")
            self.bot.NATIVE, self.bot.THRESHOLDS = self.selected_native, entry.THRESHOLDS
            return self.bot
        self.cycle = SimpleNamespace(ROOT=Path("/mock/experiment"), lease=lease,
             verify_freeze=Mock(side_effect=frozen), checkpoint=Mock(side_effect=control),
             paired=Mock(return_value=dict(self.summary["paired"])), read=Mock(side_effect=lambda path: copy.deepcopy(self.summary)),
             sha=Mock(side_effect=lambda path: "frozen-sha" if path.name == "finalist.json" else "control-sha"),
             arm_config=Mock(return_value=dict(kind=entry.ARM, thresholds=list(entry.THRESHOLDS), budget_profile=entry.ARM)),
             plan=Mock(side_effect=lambda: copy.deepcopy(self.plan)), configure=Mock(side_effect=configure),
             allocate_budget=Mock(wraps=REFERENCE.allocate_budget), charge_budget=Mock(wraps=REFERENCE.charge_budget),
             play=Mock(side_effect=AssertionError("must not start evaluation games")),
             finalize=Mock(side_effect=AssertionError("must not overwrite existing summary")))

    def runtime(self, args=None):
        with patch.object(entry, "load_cycle", return_value=self.cycle), patch.dict(sys.modules, {"bot":self.bot}):
            return entry.main(args or [])

    @staticmethod
    def clock(durations):
        return [value for i, duration in enumerate(durations) for value in (100.+i, 100.+i+duration)]

    def assert_restored(self, argv):
        self.assertIs(self.bot.choose, self.original_choose)
        self.assertIs(self.bot.NATIVE, self.original_native)
        self.assertIs(self.bot.THRESHOLDS, self.original_thresholds)
        self.assertIs(sys.argv, argv)

    def test_help_and_invalid_args_never_load_native_cycle_or_adb(self):
        with patch.object(entry, "load_cycle", side_effect=AssertionError("no artifact/native reads")) as load:
            with redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as stopped:
                entry.main(["--help"])
            self.assertEqual(stopped.exception.code, 0)
            for text in ("63/100", "49/100", "1.595", "1.562", "168", "40", "--dry-run", "--serial"):
                self.assertIn(text, output.getvalue())
            for args in (["--think","10"], ["--think","-1"], ["--think","nan"], ["--think","inf"],
                         ["--depth","7"], ["--depth","0"], ["--search-policy","legacy"], ["--unknown"]):
                with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    entry.main(args)
                self.assertEqual(error.exception.code, 2)
            load.assert_not_called()

    def test_runtime_routing_verified_under_lease_and_restored(self):
        before = sys.argv
        def main():
            self.assertFalse(self.locked)
            self.assertIs(self.bot.NATIVE, self.selected_native)
            self.assertEqual(self.bot.THRESHOLDS, entry.THRESHOLDS)
            self.assertEqual(self.bot.DEFAULT_THINK, 10.)
            argv = sys.argv
            for option, value in (("--steps","17"), ("--delay","0.2"), ("--serial","mock-device"),
                                  ("--think","0.01"), ("--depth","8"), ("--search-policy","progressive")):
                self.assertEqual(argv[argv.index(option)+1], value)
            self.assertIn("--dry-run", argv)
            info = {}
            self.assertEqual(self.bot.choose(EARLY, 8, .01, info, policy="progressive"), 0)
            self.assertEqual(info["budget_profile"], entry.ARM)
            return "done"
        self.bot.main.side_effect = main
        with patch.object(entry.time, "monotonic", side_effect=self.clock([.002])):
            self.assertEqual(self.runtime(["--steps","17","--delay",".2","--serial","mock-device","--dry-run"]), "done")
        self.assertEqual(self.events, ["lease-in", "freeze", "control", "configure", "lease-out"])
        self.cycle.checkpoint.assert_called_once_with("control", True, validate_only=True)
        self.original_choose.assert_called_once()
        args, kwargs = self.original_choose.call_args
        self.assertEqual((args[0],args[1],args[2],kwargs), (EARLY,8,.01,dict(policy="progressive")))
        self.cycle.play.assert_not_called()
        self.cycle.finalize.assert_not_called()
        self.assert_restored(before)

    def test_negative_unconfirmed_baseline_partial_and_stale_summary_gate(self):
        cases = ("unconfirmed", "negative", "baseline", "partial", "stale_summary")
        for case in cases:
            with self.subTest(case=case):
                summary = copy.deepcopy(self.summary)
                frozen = dict(finalist="baseline" if case == "baseline" else entry.ARM)
                control = dict(complete=case != "partial", games=["existing"])
                pair = dict(summary["paired"], improved=case != "negative")
                if case == "unconfirmed":
                    summary["confirmed_improvement"] = False
                if case == "stale_summary":
                    summary["control_sha256"] = "other-control"
                with patch.object(self.cycle, "verify_freeze", return_value=frozen), \
                     patch.object(self.cycle, "checkpoint", return_value=control), patch.object(self.cycle, "paired", return_value=pair), \
                     patch.object(self.cycle, "read", return_value=summary), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        self.runtime()
                self.assertEqual(error.exception.code, 2)
        self.bot.main.assert_not_called()
        self.cycle.configure.assert_not_called()
        self.original_choose.assert_not_called()
        self.cycle.play.assert_not_called()
        self.cycle.finalize.assert_not_called()

    def test_drifted_profile_thresholds_or_search_settings_refused(self):
        for case in ("profile", "thresholds", "depth", "think", "policy"):
            with self.subTest(case=case):
                plan, arm = copy.deepcopy(self.plan), dict(kind=entry.ARM,thresholds=list(entry.THRESHOLDS),budget_profile=entry.ARM)
                if case == "profile":
                    plan["budget_profiles"][entry.ARM]["two_cap_call_cap"] = .08
                elif case == "thresholds":
                    arm["thresholds"] = [1e-5]
                else:
                    plan["search"][case] = {"depth":7, "think":.02, "policy":"legacy"}[case]
                with patch.object(self.cycle,"plan",return_value=plan), patch.object(self.cycle,"arm_config",return_value=arm), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        self.runtime()
        self.bot.main.assert_not_called()
        self.cycle.configure.assert_not_called()

    def test_ledger_reuses_math_late40_debt_and_none_charge(self):
        choose = Mock(side_effect=[0,0,0,0,0,None,0])
        wrapper = entry.BankedChooser(self.cycle, entry.PROFILE, choose)
        infos = [{} for _ in range(7)]
        durations = [.001]*4 + [.168,.002,.001]
        boards = [EARLY]*4 + [LATE]*3
        with patch.object(entry.time,"monotonic",side_effect=self.clock(durations)):
            results = [wrapper(board,info=info) for board,info in zip(boards,infos)]
        budgets = [call.args[2] for call in choose.call_args_list]
        self.assertEqual(budgets[:4], [.01]*4)
        self.assertEqual(budgets[4], .04)
        self.assertEqual(budgets[5:], [0.,0.])
        self.assertIsNone(results[5])
        self.assertLess(infos[4]["budget_credit_s"], -.12)
        self.assertAlmostEqual(infos[5]["external_choose_wall_s"], .002)
        credit = 0.
        for board, elapsed in zip(boards,durations):
            _, credit = REFERENCE.allocate_budget(entry.PROFILE,credit,board.count(11))
            credit = REFERENCE.charge_budget(credit,elapsed)
        self.assertAlmostEqual(wrapper.credit, credit)
        self.assertEqual(self.cycle.allocate_budget.call_count, 7)
        self.assertEqual(self.cycle.charge_budget.call_count, 7)
        self.assertEqual(entry.BankedChooser(self.cycle, entry.PROFILE, choose).credit, 0)

    def test_reset_on_each_main_and_no_wrapper_stacking(self):
        budgets = []
        def main():
            self.bot.choose(LATE)
            budgets.append(self.original_choose.call_args.args[2])
        self.bot.main.side_effect = main
        before = sys.argv
        with patch.object(entry.time,"monotonic",side_effect=self.clock([.168,.001])):
            self.runtime()
            self.assert_restored(before)
            self.runtime()
            self.assert_restored(before)
        self.assertEqual(budgets, [.01,.01])
        self.assertEqual(self.original_choose.call_count, 2)

    def test_exception_charged_and_runtime_state_restored(self):
        wrappers = []
        def main():
            wrappers.append(self.bot.choose)
            self.bot.choose(LATE)
        self.bot.main.side_effect = main
        self.original_choose.side_effect = RuntimeError("mock native failure")
        before = sys.argv
        with patch.object(entry.time,"monotonic",side_effect=self.clock([.168])), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.runtime()
        self.assertAlmostEqual(wrappers[0].credit, .01-.168)
        self.assert_restored(before)

    def test_native_load_failure_restores_state_without_runtime(self):
        before = sys.argv
        def broken(arm):
            self.bot.THRESHOLDS = entry.THRESHOLDS
            raise RuntimeError("mock missing selected DLL")
        with patch.object(self.cycle,"configure",side_effect=broken), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.runtime()
        self.bot.main.assert_not_called()
        self.original_choose.assert_not_called()
        self.assert_restored(before)

    def test_wrapper_rejects_unmeasured_mode_before_choose(self):
        wrapper = entry.BankedChooser(self.cycle, entry.PROFILE, self.original_choose)
        for options in (dict(depth=7),dict(think=10),dict(policy="legacy")):
            with self.subTest(options=options), self.assertRaises(ValueError):
                wrapper(EARLY,**options)
        self.assertEqual(wrapper.credit, 0)
        self.original_choose.assert_not_called()
        self.cycle.allocate_budget.assert_not_called()


if __name__ == "__main__":
    unittest.main()
