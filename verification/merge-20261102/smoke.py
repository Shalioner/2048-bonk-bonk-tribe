"""Exercise merged launchers with real fixture recognition/native search, fake ADB only."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import bot
import play_search_alternative as entry


def main():
    fixture = (ROOT / 'fixtures/level-ten.png').read_bytes()
    expected = (10,3,2,1,2,1,1,0,0,2,0,0,0,0,0,0)
    results = []
    original = (bot.choose, bot.NATIVE, bot.THRESHOLDS, sys.argv)
    for profile in ('progressive', 'merged_banked_endgame'):
        calls = []
        def adb(argv, **kwargs):
            calls.append(argv)
            assert argv[0] == 'adb', argv
            cmd = argv[1:]
            if cmd == ['shell', 'wm', 'size']:
                return SimpleNamespace(stdout=b'Physical size: 100x100')  # select full-frame path
            if cmd == ['shell', 'dumpsys', 'window']:
                return SimpleNamespace(stdout=f'mCurrentFocus {bot.PACKAGE}'.encode())
            if cmd == ['exec-out', 'screencap']:
                return SimpleNamespace(stdout=fixture)
            raise AssertionError(f'Unexpected external command/gesture: {argv}')
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(bot, 'ROOT', Path(directory)), \
                 patch.object(bot, 'SPAWN_LEARNED', Path(directory) / 'spawn-model.json'), \
                 patch.object(bot.subprocess, 'run', side_effect=adb), redirect_stdout(output):
                if profile == 'progressive':
                    with patch.object(sys, 'argv', ['bot.py', '--dry-run', '--steps', '1', '--think', '.01']):
                        bot.main()
                else:
                    entry.main(['--merged-engine', '--dry-run', '--steps', '1'])
            assert (Path(directory) / 'latest.png').exists()
        rows = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{')]
        move = next(row for row in rows if 'direction' in row)
        assert tuple(move['board']) == expected
        direction = ['left', 'right', 'up', 'down'].index(move['direction'])
        assert direction in dict(bot.legal_moves(expected))
        assert move['search']['policy'] == 'progressive'
        metrics = next(row['cycle_metrics'] for row in rows if 'cycle_metrics' in row)
        assert metrics['outcome'] == 'dry_run'
        assert metrics['counts']['captures'] == 2
        if profile == 'merged_banked_endgame':
            assert move['search']['budget_profile'] == profile
            assert rows[0]['confirmed_improvement'] is False
        assert all(a is b for a, b in zip(original, (bot.choose, bot.NATIVE, bot.THRESHOLDS, sys.argv)))
        results.append(dict(profile=profile, move=move, metrics=metrics, adb_calls=len(calls), gestures=0))
    print(json.dumps(dict(ok=True, real_native=True, real_recognition=True, fake_adb=True, results=results), indent=2))


if __name__ == '__main__':
    main()
