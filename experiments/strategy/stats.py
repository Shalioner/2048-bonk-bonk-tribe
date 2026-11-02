"""Paired outcomes on identical independent game seeds, without external packages."""
import argparse, json, math
from pathlib import Path

def compare(base, candidate):
    assert base["config"] == candidate["config"], "Different simulation configurations"
    assert base["spawn_table"] == candidate["spawn_table"], "Different spawn rules"
    assert base["complete"] and candidate["complete"], "Incomplete checkpoint"
    a = {g["seed"]: g for g in base["games"]}
    b = {g["seed"]: g for g in candidate["games"]}
    assert len(a) == len(base["games"]) and len(b) == len(candidate["games"]), "Requires one depth"
    assert a.keys() == b.keys(), "Different seeds"
    assert not any(g["result"] == "limit" for g in [*a.values(), *b.values()]), "Censored games"
    gained = [s for s in a if a[s]["result"] != "goal" and b[s]["result"] == "goal"]
    lost = [s for s in a if a[s]["result"] == "goal" and b[s]["result"] != "goal"]
    n = len(gained) + len(lost)
    p = sum(math.comb(n,k) for k in range(len(gained), n+1)) / 2**n if n else 1.0
    return dict(games=len(a), baseline_goals=sum(g["result"]=="goal" for g in a.values()),
                candidate_goals=sum(g["result"]=="goal" for g in b.values()),
                gained=gained, lost=lost, same=len(a)-n, one_sided_p=p,
                baseline_seconds=sum(g["seconds"] for g in a.values()),
                candidate_seconds=sum(g["seconds"] for g in b.values()))

if __name__ == "__main__":
    ap=argparse.ArgumentParser();ap.add_argument("base",type=Path);ap.add_argument("candidate",type=Path)
    args=ap.parse_args();print(json.dumps(compare(json.loads(args.base.read_text()),json.loads(args.candidate.read_text())),indent=2))
