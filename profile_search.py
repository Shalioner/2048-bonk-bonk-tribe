"""Fixed-work load for an external sampler; timings under sampling are not benchmarks."""
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time

from search_benchmark import load


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--engine', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--seconds', type=float, default=20.)
    ap.add_argument('--board', action='append')
    ap.add_argument('--threshold', type=float, default=1e-4)
    ap.add_argument('--mode', choices=('scores', 'budget'), default='budget')
    args = ap.parse_args()
    if args.seconds <= 0:
        ap.error('--seconds must be positive')
    model = json.loads(Path('spawn-model.default.json').read_text())['stages']
    table = [model[str(max(int(k) for k in model if int(k) <= stage))] for stage in range(16)]
    boards = [int(b, 16) for b in (args.board or
              ['1211413353017100', '423054209511a622', '8741653154213210'])]
    binary_hash = hashlib.sha256(Path(args.engine).read_bytes()).hexdigest()
    lib = load(args.engine, table)
    print(json.dumps(dict(pid=os.getpid(), engine=args.engine, mode=args.mode)), flush=True)
    start, cpu_start = time.perf_counter(), time.process_time()
    calls = 0
    references = {}
    while time.perf_counter() - start < args.seconds:
        board = boards[calls % len(boards)]
        allowed = sum(1 << d for d in range(4) if lib.execute_move(d, board) != board)
        if args.mode == 'scores':
            result = [lib.score_levels_thresh(board, d, 8, args.threshold) for d in range(4)]
        else:
            info = (C.c_double * 8)()
            move = lib.solve_budget(board, allowed, 8, (C.c_float * 1)(args.threshold),
                                    1, 60., 5., info)
            if info[0] != 1 or info[4] or info[7]:
                raise RuntimeError(f'Incomplete profiling pass: {list(info)}')
            result = dict(move=move, positions=int(info[2]))
        if board in references and references[board] != result:
            raise RuntimeError(f'Non-deterministic fixed-work result: {board:016x}')
        references[board] = result
        calls += 1
    result = dict(engine=args.engine, binary_sha256=binary_hash, command=sys.argv,
                  mode=args.mode, depth=8, threshold=args.threshold, calls=calls,
                  wall_seconds=time.perf_counter()-start, cpu_seconds=time.process_time()-cpu_start,
                  peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  peak_rss_unit='bytes' if sys.platform == 'darwin' else 'KiB',
                  results={f'{b:016x}': r for b, r in references.items()},
                  note='Sampler workload only; do not use these times as unprofiled benchmarks.')
    Path(args.out).write_text(json.dumps(result, indent=2)+'\n')
    if binary_hash != hashlib.sha256(Path(args.engine).read_bytes()).hexdigest():
        raise RuntimeError('Engine file changed during profiling')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
