"""Frozen paired acceptance for exact-search optimization; no ADB or live game."""
import argparse
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import statistics
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'baseline'))
import bot
import benchmark
from search_benchmark import corpus, load, stats

PATHS = {
    'main': HERE / 'baseline/vendor/engine.dylib',
    'candidate': HERE / 'worktree/vendor/engine.dylib',
}
COMMITS = {'main': '08992d5', 'candidate': 'a8d0441'}
MODEL = bot.spawn_table(bot.default_spawn_model())
THRESHOLDS = (1e-4, 1e-5, 1e-6)
ARM_NAMES = tuple(PATHS)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, result):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def choose(lib, arm, board, budget):
    allowed = sum(1 << bot.DIRECTIONS[d] for d, _ in bot.legal_moves(board))
    if not allowed:
        return dict(direction=None, seconds=0., cpu_seconds=0., depth=0, threshold=0., fallback=False)
    info = (C.c_double * 10)()
    solver = lib.solve_budget if arm == 'old_main' else lib.solve_budget_progressive
    start, cpu = time.perf_counter(), time.process_time()
    direction = solver(bot.pack(board), allowed, 8, (C.c_float * 3)(*THRESHOLDS), 3, budget, 5., info)
    seconds, cpu_seconds = time.perf_counter() - start, time.process_time() - cpu
    if direction < 0 or not allowed & (1 << direction):
        raise AssertionError((arm, board, direction, allowed))
    return dict(direction=bot.DIRECTIONS.index(direction), seconds=seconds, cpu_seconds=cpu_seconds,
                depth=(1 if info[7] else 8) if arm == 'old_main' else int(info[8]),
                threshold=info[1], passes=int(info[0]), positions=int(info[2]),
                cut=bool(info[4]), skipped=bool(info[5]), fallback=bool(info[7]))


def fixed(libs, result, output):
    boards = corpus(libs['main'], MODEL, seed=810114)
    for index, board in enumerate(boards):
        if any(row['index'] == index for row in result['rows']):
            continue
        times = {arm: [] for arm in ARM_NAMES}
        scores = {}
        for repeat in range(3):
            order = list(ARM_NAMES)
            shift = (index + repeat) % len(order)
            for arm in order[shift:] + order[:shift]:
                started = time.perf_counter()
                values = [libs[arm].score_levels_thresh(board, d, 8, 1e-4) for d in range(4)]
                times[arm].append(time.perf_counter() - started)
                if arm in scores:
                    assert scores[arm] == values
                scores[arm] = values
            assert scores['main'] == scores['candidate'], (index, scores)
        result['rows'].append(dict(index=index, board=f'{board:016x}', seconds=times, scores=scores))
        save(output, result)
        print('fixed', index, {k: round(statistics.median(v), 6) for k, v in times.items()}, flush=True)
    result['summary'] = {arm: stats([v for row in result['rows'] for v in row['seconds'][arm]]) for arm in ARM_NAMES}
    result['exact_candidate_parity'] = True


def policy(libs, result, output):
    boards = corpus(libs['main'], MODEL, seed=2048 if result['stage']=='screen' else 810114)
    # First ten legal, nonterminal positions in the predeclared corpus, not selected by timing/outcome.
    selected = [(i, b) for i, b in enumerate(boards)
                if not bot.reached_goal(tuple((b >> (4 * j)) & 15 for j in range(16)))
                and any(libs['main'].execute_move(d, b) != b for d in range(4))][:10]
    if result['stage']=='screen':
        selected=[(i,boards[i]) for i in (0,3,7,16)]
    for index, packed in selected:
        board = tuple((packed >> (4 * j)) & 15 for j in range(16))
        for repeat in range(1 if result['stage']=='screen' else 2):
            if any(row['index'] == index and row['repeat'] == repeat for row in result['rows']):
                continue
            order = list(ARM_NAMES)
            shift = (index + repeat) % len(order)
            values = {arm: choose(libs[arm], arm, board, 10.) for arm in order[shift:] + order[:shift]}
            result['rows'].append(dict(index=index, repeat=repeat, board=f'{packed:016x}', engines=values))
            save(output, result)
            print('policy', index, repeat, {arm: (round(v['seconds'], 4), v['depth'], v['threshold']) for arm, v in values.items()}, flush=True)
    result['summary'] = {arm: stats([row['engines'][arm]['seconds'] for row in result['rows']]) for arm in ARM_NAMES}
    matched = [row for row in result['rows'] if
               (row['engines']['main']['depth'], row['engines']['main']['threshold']) ==
               (row['engines']['candidate']['depth'], row['engines']['candidate']['threshold'])]
    for row in matched:
        assert row['engines']['main']['direction'] == row['engines']['candidate']['direction'], row
    result['same_completed_work'] = dict(count=len(matched),
        summary={arm: stats([row['engines'][arm]['seconds'] for row in matched])
                 for arm in ('main', 'candidate')} if matched else {})


screen = policy


def game(lib, arm, seed):
    rng = random.Random(seed)
    board = benchmark.spawn(benchmark.spawn((0,) * 16, rng, MODEL), rng, MODEL)
    records = []
    start = time.perf_counter()
    while len(records) < 6000 and not bot.reached_goal(board):
        decision = choose(lib, arm, board, .01)
        if decision['direction'] is None:
            break
        records.append(decision)
        shifted = bot.move(board, decision['direction'])
        assert shifted != board
        board = benchmark.spawn(shifted, rng, MODEL)
    result = 'goal' if bot.reached_goal(board) else 'loss' if not bot.legal_moves(board) else 'limit'
    return dict(arm=arm, seed=seed, result=result, moves=len(records), final_board=board,
                seconds=time.perf_counter() - start, decisions=records)


def games(libs, result, output):
    for index, seed in enumerate(range(2026110320, 2026110340)):
        order = list(ARM_NAMES)
        shift = index % len(order)
        for arm in order[shift:] + order[:shift]:
            if any(row['seed'] == seed and row['arm'] == arm for row in result['rows']):
                continue
            row = game(libs[arm], arm, seed)
            result['rows'].append(row)
            save(output, result)
            print('game', seed, arm, row['result'], row['moves'], round(row['seconds'], 3), flush=True)
    result['summary'] = {}
    for arm in ARM_NAMES:
        rows = [row for row in result['rows'] if row['arm'] == arm]
        latencies = [move['seconds'] for row in rows for move in row['decisions']]
        result['summary'][arm] = dict(games=len(rows), goals=sum(row['result'] == 'goal' for row in rows),
            losses=sum(row['result'] == 'loss' for row in rows), limits=sum(row['result'] == 'limit' for row in rows),
            moves=sum(row['moves'] for row in rows), latency=stats(latencies),
            total_choose_seconds=sum(latencies),
            total_cpu_seconds=sum(move['cpu_seconds'] for row in rows for move in row['decisions']))
    pairs = {seed: {row['arm']: row['result'] == 'goal' for row in result['rows'] if row['seed'] == seed}
             for seed in range(2026110320, 2026110340)}
    result['paired'] = {}
    for baseline in ('main',):
        gained = sum(pair['candidate'] and not pair[baseline] for pair in pairs.values())
        lost = sum(pair[baseline] and not pair['candidate'] for pair in pairs.values())
        n = gained + lost
        p = sum(math.comb(n, k) for k in range(gained, n + 1)) / 2 ** n if n else 1.
        result['paired'][baseline] = dict(gained=gained, lost=lost, one_sided_p=p)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('screen', 'fixed', 'policy', 'games'), required=True)
    args = parser.parse_args()
    output = HERE / 'test-results' / (args.stage + '.json')
    output.parent.mkdir(exist_ok=True)
    fingerprints = dict(script=sha(Path(__file__)), model=sha(bot.SPAWN_DEFAULT),
                        sources={folder+'/'+name: sha(HERE/folder/name) for folder in ('baseline','worktree')
                                 for name in ('bot.py','benchmark.py','search_benchmark.py','vendor/engine_adapted.cpp')},
                        binaries={arm: sha(path) for arm, path in PATHS.items()}, commits=COMMITS)
    result = dict(stage=args.stage, fingerprints=fingerprints, complete=False, rows=[],
                  platform=platform.platform(), load_average=os.getloadavg(),
                  plan=dict(depth=8, fixed_threshold=1e-4, corpus_seed=2048 if args.stage == "screen" else 810114, fixed_repeats=3,
                            policy_budget=10., policy_repeats=1 if args.stage == "screen" else 2, game_budget=.01,
                            game_seeds=[2026110320, 2026110339], max_moves=6000,
                            win_scope='20-seed exploratory simulation at 10ms, not default 10s or phone win rate'))
    if output.exists():
        previous = json.loads(output.read_text())
        assert previous['fingerprints'] == fingerprints and previous['plan'] == result['plan'], 'checkpoint inputs changed'
        if previous['complete']:
            print('Already complete; no measurements repeated')
            return
        result = previous
    libs = {arm: load(path, MODEL) for arm, path in PATHS.items()}
    for arm, lib in libs.items():
        if arm != 'old_main':
            lib.solve_budget_progressive.argtypes = lib.solve_budget.argtypes
            lib.solve_budget_progressive.restype = C.c_int
    globals()[args.stage](libs, result, output)
    assert fingerprints['binaries'] == {arm: sha(path) for arm, path in PATHS.items()}
    assert fingerprints['sources'] == {folder+'/'+name: sha(HERE/folder/name) for folder in ('baseline','worktree')
                                 for name in ('bot.py','benchmark.py','search_benchmark.py','vendor/engine_adapted.cpp')}
    assert fingerprints['script'] == sha(Path(__file__))
    result['complete'] = True
    save(output, result)
    print(json.dumps(result['summary'], indent=2), flush=True)


if __name__ == '__main__':
    main()
