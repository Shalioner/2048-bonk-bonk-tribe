import contextlib
import errno
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
import bot


class FakeClock:
    def __init__(self): self.now = 0.
    def monotonic(self): return self.now
    def advance(self, seconds): self.now += seconds


class CycleTests(unittest.TestCase):
    board = (1, 1) + (0,) * 14

    def run_cycle(self, boards=None, cropped=False, bad_crop=False, focus_change=False,
                  stop_in_choose=False, stop_in_focus=False, read_error=None, steps=1,
                  crop_error=None, nested_ocr=False, read_errors=None, frame_bytes=None):
        clock = FakeClock()
        output = io.StringIO()
        frames = iter(boards or [self.board, self.board])
        errors = iter(read_errors or [])
        state = dict(focus=0, captures=0, crop=0, gestures=0, decodes=0)
        model = bot.default_spawn_model()
        original_read, original_decode = bot.read_board, bot.decode_screen
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def run(argv, **kwargs):
                self.assertEqual(argv[0], 'adb')
                cmd = argv[1:]
                if cmd == ['shell', 'wm', 'size']:
                    return SimpleNamespace(stdout=(b'Physical size: 1440x3040' if cropped else b'Physical size: 100x100'))
                if cmd == ['shell', 'dumpsys', 'window']:
                    state['focus'] += 1
                    clock.advance(.02)
                    if stop_in_focus and state['focus'] == 2:
                        (root / 'STOP').write_text('stop')
                    package = 'other.app' if focus_change and state['focus'] > 1 else bot.PACKAGE
                    return SimpleNamespace(stdout=f'mCurrentFocus {package}'.encode())
                if cmd[0] == 'exec-out':
                    state['captures'] += 1
                    clock.advance(.03)
                    if 'dd bs=' in cmd[1]:
                        state['crop'] += 1
                        if crop_error:raise crop_error
                        return SimpleNamespace(stdout=b'' if bad_crop else b'x' * (5760 * 81 * 4))
                    return SimpleNamespace(stdout=frame_bytes if frame_bytes is not None else b'full frame')
                if cmd[0] == 'shell' and 'input motionevent' in cmd[1]:
                    state['gestures'] += 1
                    state['gesture_command'] = cmd[1]
                    clock.advance(.01)
                    return SimpleNamespace(stdout=b'')
                self.fail(f'Unexpected adb command: {cmd}')
            def read(data, metrics=None):
                if nested_ocr:
                    clock.advance(.02)
                    metrics.counts['ocr_calls'] += 1
                    metrics.call('ocr', clock.advance, .02)
                else:clock.advance(.04)
                if read_error: raise read_error
                failure = next(errors, None)
                if failure:raise failure
                if frame_bytes is not None:return original_read(data, metrics=metrics)
                return next(frames)
            def decode(data):
                state['decodes'] += 1
                if frame_bytes is not None:return original_decode(data)
                return SimpleNamespace(size=(1440, 3040), save=Mock())
            def choose(board, depth, think, info):
                self.assertEqual((depth, think), (8, 10.))
                clock.advance(.07)
                info.update(search_s=.07, passes=1, positions=4)
                if stop_in_choose: (root / 'STOP').write_text('stop')
                return 0
            lock = Mock()
            lock.fileno.return_value = 123
            with contextlib.ExitStack() as stack:
                for name, value in [('ROOT', root), ('open', Mock(return_value=lock)),
                                    ('load_spawn_model', Mock(return_value=model)),
                                    ('apply_spawn_model', Mock()), ('save_spawn_model', Mock()),
                                    ('read_board', Mock(side_effect=read)), ('choose', Mock(side_effect=choose)),
                                    ('decode_screen', Mock(side_effect=decode))]:
                    stack.enter_context(patch.object(bot, name, value, create=True))
                stack.enter_context(patch.object(bot.fcntl, 'flock'))
                stack.enter_context(patch.object(bot.subprocess, 'run', side_effect=run))
                stack.enter_context(patch.object(bot.time, 'monotonic', side_effect=clock.monotonic))
                stack.enter_context(patch.object(bot.time, 'sleep', side_effect=clock.advance))
                stack.enter_context(patch.object(bot.sys, 'argv', ['bot.py', '--steps', str(steps)]))
                stack.enter_context(contextlib.redirect_stdout(output))
                try:
                    bot.main()
                except Exception as exc:
                    return state, output.getvalue(), exc, clock.now
        return state, output.getvalue(), None, clock.now

    def assert_cycle_metrics(self, output, outcome, elapsed):
        rows = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
        metrics = [row['cycle_metrics'] for row in rows if 'cycle_metrics' in row]
        self.assertEqual(len(metrics), 1)
        cycle = metrics[0]
        self.assertEqual(cycle['outcome'], outcome)
        self.assertAlmostEqual(cycle['total_s'], elapsed, places=6)
        self.assertGreaterEqual(cycle['other_s'], 0)
        self.assertTrue(all(value >= 0 for value in cycle['seconds'].values()))
        self.assertAlmostEqual(sum(cycle['seconds'].values()) + cycle['other_s'], elapsed, places=6)
        return cycle

    def test_incomplete_crop_retries_full_frame_once_then_confirms(self):
        state, _, error, _ = self.run_cycle(cropped=True, bad_crop=True)
        self.assertIsNone(error)
        self.assertEqual((state['crop'], state['captures'], state['gestures']), (1, 3, 1))

    def test_focus_change_after_search_never_sends_gesture(self):
        state, output, error, elapsed = self.run_cycle(focus_change=True)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn('фокус', str(error))
        self.assertEqual(state['gestures'], 0)
        cycle = self.assert_cycle_metrics(output, 'error', elapsed)
        self.assertAlmostEqual(cycle['seconds']['focus'], .04)
        self.assertAlmostEqual(cycle['seconds']['choose'], .07)
        self.assertNotIn('gesture', cycle['seconds'])
        self.assertNotIn('post_move_wait', cycle['seconds'])

    def test_capture_timeout_or_exit_error_uses_one_full_frame_fallback(self):
        for error in (subprocess.TimeoutExpired('adb', 20), subprocess.CalledProcessError(1, 'adb')):
            with self.subTest(error=type(error).__name__):
                state, _, failure, _ = self.run_cycle(cropped=True, crop_error=error)
                self.assertIsNone(failure)
                self.assertEqual((state['crop'], state['captures'], state['gestures']), (1, 3, 1))

    def test_stop_during_search_or_last_focus_never_sends_gesture(self):
        for when in ('stop_in_choose', 'stop_in_focus'):
            with self.subTest(when=when):
                state, output, error, elapsed = self.run_cycle(**{when: True})
                self.assertIsNone(error)
                self.assertEqual(state['gestures'], 0)
                cycle = self.assert_cycle_metrics(output, 'stopped', elapsed)
                self.assertAlmostEqual(cycle['seconds']['choose'], .07)
                self.assertAlmostEqual(cycle['seconds']['focus'], .02 if when == 'stop_in_choose' else .04)
                self.assertNotIn('gesture', cycle['seconds'])
                self.assertNotIn('post_move_wait', cycle['seconds'])

    def test_stale_and_transient_frames_do_not_repeat_gesture(self):
        actual = (2, 1) + (0,) * 14
        transient = (5, 1) + (0,) * 14
        frames = [self.board, self.board, self.board, self.board, transient, actual, actual]
        state, _, error, _ = self.run_cycle(boards=frames, steps=2)
        self.assertIsNone(error)
        self.assertEqual((state['captures'], state['gestures']), (7, 2))

    def test_repeated_bad_observations_are_bounded_and_never_move(self):
        state, output, error, elapsed = self.run_cycle(cropped=True, bad_crop=True, read_error=RuntimeError('bad full frame'))
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(state['gestures'], 0)
        self.assertLessEqual(state['captures'], 17)
        self.assertEqual(state['crop'], 1)
        cycle = self.assert_cycle_metrics(output, 'error', elapsed)
        self.assertEqual(cycle['counts']['observations'], 15)
        self.assertEqual(cycle['counts']['failed_observations'], 15)
        self.assertAlmostEqual(cycle['seconds']['capture'], .03 * state['captures'])
        self.assertAlmostEqual(cycle['seconds']['confirm_wait'], .15 * 14)
        self.assertNotIn('choose', cycle['seconds'])
        self.assertNotIn('gesture', cycle['seconds'])

    def test_failed_diagnostic_write_does_not_block_full_frame_recovery(self):
        errors = [OSError(errno.ENOSPC, 'disk full'), OSError(errno.EROFS, 'read-only'),
                  IsADirectoryError(errno.EISDIR, 'path is a directory')]
        for failure in errors:
            with self.subTest(error=failure.errno), patch.object(Path, 'write_bytes', side_effect=failure):
                state, output, error, elapsed = self.run_cycle(cropped=True, read_errors=[RuntimeError('bad crop')])
                self.assertIsNone(error)
                self.assertEqual((state['captures'], state['gestures']), (3, 1))
                self.assertIn('bad crop', output)
                self.assertIn('diagnostic_error', output)
                self.assertIn(str(failure), output)
                cycle = self.assert_cycle_metrics(output, 'moved', elapsed)
                self.assertEqual(cycle['counts']['diagnostic_write_errors'], 1)
                self.assertEqual(cycle['counts']['full_frame_fallbacks'], 1)

    def test_failed_diagnostic_write_and_bad_full_frame_preserve_cause_and_never_move(self):
        with patch.object(Path, 'write_bytes', side_effect=OSError(errno.ENOSPC, 'disk full')):
            state, output, error, elapsed = self.run_cycle(cropped=True, read_error=RuntimeError('unreadable frame'))
        self.assertIsInstance(error, RuntimeError)
        self.assertIn('unreadable frame', str(error))
        self.assertEqual(state['gestures'], 0)
        self.assertEqual(state['captures'], 16)
        self.assertIn('diagnostic_error', output)
        self.assertIn('unreadable frame', output)
        cycle = self.assert_cycle_metrics(output, 'error', elapsed)
        self.assertEqual(cycle['counts']['diagnostic_write_errors'], 16)
        self.assertEqual(cycle['counts']['full_frame_fallbacks'], 1)
        self.assertNotIn('choose', cycle['seconds'])
        self.assertNotIn('gesture', cycle['seconds'])

    def test_cycle_metrics_partition_full_steady_path_and_keep_legacy_fields(self):
        state, output, error, elapsed = self.run_cycle()
        self.assertIsNone(error)
        rows = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
        step = next(row for row in rows if 'board' in row)
        self.assertAlmostEqual(step['capture_s'], .05)
        self.assertAlmostEqual(step['think_s'], .33)
        cycle = next(row['cycle_metrics'] for row in rows if 'cycle_metrics' in row)
        self.assertEqual(cycle['outcome'], 'moved')
        self.assertAlmostEqual(cycle['total_s'], elapsed, places=6)
        self.assertAlmostEqual(sum(cycle['seconds'].values()) + cycle['other_s'], cycle['total_s'], places=6)
        self.assertAlmostEqual(cycle['seconds']['confirm_wait'], .15)
        self.assertAlmostEqual(cycle['seconds']['post_move_wait'], .4)
        self.assertAlmostEqual(cycle['seconds']['capture'], .06)
        self.assertAlmostEqual(cycle['seconds']['recognize'], .08)
        self.assertAlmostEqual(cycle['seconds']['focus'], .04)
        self.assertAlmostEqual(cycle['seconds']['choose'], .07)
        self.assertEqual(cycle['counts']['captures'], 2)
        self.assertEqual(state['focus'], 2)
        self.assertEqual(state['decodes'], 1)  # Подставной read_board: прежний geometry fallback.

    def test_ocr_timeout_and_exit_errors_become_bounded_observation_errors(self):
        for exc in (subprocess.TimeoutExpired('tesseract', 3), subprocess.CalledProcessError(1, 'tesseract')):
            with self.subTest(error=type(exc).__name__):
                with patch.object(bot, 'decode_screen', return_value=Image.new('RGB', (1440, 3040))), \
                     patch.object(bot, 'icon_level', return_value=None), \
                     patch.object(bot, 'two_digit_level', return_value=None), \
                     patch.object(bot, 'mask', return_value=np.full((40, 32), 255, dtype=np.uint8)), \
                     patch.object(bot, 'TEMPLATES', {1: np.zeros((40, 32)), 2: np.zeros((40, 32))}), \
                     patch.object(bot.subprocess, 'run', side_effect=exc) as run:
                    with self.assertRaisesRegex(RuntimeError, 'OCR'):
                        bot.read_board(b'frame')
                    self.assertGreater(run.call_args.kwargs.get('timeout', 0), 0)
                    self.assertLessEqual(run.call_args.kwargs['timeout'], 5.)

    def test_nested_ocr_time_is_not_counted_twice_in_recognition(self):
        _, output, error, elapsed = self.run_cycle(nested_ocr=True)
        self.assertIsNone(error)
        rows = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
        cycle = next(row['cycle_metrics'] for row in rows if 'cycle_metrics' in row)
        self.assertAlmostEqual(cycle['seconds']['recognize'], .04)
        self.assertAlmostEqual(cycle['seconds']['ocr'], .04)
        self.assertEqual(cycle['counts']['ocr_calls'], 2)
        self.assertAlmostEqual(sum(cycle['seconds'].values()) + cycle['other_s'], elapsed, places=6)

    def test_confirmed_fixture_reuses_source_geometry_without_third_decode(self):
        fixture = (bot.ROOT / 'fixtures/saved-board.png').read_bytes()
        expected = [0, 0, 0, 0, 1, 0, 0, 0, 5, 1, 0, 1, 7, 3, 3, 2]
        for dimensions in ((1440, 3040), (1441, 3041)):
            with self.subTest(dimensions=dimensions):
                data = fixture
                if dimensions != (1440, 3040):
                    frame = Image.open(io.BytesIO(fixture)).resize(dimensions)
                    data = io.BytesIO()
                    frame.save(data, format='PNG')
                    data = data.getvalue()
                state, output, error, _ = self.run_cycle(frame_bytes=data)
                self.assertIsNone(error)
                self.assertEqual((state['captures'], state['decodes'], state['gestures']), (2, 2, 1))
                rows = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
                self.assertEqual(next(row['board'] for row in rows if 'board' in row), expected)
                w, h = dimensions
                x1, y1, x2, y2 = round(.77*w), round(.58*h), round(.28*w), round(.58*h)
                self.assertEqual(state['gesture_command'],
                                 f'input motionevent DOWN {x1} {y1}; input motionevent MOVE {x2} {y2}; input motionevent UP {x2} {y2}')


if __name__ == '__main__':
    unittest.main()
