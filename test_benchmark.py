import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import benchmark
import bot


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.table = bot.spawn_table(bot.default_spawn_model())

    def tearDown(self):
        bot.apply_spawn_model(bot.default_spawn_model())

    def test_timed_cli_forwards_progressive_default_and_explicit_legacy(self):
        row = dict(seed=1, depth=4, think=.01, result="goal", moves=1, caps=3, seconds=.01)
        for extra, policy in (([], "progressive"), (["--search-policy", "legacy"], "legacy")):
            with self.subTest(policy=policy), patch.object(benchmark, "play", return_value=row) as play, \
                 patch.object(benchmark, "save_checkpoint"), contextlib.redirect_stdout(io.StringIO()):
                benchmark.main(["--games", "1", "--think", ".01", *extra])
            self.assertEqual(play.call_args.kwargs, {"policy": policy})

    def test_unsupported_progressive_cli_stops_before_checkpoint_or_game(self):
        from types import SimpleNamespace
        with patch.object(bot, "NATIVE", SimpleNamespace()), patch.object(benchmark, "save_checkpoint") as save, \
             patch.object(benchmark, "play") as play, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                benchmark.main(["--think", ".01"])
        self.assertEqual(error.exception.code, 2)
        save.assert_not_called()
        play.assert_not_called()

    def artificial_game(self, final, max_moves=1):
        start = (1,) + (0,) * 15
        with patch.object(benchmark, "spawn", side_effect=[start, start, final]), \
             patch.object(benchmark, "choose_fixed", return_value=0):
            return benchmark.play(1, 3, None, max_moves, self.table)

    def test_goal_on_last_move(self):
        row = self.artificial_game((11, 11, 11) + (0,) * 13)
        self.assertEqual((row["result"], row["moves"]), ("goal", 1))
        self.assertEqual(row["cap_reached_at"], {3: 1})

    def test_loss_on_last_move(self):
        row = self.artificial_game((1, 2, 1, 2, 2, 1, 2, 1) * 2)
        self.assertEqual((row["result"], row["moves"]), ("loss", 1))

    def test_limit_counts_completed_move(self):
        row = self.artificial_game((2,) + (0,) * 15)
        self.assertEqual((row["result"], row["moves"]), ("limit", 1))

    def test_zero_moves(self):
        row = benchmark.play(1, 3, None, 0, self.table)
        self.assertEqual((row["result"], row["moves"]), ("limit", 0))

    def test_solver_uses_simulator_model(self):
        table = [[0, 1] + [0] * 13 for _ in range(16)]
        row = benchmark.play(1, 3, None, 0, table)
        self.assertEqual(row["board"].count(2), 2)
        self.assertEqual(bot.NATIVE.get_spawn_prob(11, 2), 1)
        self.assertEqual(bot.NATIVE.get_spawn_prob(11, 1), 0)

    def test_fixed_search_has_no_budget_and_matches_native(self):
        board = (5, 2, 1, 1) + (0,) * 12
        with patch.object(bot, "choose", side_effect=AssertionError("timed search called")):
            direction = benchmark.choose_fixed(board, 2)
        allowed = sum(1 << bot.DIRECTIONS[d] for d, _ in bot.legal_moves(board))
        self.assertEqual(bot.DIRECTIONS[direction], bot.NATIVE.solve_levels(bot.pack(board), allowed, 2))

    def test_comparison_reuses_seeds_and_reports_each_depth(self):
        calls = []
        def play(seed, depth, think, max_moves, table):
            calls.append((seed, depth))
            return dict(seed=seed, depth=depth, think=think, result="goal" if seed == 7 else "limit",
                        moves=2, caps=3 if seed == 7 else 1, seconds=.2)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "result.json"
            with patch.object(benchmark, "play", side_effect=play), contextlib.redirect_stdout(io.StringIO()):
                benchmark.main(["--games", "2", "--seed", "7", "--depths", "2", "3", "--out", str(out)])
            result = json.loads(out.read_text())
        self.assertEqual(calls, [(7, 2), (8, 2), (7, 3), (8, 3)])
        self.assertEqual([r["win_rate"] for r in result["comparisons"]], [.5, .5])
        self.assertEqual([r["limits"] for r in result["comparisons"]], [1, 1])
        self.assertEqual(result["comparisons"][0]["seconds_per_move"], .1)

    def test_endgame_depth_switches_and_counts_only_completed_moves(self):
        first = (11,1)+(0,)*14
        two = (11,11,1)+(0,)*13
        goal = (11,11,11)+(0,)*13
        for think in (None, .2):
            for terminal_direction in (1, None):
                target = benchmark if think is None else bot
                function = "choose_fixed" if think is None else "choose"
                with patch.object(benchmark, "spawn", side_effect=[first,first,two,goal]), \
                     patch.object(target, function, side_effect=[0,terminal_direction]) as choose:
                    row = benchmark.play(1,3,think,10,self.table,endgame_depth=4)
                self.assertEqual([c.args[1] for c in choose.call_args_list], [3,4])
                self.assertEqual(row["endgame_depth"], 4)
                self.assertEqual(row["depth_counts"], {"3":1,"4":1} if terminal_direction is not None else {"3":1})
                self.assertEqual(sum(row["depth_counts"].values()),row["moves"])



class CheckpointTests(unittest.TestCase):
    @staticmethod
    def game(seed, depth, think, max_moves, table):
        return dict(seed=seed, depth=depth, think=think, result="goal", moves=2,
                    caps=3, seconds=.2, board=[11,11,11]+[0]*13)

    def test_interruption_resume_and_completed_run_skips_every_game(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "result.json"
            args = ["--games", "2", "--seed", "7", "--depths", "2", "3", "--out", str(out)]
            with patch.object(benchmark, "play", side_effect=[self.game(7,2,None,6000,None), KeyboardInterrupt]), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    benchmark.main(args)
            interrupted = json.loads(out.read_text())
            self.assertEqual(len(interrupted["games"]), 1)
            self.assertFalse(interrupted["complete"])
            self.assertEqual(interrupted["comparisons"][0]["games"], 1)
            with patch.object(benchmark, "play", side_effect=self.game) as play, contextlib.redirect_stdout(io.StringIO()):
                benchmark.main(args + ["--resume"])
            self.assertEqual([(c.args[0], c.args[1]) for c in play.call_args_list], [(8,2),(7,3),(8,3)])
            finished = json.loads(out.read_text())
            self.assertTrue(finished["complete"])
            self.assertEqual(len(finished["games"]), 4)
            with patch.object(benchmark, "play", side_effect=AssertionError("repeated game")), \
                 contextlib.redirect_stdout(io.StringIO()):
                benchmark.main(args + ["--resume"])
            self.assertEqual(json.loads(out.read_text()), finished)

    def test_incompatible_config_model_or_sources_preserves_file(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "result.json"
            args = ["--games", "1", "--out", str(out)]
            with patch.object(benchmark, "play", side_effect=self.game), contextlib.redirect_stdout(io.StringIO()):
                benchmark.main(args)
            original = out.read_bytes()
            changed = bot.default_spawn_model()
            changed["stages"]["0"][0] += 1
            changes = [(["--depth", "5"], contextlib.nullcontext()),
                       ([], patch.object(bot, "default_spawn_model", return_value=changed)),
                       ([], patch.object(benchmark, "source_hashes", return_value={"changed":"yes"}))]
            for extra, context in changes:
                with context, patch.object(benchmark,"play", side_effect=AssertionError("game started")), \
                     contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as exc:
                        benchmark.main(args + extra + ["--resume"])
                    self.assertEqual(exc.exception.code, 2)
                self.assertEqual(out.read_bytes(), original)

    def test_loaded_engine_change_rejected_without_overwrite(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "result.json"
            engine = Path(directory) / "engine.dylib"
            engine.write_bytes(b"baseline engine")
            args = ["--games", "1", "--out", str(out)]
            with patch.object(bot, "NATIVE", SimpleNamespace(_name=str(engine))), \
                 patch.object(benchmark, "play", side_effect=self.game), \
                 contextlib.redirect_stdout(io.StringIO()):
                benchmark.main(args)
            original = out.read_bytes()
            # Source files remain identical, but the actually selected binary changes.
            other = Path(directory) / "experimental.dylib"
            other.write_bytes(b"different engine with same source path")
            with patch.object(bot, "NATIVE", SimpleNamespace(_name=str(other))), \
                 patch.object(benchmark, "play", side_effect=AssertionError("game started")), \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    benchmark.main(args + ["--resume"])
                self.assertEqual(exc.exception.code, 2)
            self.assertEqual(out.read_bytes(), original)
            # Moving the same bytes is compatible; paths do not fingerprint strategy.
            other.write_bytes(engine.read_bytes())
            with patch.object(bot, "NATIVE", SimpleNamespace(_name=str(other))), \
                 patch.object(benchmark, "play", side_effect=AssertionError("game repeated")), \
                 contextlib.redirect_stdout(io.StringIO()):
                benchmark.main(args + ["--resume"])

    def test_missing_loaded_engine_has_user_error_without_overwrite(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "result.json"
            original = b'{"prior":"unchanged"}'
            out.write_bytes(original)
            with patch.object(bot, "NATIVE", SimpleNamespace(_name=str(Path(directory)/"missing.dylib"))), \
                 contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit) as exc:
                    benchmark.main(["--out", str(out)])
                self.assertEqual(exc.exception.code, 2)
            self.assertIn("Не удалось прочитать загруженный движок", errors.getvalue())
            self.assertEqual(out.read_bytes(), original)

    def test_endgame_config_is_forwarded_and_resume_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "result.json"
            args = ["--games","1","--depth","3","--endgame-depth","4","--out",str(out)]
            row = self.game(1,3,None,6000,None)
            row.update(endgame_depth=4,depth_counts={"3":2})
            with patch.object(benchmark,"play",return_value=row) as play, \
                 contextlib.redirect_stdout(io.StringIO()):
                benchmark.main(args)
            self.assertEqual(play.call_args.args[-1],4)
            self.assertEqual(json.loads(out.read_text())["config"]["endgame_depth"],4)
            original = out.read_bytes()
            with patch.object(benchmark,"play",side_effect=AssertionError("game started")), \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    benchmark.main(args+["--endgame-depth","5","--resume"])
                self.assertEqual(exc.exception.code,2)
            self.assertEqual(out.read_bytes(),original)

    def test_failed_atomic_replace_keeps_previous_json(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "result.json"
            benchmark.save_checkpoint(out, {"old":True})
            with patch.object(benchmark.os, "replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    benchmark.save_checkpoint(out, {"new":True})
            self.assertEqual(json.loads(out.read_text()), {"old":True})
            self.assertEqual(list(Path(directory).iterdir()), [out])

    def test_missing_or_legacy_checkpoint_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "result.json"
            for content in (None, {"games":[]}):
                if content is not None:
                    out.write_text(json.dumps(content))
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        benchmark.main(["--resume", "--out", str(out)])


if __name__ == "__main__":
    unittest.main()
