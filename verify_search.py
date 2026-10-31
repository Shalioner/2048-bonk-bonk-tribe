"""Differential regression checks against a separately built baseline engine."""
import argparse
import ctypes as C
import random
from search_benchmark import load


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline', required=True)
    ap.add_argument('--candidate', required=True)
    args = ap.parse_args()
    rng = random.Random(809)
    models = [
        [[9, 1] + [0]*13 for _ in range(16)],
        [[0, 0, 1, 0, 4] + [0]*10 for _ in range(16)],
        [[rng.randrange(1, 10) for _ in range(15)] for _ in range(16)],
        [[rng.randrange(0, 10) if i % 3 == s % 3 else 0 for i in range(15)] for s in range(16)],
    ]
    checked = 0
    for table in models:
        libs = [load(p, table) for p in (args.baseline, args.candidate)]
        for lib in libs:
            lib.solve_levels.argtypes = [C.c_uint64, C.c_int, C.c_int]
            lib.solve_levels.restype = C.c_int
        for _ in range(50):
            ranks = [rng.randrange(16) if rng.random() < .85 else 0 for _ in range(16)]
            board = sum(v << (4*i) for i, v in enumerate(ranks))
            depth = rng.choice((1, 2, 3))
            threshold = rng.choice((1e-4, 1e-5, 1e-6))
            scores = [[lib.score_levels_thresh(board, d, depth, threshold) for d in range(4)] for lib in libs]
            if scores[0] != scores[1]:
                raise RuntimeError((hex(board), depth, threshold, scores))
            mask = rng.randrange(16)
            moves = [lib.solve_levels(board, mask, depth) for lib in libs]
            if moves[0] != moves[1]:
                raise RuntimeError((hex(board), mask, moves))
            checked += 4
        # Failed model update must preserve both probabilities and cached active ranks.
        invalid = (C.c_float * 240)(*([-1.] * 240))
        if not all(lib.set_spawn_table(invalid, 16, 15) == -1 for lib in libs):
            raise RuntimeError('Invalid model accepted')
        board = 0x123456789a000000
        if libs[0].score_levels_thresh(board, 0, 3, 1e-4) != libs[1].score_levels_thresh(board, 0, 3, 1e-4):
            raise RuntimeError('Parity after invalid model update')
    print(f'Exact parity: {checked} per-direction scores, 200 allowed-mask decisions, 4 models; depths 1–3, thresholds 1e-4/1e-5/1e-6.')


if __name__ == '__main__':
    main()
