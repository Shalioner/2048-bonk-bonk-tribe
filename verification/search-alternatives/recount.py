"""Independent raw recount: no games, native search or runner summarization."""
import collections
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

WORK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORK))
import bot


def read(path):
    return json.loads(path.read_text())


def main():
    root = WORK / "experiments/search_alternatives"
    plan, frozen, summary = (read(root / x) for x in ("plan.json", "finalist.json", "summary.json"))
    screen, control = (read(root / (x + ".json")) for x in ("screen", "control"))
    arms = {a["name"]: a for a in plan["arms"]}
    for path, expected in frozen["hashes"].items():
        assert hashlib.sha256((WORK / path).read_bytes()).hexdigest() == expected, path
    expected_screen = []
    names = list(arms)
    for i in range(16):
        expected_screen += [(2400000000 + i, a, a) for a in names[i % 5:] + names[:i % 5]]
    expected_control = []
    for i in range(100):
        labels = ["baseline", "finalist"] if i % 2 == 0 else ["finalist", "baseline"]
        expected_control += [(2500000000 + i, a, "baseline" if a == "baseline" else frozen["finalist"]) for a in labels]
    totals = {}
    for data, expected in ((screen, expected_screen), (control, expected_control)):
        assert data["complete"] and [(g["seed"], g["arm"], g["source_arm"]) for g in data["games"]] == expected
        for g in data["games"]:
            board = tuple(g["board"])
            assert g["caps"] == board.count(11)
            assert g["result"] in ("goal", "loss")
            assert (board.count(11) >= 3) == (g["result"] == "goal")
            if g["result"] == "loss":
                assert not bot.legal_moves(board)
            walls, budgets, caps = (g[x] for x in ("wall_samples", "budget_samples", "decision_caps"))
            assert len(walls) == len(budgets) == len(caps) == g["search_calls"]
            assert sum(walls) == g["search_wall_seconds"]
            assert sum(g["threshold_counts"].values()) == g["moves"]
            arm = arms[g["source_arm"]]
            assert g["thresholds"] == arm["thresholds"]
            assert g["budget_profile"] == arm["budget_profile"]
            credit = 0.0 if g["budget_profile"] == "banked_endgame" else None
            for wall, budget, cap in zip(walls, budgets, caps):
                assert math.isfinite(wall) and wall >= 0 and cap in (0, 1, 2)
                if credit is None:
                    assert budget == .01
                else:
                    credit += .01
                    assert abs(budget - min(.04 if cap == 2 else .01, max(0.0, credit))) <= 1e-10
                    credit -= wall
            if credit is None:
                assert g["credit_balance"] is None
            else:
                assert abs(credit - g["credit_balance"]) <= 1e-10
    grouped = {a: {g["seed"]: g for g in control["games"] if g["arm"] == a} for a in ("baseline", "finalist")}
    gained = sorted(s for s in grouped["baseline"] if grouped["finalist"][s]["result"] == "goal" and grouped["baseline"][s]["result"] != "goal")
    lost = sorted(s for s in grouped["baseline"] if grouped["finalist"][s]["result"] != "goal" and grouped["baseline"][s]["result"] == "goal")
    n = len(gained) + len(lost)
    exact_p = Fraction(sum(math.comb(n, k) for k in range(len(gained), n + 1)), 2 ** n)
    assert gained == summary["paired"]["gained"] and lost == summary["paired"]["lost"]
    assert float(exact_p) == summary["paired"]["one_sided_p"]
    for label, games in grouped.items():
        gs = list(games.values())
        walls = sorted(x for g in gs for x in g["wall_samples"])
        moves = sum(g["moves"] for g in gs)
        values = dict(goals=sum(g["result"] == "goal" for g in gs), moves=moves,
                      choose_wall_per_move=sum(g["search_wall_seconds"] for g in gs) / moves,
                      choose_cpu_per_move=sum(g["search_cpu_seconds"] for g in gs) / moves)
        assert all(values[k] == summary["arms"][label][k] for k in values)
        latency = dict(count=len(walls), median_seconds=statistics.median(walls),
                       p95_nearest_rank_seconds=walls[math.ceil(.95 * len(walls)) - 1],
                       max_seconds=walls[-1], above_nominal_rate=sum(x > .01 for x in walls) / len(walls))
        assert all(v == summary["arms"][label]["wall_latency"][k] for k, v in latency.items())
        totals[label] = dict(**values, latency=latency)
    result = dict(games_checked=280, freeze_hashes_checked=len(frozen["hashes"]),
                  gained=len(gained), lost=len(lost), exact_p=str(exact_p), one_sided_p=float(exact_p),
                  totals=totals, scope="raw outcome/ledger/order/hash/latency recount; no complete trajectory replay or fresh games")
    (Path(__file__).parent / "recount.json").write_text(json.dumps(result, indent=1) + "\n")
    print({k: v for k, v in result.items() if k != "totals"})


if __name__ == "__main__":
    main()
