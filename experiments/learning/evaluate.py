"""Заморозка одного варианта и независимый парный контроль нового цикла."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import cycle


def read_stage(stage):
    data = json.loads((ROOT / (stage + ".json")).read_text())
    if not data.get("complete"):
        raise ValueError("Не завершён " + stage)
    cycle.validate_games(data["games"], cycle.evaluation_schedule(data["config"]), data["config"])
    cycle.reject_limits(data)
    expected = (data["config"]["seeds"][1] - data["config"]["seeds"][0] + 1) * len(data["config"]["arms"])
    if len(data["games"]) != expected:
        raise ValueError("Неполный набор seed/arm")
    for arm in data["config"]["arms"]:
        if data["summaries"][arm] != cycle.summarize(data["games"], arm):
            raise ValueError("Неверная summary: " + arm)
    return data


def cost(rows):
    moves = sum(g["moves"] for g in rows)
    search = sum(g["search_wall_seconds"] for g in rows)
    depths = {}
    for game in rows:
        for depth, number in game["depth_counts"].items():
            depths[depth] = depths.get(depth, 0) + number
    return dict(moves=moves, search_wall_seconds=search,
                search_seconds_per_move=search / moves if moves else None,
                seconds=sum(g["seconds"] for g in rows), positions=sum(g["positions"] or 0 for g in rows),
                fallbacks=sum(g["fallbacks"] for g in rows), cuts=sum(g["cuts"] for g in rows),
                actual_depth_counts=depths)


def freeze():
    if (ROOT / "finalist.json").exists() or (ROOT / "holdout.json").exists():
        raise ValueError("Заморозка или контроль уже существуют")
    data = read_stage("screen")
    arms = data["config"]["arms"]
    scores = {arm: sum(g["result"] == "goal" for g in data["games"] if g["arm"] == arm) for arm in arms}
    costs = {arm: cost([g for g in data["games"] if g["arm"] == arm]) for arm in arms}
    candidates = [arm for arm in arms if arm != "baseline_legacy"]
    candidate = min(candidates, key=lambda arm: (-scores[arm], costs[arm]["search_seconds_per_move"], arms.index(arm)))
    # Фиксируем реальные бинарники, модель и все файлы, от которых зависел screen.
    hashes = dict(data["source_hashes"]["files"])
    for engine in data["source_hashes"]["engines"].values():
        hashes[engine["path"]] = engine["sha256"]
    if "model" in data["source_hashes"]:
        hashes["experiments/learning/model.bin"] = data["source_hashes"]["model"]["sha256"]
        hashes["value_model.py"] = data["source_hashes"]["value_model.py"]
    for path in (ROOT / "screen.json", ROOT / "training-metrics.json"):
        if path.exists():
            hashes[cycle.location(path)] = cycle.sha(path)
    # Статистическое правило и выбор корпуса — тоже часть замороженного опыта.
    for path in (ROOT / "evaluate.py", ROOT / "position_study.py"):
        hashes[cycle.location(path)] = cycle.sha(path)
    for path in (ROOT / "fit_round2.py", ROOT / "model-round2-plan.json",
                 ROOT / "refinement" / "selection.json"):
        if path.exists():
            hashes[cycle.location(path)] = cycle.sha(path)
    for relative, digest in hashes.items():
        if cycle.sha(cycle.WORK / relative) != digest:
            raise ValueError("Источник изменился после screen: " + relative)
    result = dict(finalist=candidate, hashes=hashes, screen_scores=scores, screen_costs=costs,
                  promoted=scores[candidate] > scores["baseline_legacy"],
                  exploratory=scores[candidate] <= scores["baseline_legacy"],
                  baseline_commit=cycle.plan()["baseline_commit"],
                  selection_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cycle.WORK, text=True).strip(),
                  frozen_at_utc=datetime.now(timezone.utc).isoformat(),
                  holdout_policy=cycle.plan()["holdout"], model_gate=cycle.model_gate(), no_further_tuning=True)
    cycle.benchmark.save_checkpoint(ROOT / "finalist.json", result)
    print(json.dumps(dict(finalist=candidate, scores=scores, promoted=result["promoted"]), indent=2))


def finalize():
    frozen = json.loads((ROOT / "finalist.json").read_text())
    for relative, digest in frozen["hashes"].items():
        if cycle.sha(cycle.WORK / relative) != digest:
            raise ValueError("Нарушена заморозка: " + relative)
    data = read_stage("holdout")
    if data["config"] != cycle.evaluation_config("holdout"):
        raise ValueError("Контроль не соответствует заморозке/протоколу")
    screen = read_stage("screen")
    candidate = frozen["finalist"]
    base = {g["seed"]: g for g in data["games"] if g["arm"] == "baseline_legacy"}
    other = {g["seed"]: g for g in data["games"] if g["arm"] == candidate}
    if base.keys() != other.keys():
        raise ValueError("Различные контрольные seed")
    train = cycle.plan()["training"]
    used = set()
    for limits in (train["seeds"], train["validation_seeds"], cycle.plan()["screen"]["seeds"]):
        used.update(range(limits[0], limits[1] + 1))
    if used.intersection(base):
        raise ValueError("Контроль пересекается с обучением/отбором")
    # Независимо от producer result считаем победы по реальным конечным полям.
    wins_a = {seed for seed, game in base.items() if game["board"].count(11) >= 3}
    wins_b = {seed for seed, game in other.items() if game["board"].count(11) >= 3}
    gained, lost = sorted(wins_b - wins_a), sorted(wins_a - wins_b)
    n = len(gained) + len(lost)
    numerator = sum(math.comb(n, k) for k in range(len(gained), n + 1)) if n else 1
    denominator = 2 ** n if n else 1
    p = numerator / denominator
    cost_a, cost_b = cost(list(base.values())), cost(list(other.values()))
    per_move_ratio = cost_b["search_seconds_per_move"] / cost_a["search_seconds_per_move"]
    significance = len(wins_b) > len(wins_a) and p <= .05
    training_count = sum(len(json.loads((ROOT / ("teacher-" + split + ".json")).read_text())["games"])
                         for split in ("train", "validation"))
    corpus_path = ROOT / "corpus-source.json"
    corpus_count = 0
    if corpus_path.exists():
        corpus = json.loads(corpus_path.read_text())
        if not corpus.get("complete"):
            raise ValueError("Позиционный корпус не завершён")
        cycle.reject_limits(corpus)
        corpus_count = len(corpus["games"])
    result = dict(finalist=candidate, games_per_arm=len(base), baseline_goals=len(wins_a),
                  candidate_goals=len(wins_b), gained=gained, lost=lost, same=len(base) - n,
                  exact_tail_numerator=numerator, exact_tail_denominator=denominator, one_sided_p=p,
                  difference_percentage_points=100 * (len(wins_b) - len(wins_a)) / len(base),
                  screen_promoted=frozen["promoted"], control_significance_passed=significance,
                  supported_short_budget_improvement=significance and frozen["promoted"],
                  model_gate=frozen["model_gate"], nominal_think_seconds=data["config"]["think"],
                  requested_depth=data["config"]["depth"], cost={"baseline": cost_a, "candidate": cost_b},
                  per_move_search_cost_ratio=per_move_ratio, near_equal_actual_search_cost=abs(per_move_ratio - 1) <= .1,
                  limits=0, no_seed_overlap=True, frozen_provenance_verified=True,
                  full_games_training_validation=training_count, full_games_screen=len(screen["games"]),
                  full_games_holdout=len(data["games"]), full_games_position_corpus=corpus_count,
                  total_full_games=training_count + len(screen["games"]) + len(data["games"]) + corpus_count,
                  runtime_or_default_policy_switched=False, ten_second_full_game_win_rate_validated=False)
    cycle.benchmark.save_checkpoint(ROOT / "summary.json", result)
    print(json.dumps({key: result[key] for key in ("finalist", "baseline_goals", "candidate_goals", "one_sided_p", "supported_short_budget_improvement", "per_move_search_cost_ratio", "total_full_games")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "finalize"))
    args = parser.parse_args()
    freeze() if args.action == "freeze" else finalize()
