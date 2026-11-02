"""Isolated reproducible Bonk probes; no ADB, third-party libraries or runtime writes."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
WORK = ROOT.parents[1]
sys.path.insert(0, str(WORK))
PLAN = json.loads((ROOT / "plan.json").read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new)


def source(params):
    s = (WORK / "experiments/strategy/baseline.cpp").read_text()
    # Identical diagnostics in every variant, single-threaded fixed-depth calls only.
    s = replace_once(s, "static float score_move_node(eval_state &state, board_t board, float cprob) {",
        "static unsigned long long lab_clips=0, lab_nodes=0;\nstatic float score_move_node(eval_state &state, board_t board, float cprob) {")
    s = replace_once(s, "    float best = 0.0f;\n    state.curdepth++;",
        "    float best = 0.0f; bool legal=false, nonnegative=false;\n    state.curdepth++;")
    s = replace_once(s, "            best = std::max(best, score_tilechoose_node(state, newboard, cprob));",
        "            legal=true; lab_nodes++;\n            float child = score_tilechoose_node(state, newboard, cprob);\n            if(child >= 0) nonnegative=true;\n            best = std::max(best, child);")
    s = replace_once(s, "    state.curdepth--;\n\n    return best;",
        "    state.curdepth--;\n    if(legal && !nonnegative) lab_clips++;\n    return best;")
    term = "0.0f"
    if "progress" in params:
        term = f"{params['progress']}.0f * count_cap(board)"
    if "mobility" in params:
        term = f"0.0f; if(count_cap(board)==2) for(int d=0;d<4;d++) if(execute_move(d,board)!=board) bonus += {params['mobility']}.0f"
    s = replace_once(s, "    return score_helper(          board , heur_score_table) +",
        f"    float bonus = {term};\n    return bonus + score_helper(          board , heur_score_table) +")
    s += '\nextern "C" float lab_heur(board_t b) { return score_heur_board(b); }\nextern "C" unsigned long long lab_clip_count() { return lab_clips; }\nextern "C" unsigned long long lab_node_count() { return lab_nodes; }\nextern "C" void lab_reset() { lab_clips=0; lab_nodes=0; }\n'
    return s


def build(name):
    folder = ROOT / name
    folder.mkdir(exist_ok=True)
    cpp = folder / "engine.cpp"
    cpp.write_text(source(PLAN["variants"][name]))
    binary = ROOT / "build" / name / ("engine.dylib" if sys.platform == "darwin" else "engine.so")
    binary.parent.mkdir(parents=True, exist_ok=True)
    flags = ["clang++", "-O3", "-std=c++11", "-dynamiclib"] if sys.platform == "darwin" else ["g++", "-O3", "-std=c++11", "-shared", "-fPIC", "-pthread"]
    subprocess.run(flags + ["-I", str(WORK / "vendor"), str(cpp), "-o", str(binary)], check=True)
    manifest = dict(variant=name, parameters=PLAN["variants"][name], baseline_commit=PLAN["baseline_commit"],
                    cpp_sha256=sha(cpp), engine_sha256=sha(binary), runner_sha256=sha(Path(__file__)),
                    baseline_cpp_sha256=sha(WORK / "experiments/strategy/baseline.cpp"))
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def load(name):
    import bot
    binary = ROOT / "build" / name / ("engine.dylib" if sys.platform == "darwin" else "engine.so")
    bot.NATIVE = bot.load_engine(binary)
    bot.NATIVE.lab_heur.argtypes = [ctypes.c_uint64]
    bot.NATIVE.lab_heur.restype = ctypes.c_float
    bot.NATIVE.lab_clip_count.restype = ctypes.c_ulonglong
    bot.NATIVE.lab_node_count.restype = ctypes.c_ulonglong
    return bot


def play(name, seed, table):
    import benchmark
    bot = sys.modules["bot"]
    benchmark.apply_table(table)
    bot.NATIVE.lab_reset()
    rng = random.Random(seed)
    board = benchmark.spawn(benchmark.spawn((0,) * 16, rng, table), rng, table)
    started = time.monotonic()
    firsts, snapshots, counts = {}, {}, {}
    params = PLAN["variants"][name]
    moves = 0
    while moves < PLAN["max_moves"] and not bot.reached_goal(board):
        focused = "focus_empty" in params and board.count(bot.CAP) >= 2 and board.count(0) <= params["focus_empty"]
        depth = PLAN["focus_depth"] if focused else PLAN["depth"]
        direction = benchmark.choose_fixed(board, depth)
        if direction is None:
            break
        counts[str(depth)] = counts.get(str(depth), 0) + 1
        board = benchmark.spawn(bot.move(board, direction), rng, table)
        moves += 1
        caps = board.count(bot.CAP)
        if caps and str(caps) not in firsts:
            firsts[str(caps)] = moves
            snapshots[str(caps)] = list(board)
    result = "goal" if bot.reached_goal(board) else "loss" if not bot.legal_moves(board) else "limit"
    return dict(seed=seed, depth=PLAN["depth"], think=None, endgame_depth=None, result=result,
                moves=moves, caps=board.count(bot.CAP), max=max(board), board=list(board),
                cap_reached_at=firsts, cap_boards=snapshots, depth_counts=counts,
                clipped_nodes=bot.NATIVE.lab_clip_count(), nodes=bot.NATIVE.lab_node_count(),
                seconds=round(time.monotonic() - started, 4))


def run(name, stage, resume):
    import benchmark
    bot = load(name)
    low, high = PLAN["training_seeds" if stage == "training" else "holdout_seeds"]
    config = dict(games=high-low+1, seed=low, depths=[PLAN["depth"]], think=None,
                  max_moves=PLAN["max_moves"], endgame_depth=None)
    model = bot.default_spawn_model()
    table = bot.spawn_table(model)
    sources = benchmark.source_hashes()
    sources.update(lab=sha(Path(__file__)), plan=sha(ROOT / "plan.json"), cpp=sha(ROOT / name / "engine.cpp"))
    out = ROOT / name / (stage + ".json")
    if out.exists() and not resume:
        raise ValueError("Existing output: use --resume; refusing overwrite")
    rows = benchmark.resume_rows(out, config, model, sources, table) if resume else []
    done = {row["seed"] for row in rows}
    for seed in range(low, high+1):
        if seed in done:
            continue
        row = play(name, seed, table)
        rows.append(row)
        data = benchmark.checkpoint(rows, config, model, sources, table)
        data["experiment"] = dict(variant=name, parameters=PLAN["variants"][name], manifest=json.loads((ROOT/name/"manifest.json").read_text()))
        benchmark.save_checkpoint(out, data)
        print(json.dumps(dict(variant=name, seed=seed, result=row["result"], seconds=row["seconds"])), flush=True)
    print(json.dumps(benchmark.summarize(rows, PLAN["depth"], None)), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["build", "run"])
    ap.add_argument("variant", choices=PLAN["variants"])
    ap.add_argument("--stage", choices=["training", "holdout"], default="training")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    build(args.variant) if args.action == "build" else run(args.variant, args.stage, args.resume)
