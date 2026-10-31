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
import random
import statistics
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


def corpus(lib, table):
    rng = random.Random(2048)
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
    ap.add_argument('--mode', choices=('scores', 'budget'), default='scores')
    ap.add_argument('--limit', type=int, default=0, help='limit corpus size for smoke tests')
    ap.add_argument('--depth', type=int, default=8)
    ap.add_argument('--threshold', type=float, default=1e-4)
    args = ap.parse_args()
    model = json.loads(Path('spawn-model.default.json').read_text())['stages']
    table = [model[str(max(int(k) for k in model if int(k) <= stage))] for stage in range(16)]
    libs = [load(p, table) for p in (args.baseline, args.candidate)]
    boards = [int(b, 16) for b in args.board] if args.board else corpus(libs[0], table)
    if args.limit:
        boards = boards[:args.limit]
    rows = []
    for index, board in enumerate(boards):
        timings = [[], []]
        reference = None
        for repeat in range(args.repeats):
            for which in ((0, 1) if repeat % 2 == 0 else (1, 0)):
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
                    move = lib.solve_budget(board, allowed, args.depth, (C.c_float * 1)(args.threshold),
                                            1, 60., 5., info)
                    elapsed = time.perf_counter()-start
                    if info[0] != 1 or info[4] or info[7]:
                        raise RuntimeError(f'Incomplete depth-{args.depth} pass: {list(info)}')
                    scores = None
                timings[which].append(elapsed)
                if reference is None:
                    reference = (scores, move)
                if (scores, move) != reference:
                    raise RuntimeError(f'Parity mismatch: {hex(board)}, engine={which}, {reference}, {scores}, {move}')
        rows.append(dict(board=f'{board:016x}', scores=reference[0], move=reference[1], seconds=timings))
        print(index, hex(board), [round(statistics.median(v), 5) for v in timings], flush=True)
    aggregates = [stats([v for r in rows for v in r['seconds'][i]]) for i in range(2)]
    position_stats = [stats([statistics.median(r['seconds'][i]) for r in rows]) for i in range(2)]
    result = dict(mode=args.mode, depth=args.depth, threshold=args.threshold, repeats=args.repeats,
                  hashes={p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                          for p in (args.baseline, args.candidate)},
                  summary=aggregates, per_position_medians=position_stats, rows=rows,
                  exact_parity=True, score_parity=args.mode == 'scores')
    Path(args.out).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(aggregates), flush=True)


if __name__ == '__main__':
    main()
