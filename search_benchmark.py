"""Paired fixed-depth timings and exact float/move parity, without a phone.

Build baseline and candidate with the same compiler flags. Example:
python3 search_benchmark.py --baseline vendor/baseline.dylib --candidate vendor/engine.dylib --out search-results.json
"""
import argparse
import ctypes as C
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
import sys
import time


def load(path, table):
    lib = C.CDLL(str(Path(path).resolve()))
    lib.init_tables()
    lib.execute_move.argtypes = [C.c_int, C.c_uint64]
    lib.execute_move.restype = C.c_uint64
    lib.score_levels_thresh.argtypes = [C.c_uint64, C.c_int, C.c_int, C.c_float]
    lib.score_levels_thresh.restype = C.c_float
    lib.set_spawn_table.argtypes = [C.POINTER(C.c_float), C.c_int, C.c_int]
    if lib.set_spawn_table((C.c_float * 240)(*sum(table, [])), 16, 15) != 0:
        raise ValueError('Invalid spawn model')
    lib.solve_budget.argtypes = [C.c_uint64, C.c_int, C.c_int, C.POINTER(C.c_float),
                                 C.c_int, C.c_double, C.c_double, C.POINTER(C.c_double)]
    lib.solve_budget.restype = C.c_int
    return lib


def corpus(lib, table, seed=2048):
    rng = random.Random(seed)
    board = 0
    boards = []
    for step in range(1600):
        legal = [(d, lib.execute_move(d, board)) for d in range(4)
                 if lib.execute_move(d, board) != board]
        if not legal:
            board = 0
        else:
            # A shallow policy makes this corpus quick to reproduce independently of candidate.
            _, board = max(legal, key=lambda x: lib.score_levels_thresh(board, x[0], 1, 1e-4))
        empty = [i for i in range(16) if not (board >> (4*i)) & 15]
        if empty:
            stage = max((board >> (4*i)) & 15 for i in range(16))
            rank = rng.choices(range(1, 16), table[stage])[0]
            board |= rank << (4*rng.choice(empty))
        if step in (10, 30, 60, 100, 160, 220, 300, 400, 500, 650, 800, 1000, 1200, 1400, 1599):
            boards.append(board)
    # Crowded endgame, cap barriers, near-goal and terminal, ranks above cap.
    boards += [int(s, 16) for s in ('123423453456456b', '11223344556677bb',
                                    '123456789abbb000', '123456789abcdef0',
                                    '0000000000000011', '1234123412341234')]
    return boards


def stats(values):
    return dict(median=statistics.median(values), p95=sorted(values)[math.ceil(.95*len(values))-1],
                maximum=max(values))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline', required=True)
    ap.add_argument('--candidate', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--repeats', type=int, default=3)
    ap.add_argument('--board', action='append', help='override corpus with packed hexadecimal boards')
    ap.add_argument('--mode', choices=('scores', 'budget', 'policy'), default='scores',
                    help='policy: legacy solve_budget with three thresholds (not the progressive CLI default)')
    ap.add_argument('--limit', type=int, default=0, help='limit corpus size for smoke tests')
    ap.add_argument('--depth', type=int, default=8)
    ap.add_argument('--threshold', type=float, default=1e-4)
    ap.add_argument('--seed', type=int, default=2048, help='trajectory seed; use a separate seed for PGO holdout')
    args = ap.parse_args()
    if args.repeats < 1:
        ap.error('--repeats must be positive')
    if args.mode == 'policy':
        from bot import DEFAULT_DEPTH, DEFAULT_THINK, GROWTH, THRESHOLDS
        if args.depth != DEFAULT_DEPTH or args.threshold != 1e-4:
            ap.error('policy mode measures legacy scheduling at the default depth and thresholds')
        thresholds, budget, growth = THRESHOLDS, DEFAULT_THINK, GROWTH
    else:
        thresholds, budget, growth = (args.threshold,), 60., 5.
    paths = (args.baseline, args.candidate)
    input_hashes = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}
    benchmark_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    model_bytes = Path('spawn-model.default.json').read_bytes()
    model = json.loads(model_bytes)['stages']
    table = [model[str(max(int(k) for k in model if int(k) <= stage))] for stage in range(16)]
    libs = [load(p, table) for p in (args.baseline, args.candidate)]
    boards = [int(b, 16) for b in args.board] if args.board else corpus(libs[0], table, args.seed)
    if args.limit:
        boards = boards[:args.limit]
    rows = []
    for index, board in enumerate(boards):
        timings = [[], []]
        details = [[], []]
        reference = None
        moves_by_work = {}
        for repeat in range(args.repeats):
            for which in ((0, 1) if (index + repeat) % 2 == 0 else (1, 0)):
                lib = libs[which]
                allowed = sum(1 << d for d in range(4) if lib.execute_move(d, board) != board)
                start = time.perf_counter()
                if args.mode == 'scores':
                    scores = [lib.score_levels_thresh(board, d, args.depth, args.threshold) for d in range(4)]
                    elapsed = time.perf_counter()-start
                    legal = [d for d in range(4) if allowed & (1 << d) and scores[d] > -1]
                    move = max(legal, key=lambda d: scores[d], default=-1)
                else:
                    info = (C.c_double * 8)()
                    # bot.choose returns immediately when the board has no legal move.
                    if args.mode == 'policy' and not allowed:
                        move = -1
                    else:
                        move = lib.solve_budget(board, allowed, args.depth,
                                                (C.c_float * len(thresholds))(*thresholds),
                                                len(thresholds), budget, growth, info)
                    elapsed = time.perf_counter()-start
                    if args.mode == 'budget' and (info[0] != 1 or info[4] or info[7]):
                        raise RuntimeError(f'Incomplete depth-{args.depth} pass: {list(info)}')
                    scores = None
                    details[which].append(dict(move=move, passes=int(info[0]), threshold=info[1],
                                               positions=int(info[2]), native_seconds=info[3],
                                               cut=bool(info[4]), skipped=bool(info[5]),
                                               last_pass_seconds=info[6], fallback=bool(info[7])))
                timings[which].append(elapsed)
                if args.mode == 'policy':
                    # Different completed thresholds mean different work. Never assert their moves equal.
                    work = (info[0], info[1], info[7])
                    if work in moves_by_work and move != moves_by_work[work]:
                        raise RuntimeError(f'Equal completed work changed move: {hex(board)}, {work}')
                    moves_by_work[work] = move
                    continue
                if reference is None:
                    reference = (scores, move, int(info[2]) if args.mode == 'budget' else None)
                current = (scores, move, int(info[2]) if args.mode == 'budget' else None)
                if current != reference:
                    raise RuntimeError(f'Parity mismatch: {hex(board)}, engine={which}, {reference}, {scores}, {move}')
        rows.append(dict(board=f'{board:016x}', scores=reference[0] if reference else None,
                         move=reference[1] if reference else None, seconds=timings, details=details))
        print(index, hex(board), [round(statistics.median(v), 5) for v in timings], flush=True)
    aggregates = [stats([v for r in rows for v in r['seconds'][i]]) for i in range(2)]
    position_stats = [stats([statistics.median(r['seconds'][i]) for r in rows]) for i in range(2)]
    result = dict(mode=args.mode, search_policy="legacy" if args.mode == "policy" else None,
                  depth=args.depth, threshold=args.threshold, repeats=args.repeats,
                  seed=args.seed,
                  thresholds=thresholds, budget_seconds=budget if args.mode != 'scores' else None,
                  growth=growth if args.mode != 'scores' else None,
                  hashes=input_hashes,
                  hashes_after={p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths},
                  model_sha256=hashlib.sha256(model_bytes).hexdigest(),
                  benchmark_sha256=benchmark_hash,
                  command=sys.argv, platform=platform.platform(),
                  summary=aggregates, per_position_medians=position_stats, rows=rows,
                  exact_parity=True if args.mode != 'policy' else None,
                  equal_completed_work_move_parity=True, score_parity=args.mode == 'scores')
    Path(args.out).write_text(json.dumps(result, indent=2)+'\n')
    if result['hashes'] != result['hashes_after']:
        raise RuntimeError('Engine file changed during measurement; results retained but invalid')
    print(json.dumps(aggregates), flush=True)


if __name__ == '__main__':
    main()
