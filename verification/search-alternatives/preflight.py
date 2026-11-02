"""Archive the actual build and smoke its contract; no games or phone access."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

WORK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORK))
from experiments.search_alternatives import run as cycle


def main():
    manifest = cycle.check_build()
    target = Path(__file__).resolve().parent
    native_backup = WORK.parent / "native-engines"
    native_backup.mkdir(exist_ok=True)
    rows = []
    board = (1, 1) + (0,) * 14
    for arm in cycle.plan()["arms"]:
        name = arm["name"]
        path = cycle.engine_path(name)
        bot = cycle.configure(name)
        assert (bot.NATIVE.engine_api_version(), bot.NATIVE.get_cap_rank(),
                bot.NATIVE.get_goal_count()) == (2, 11, 3)
        assert hasattr(bot.NATIVE, "solve_budget_progressive")
        assert not hasattr(bot.NATIVE, "load_value_model")
        info = {}
        direction = bot.choose(board, depth=1, think=0.01, info=info, policy="progressive")
        assert direction in [d for d, _ in bot.legal_moves(board)]
        assert tuple(bot.THRESHOLDS) == tuple(arm["thresholds"])
        shutil.copy2(path, native_backup / (name + path.suffix))
        shutil.copy2(path.with_name("engine.cpp"), target / (name + ".cpp"))
        rows.append(dict(arm=name, api=2, cap=11, goal=3, move=direction,
                         thresholds=list(bot.THRESHOLDS), search=info,
                         native_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (target / "build-manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    (target / "preflight.json").write_text(json.dumps(dict(
        scope="API and one-move smoke, not win-rate data; actual libraries backed up outside Git",
        rows=rows), indent=1) + "\n")
    for source in ("search-cycle-tests.txt", "search-variant-tests.txt", "build-search.log"):
        shutil.copy2(WORK.parent / source, target / source)
    print({"arms": len(rows), "api": 2, "cap": 11, "goal": 3, "sources_verified": True})


if __name__ == "__main__":
    main()
