"""Frozen engine experiments; run from any directory, no ADB calls."""
import argparse, hashlib, json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
WORK = ROOT / "worktree"
VARIANTS = {
 "baseline": {},
 "goal4m": {"goal": 4000000},
 "goal10m": {"goal": 10000000},
 "edgecap": {"edge": 6000},
 "adjacentcap": {"adjacent": 10000},
 "highmerges": {"highmerges": True},
 "mono70": {"mono": 70},
 "endempty": {"endempty": 1620},
 "highmerges_soft": {"highmerges": True, "merge_start": 7},
 "combined": {"highmerges": True, "endempty": 1620},
 "endempty_half": {"endempty": 810},
 "endempty_double": {"endempty": 3240},
 "endempty_quarter": {"endempty": 405},
 "endempty_mono70": {"endempty": 1620, "mono": 70},
}

def source(params):
    s = (ROOT / "baseline.cpp").read_text()
    if "goal" in params:
        assert s.count("return 1e9f;") == 2
        s = s.replace("return 1e9f;", "return %.1ff;" % params["goal"])
    if "mono" in params:
        s = s.replace("SCORE_MONOTONICITY_WEIGHT = 47.0f", "SCORE_MONOTONICITY_WEIGHT = %.1ff" % params["mono"])
    if params.get("highmerges"):
        s = s.replace("merges += 1 + counter;", "merges += (1 + counter) * std::max(1, prev - %d);" % params.get("merge_start", 5))
    if "edge" in params:
        term = "float bonus = 0; for (int i = 0; i < 16; ++i) if (((board >> (4*i)) & 15) == CAP_RANK && (i/4 == 0 || i/4 == 3 || i%%4 == 0 || i%%4 == 3)) bonus += %.1ff;" % params["edge"]
    elif "adjacent" in params:
        term = "float bonus = 0; for (int i = 0; i < 16; ++i) if (((board >> (4*i)) & 15) == CAP_RANK) { if (i%%4 < 3 && ((board >> (4*(i+1))) & 15) == CAP_RANK) bonus += %.1ff; if (i/4 < 3 && ((board >> (4*(i+4))) & 15) == CAP_RANK) bonus += %.1ff; }" % (params["adjacent"], params["adjacent"])
    else:
        term = "float bonus = 0;"
    if "endempty" in params:
        term = "float bonus = count_cap(board) == 2 ? %.1ff * count_empty(board) : 0.0f;" % params["endempty"]
    if term != "float bonus = 0;":
        s = s.replace("    return score_helper(          board , heur_score_table) +", "    " + term + "\n    return bonus + score_helper(          board , heur_score_table) +")
    return s


def build(name):
    folder = ROOT / name
    folder.mkdir(exist_ok=True)
    cpp = folder / "engine.cpp"
    cpp.write_text(source(VARIANTS[name]))
    engine = folder / ("engine.dylib" if sys.platform == "darwin" else "engine.so")
    flags = ["clang++", "-O3", "-std=c++11", "-dynamiclib"] if sys.platform == "darwin" else ["g++", "-O3", "-std=c++11", "-shared", "-fPIC", "-pthread"]
    subprocess.run(flags + ["-I", str(WORK / "vendor"), str(cpp), "-o", str(engine)], check=True)
    manifest = {"variant": name, "parameters": VARIANTS[name], "source_sha256": hashlib.sha256(cpp.read_bytes()).hexdigest(), "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest()}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["build", "run"])
    ap.add_argument("variant", choices=VARIANTS)
    args, rest = ap.parse_known_args()
    if args.action == "build":
        build(args.variant)
    else:
        sys.path.insert(0, str(WORK))
        import bot, benchmark
        engine = ROOT / args.variant / ("engine.dylib" if sys.platform == "darwin" else "engine.so")
        bot.NATIVE = bot.load_engine(engine)
        benchmark.main(rest)
