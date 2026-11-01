"""Отдельный корпус: реальный бюджет10с и чувствительность к spawn-модели."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import cycle


def corpus(resume=False):
    plan = cycle.plan()
    spec = plan["ten_second_corpus"]
    config = dict(seeds=spec["source_seeds"], max_moves=plan["screen"]["max_moves"], teacher=spec["teacher"])
    schedule = [(seed, "teacher") for seed in range(config["seeds"][0], config["seeds"][1] + 1)]
    table = cycle.bot.spawn_table(cycle.bot.default_spawn_model())
    sources = cycle.source_hashes(["teacher"])
    sources["position_study.py"] = cycle.sha(Path(__file__))
    path = ROOT / "corpus-source.json"
    data = cycle.checkpoint(path, "position_corpus", config, sources, table, table, schedule, resume)
    cycle.reject_limits(data)
    for seed, arm in schedule[len(data["games"]):]:
        cycle.configure_arm(arm)
        row = cycle.play(seed, arm, table, max_moves=config["max_moves"])
        row.pop("records")
        data["games"].append(row)
        cycle.save(path, data, schedule)
        cycle.progress(row)
        cycle.reject_limits(data)
    return data


def positions():
    data = json.loads((ROOT / "corpus-source.json").read_text())
    if not data["complete"]:
        raise ValueError("Корпус не завершён")
    groups = {}
    for game in data["games"]:
        for caps in ("1", "2"):
            board = game["cap_boards"].get(caps)
            if board is None:
                continue
            empty = board.count(0)
            bucket = 0 if empty <= 3 else 1 if empty <= 7 else 2
            groups.setdefault((caps, bucket), []).append(dict(id=str(game["seed"]) + "/cap" + caps,
                seed=game["seed"], caps=int(caps), empty=empty, board=board))
    # Два первых seed в каждой из шести страт; исход партии не используется.
    selected = [row for key in sorted(groups) for row in sorted(groups[key], key=lambda r: r["seed"])[:2]]
    if not selected:
        raise ValueError("Нет незавершённых cap-позиций")
    return selected


def resume_study(path, config, sources, resume):
    path = Path(path)
    if not path.exists():
        if resume:
            raise ValueError("Нет study-checkpoint")
        return dict(config=config, source_hashes=sources, rows=[], complete=False)
    if not resume:
        raise ValueError("Study существует; используйте --resume")
    data = json.loads(path.read_text())
    if data.get("config") != config or data.get("source_hashes") != sources or not isinstance(data.get("rows"), list):
        raise ValueError("Несовместимый study-checkpoint")
    return data


def profile(resume=False):
    selected = positions()
    arms = ["baseline_legacy", "progressive"] + (["value_progressive"] if cycle.model_gate()["passed"] else [])
    spec = cycle.plan()["ten_second_corpus"]
    config = dict(positions=selected, arms=arms, depth=8, think=spec["think_seconds"],
                  scope="completed pass depth, not full-game win evidence; cutoff still applies")
    sources = cycle.source_hashes(arms, include_model="value_progressive" in arms)
    sources.update(corpus=cycle.sha(ROOT / "corpus-source.json"), study=cycle.sha(Path(__file__)))
    schedule = [(position, arm) for index, position in enumerate(selected)
                for arm in arms[index % len(arms):] + arms[:index % len(arms)]]
    path = ROOT / "ten-second-profile.json"
    data = resume_study(path, config, sources, resume)
    for row, (position, arm) in zip(data["rows"], schedule):
        if (row["id"], row["arm"]) != (position["id"], arm):
            raise ValueError("Повреждён порядок profile")
    if len(data["rows"]) > len(schedule):
        raise ValueError("Лишние profile-записи")
    table = cycle.bot.spawn_table(cycle.bot.default_spawn_model())
    for position, arm in schedule[len(data["rows"]):]:
        cycle.configure_arm(arm)
        cycle.benchmark.apply_table(table)
        info = {}
        began = time.monotonic()
        direction = cycle.bot.choose(tuple(position["board"]), 8, spec["think_seconds"], info,
                                     policy="legacy" if arm == "baseline_legacy" else "progressive")
        elapsed = time.monotonic() - began
        if direction not in [d for d, _ in cycle.bot.legal_moves(tuple(position["board"]))]:
            raise ValueError("Нелегальный profile-ход")
        data["rows"].append(dict(id=position["id"], arm=arm, board=position["board"],
                                 direction=direction, wall_seconds=elapsed, search=info))
        data["complete"] = len(data["rows"]) == len(schedule)
        cycle.benchmark.save_checkpoint(path, data)
        print(json.dumps(dict(id=position["id"], arm=arm, wall_seconds=elapsed, **info)), flush=True)
    return data


def stress_tables():
    base = cycle.bot.spawn_table(cycle.bot.default_spawn_model())
    rare = copy.deepcopy(base)
    row = rare[11]
    total = sum(row)
    rare[11] = [float(v) / total * .995 for v in row]
    rare[11][5] += .005
    tables = {"default": base, "rare_rank6_half_percent_at11": rare}
    for factor, name in ((.75, "higher_rank_weights_minus25pct"), (1.25, "higher_rank_weights_plus25pct")):
        table = copy.deepcopy(base)
        for stage, row in enumerate(table):
            table[stage] = [value * (factor if rank >= 3 else 1) for rank, value in enumerate(row, 1)]
        tables[name] = table
    return tables


def sensitivity(resume=False):
    selected = positions()
    arms = ["baseline_legacy", "cache_legacy"] + (["value_progressive"] if cycle.model_gate()["passed"] else [])
    tables = stress_tables()
    config = dict(positions=selected, arms=arms, solver_tables=tables,
                  environment_table=cycle.bot.spawn_table(cycle.bot.default_spawn_model()),
                  depth=3, think=None,
                  scope="fixed-position solver-model sensitivity only; no environment rollouts or win evidence")
    sources = cycle.source_hashes(arms, include_model="value_progressive" in arms)
    sources.update(corpus=cycle.sha(ROOT / "corpus-source.json"), study=cycle.sha(Path(__file__)))
    schedule = [(position, arm, name) for position in selected for arm in arms for name in tables]
    path = ROOT / "model-sensitivity.json"
    data = resume_study(path, config, sources, resume)
    if len(data["rows"]) > len(schedule):
        raise ValueError("Лишние sensitivity-записи")
    for row, (position, arm, name) in zip(data["rows"], schedule):
        if (row["id"], row["arm"], row["solver_model"]) != (position["id"], arm, name):
            raise ValueError("Повреждён порядок sensitivity")
    for position, arm, name in schedule[len(data["rows"]):]:
        cycle.configure_arm(arm)
        cycle.benchmark.apply_table(tables[name])
        board = tuple(position["board"])
        scores = {str(d): float(cycle.bot.NATIVE.score_levels(cycle.bot.pack(board), cycle.bot.DIRECTIONS[d], 3))
                  for d, _ in cycle.bot.legal_moves(board)}
        direction = int(max(scores, key=scores.get))
        data["rows"].append(dict(id=position["id"], arm=arm, solver_model=name,
                                 direction=direction, scores=scores))
        data["complete"] = len(data["rows"]) == len(schedule)
        cycle.benchmark.save_checkpoint(path, data)
    print(json.dumps(dict(positions=len(selected), comparisons=len(data["rows"]), complete=data["complete"])))
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("corpus", "profile", "sensitivity"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    {"corpus": corpus, "profile": profile, "sensitivity": sensitivity}[args.action](args.resume)
