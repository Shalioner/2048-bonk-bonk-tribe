"""Офлайн-цикл данных, обучения и последовательного парного контроля Bonk."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
WORK = ROOT.parents[1]
sys.path.insert(0, str(WORK))
import bot
import benchmark

ARMS = ("baseline_legacy", "cache_legacy", "progressive", "value_progressive")
SUFFIX = "engine.dylib" if sys.platform == "darwin" else "engine.so"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def location(path):
    try:
        return str(Path(path).relative_to(WORK))
    except ValueError:
        return str(path)


def plan():
    return json.loads((ROOT / "plan.json").read_text())


def engine_path(arm):
    return ROOT / "build" / "baseline" / SUFFIX if arm == "baseline_legacy" else WORK / "vendor" / SUFFIX


def configure_arm(arm, model_path=None):
    if arm not in (*ARMS, "teacher"):
        raise ValueError("Неизвестный вариант: " + arm)
    bot.NATIVE = bot.load_engine(engine_path(arm))
    configure = getattr(bot, "configure_value_model", None)
    if configure is None:
        raise RuntimeError("Нужна интегрированная bot.configure_value_model")
    configure(Path(model_path or ROOT / "model.bin") if arm == "value_progressive" else None)
    if arm in ("progressive", "value_progressive") and not hasattr(bot.NATIVE, "solve_budget_progressive"):
        raise RuntimeError("Движок не поддерживает progressive; пересоберите sh vendor/build.sh")


def build_baseline():
    """Замороженный исходник прежнего движка, отдельно от текущей сборки."""
    commit = plan()["baseline_commit"]
    source = subprocess.check_output(["git", "show", commit + ":vendor/engine_adapted.cpp"], cwd=WORK)
    folder = ROOT / "build" / "baseline"
    folder.mkdir(parents=True, exist_ok=True)
    cpp, binary, manifest_path = folder / "engine.cpp", folder / SUFFIX, folder / "manifest.json"
    headers = {name: sha(WORK / "vendor" / name) for name in ("2048.h", "config.h", "platdefs.h")}
    source_sha = hashlib.sha256(source).hexdigest()
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if (manifest.get("commit") == commit and manifest.get("source_sha256") == source_sha and
                manifest.get("headers") == headers and cpp.exists() and sha(cpp) == source_sha and
                binary.exists() and sha(binary) == manifest.get("engine_sha256")):
            return manifest
        raise ValueError("Существующая baseline-сборка несовместима; сохраните её и выберите отдельную рабочую копию")
    cpp.write_bytes(source)
    flags = (["clang++", "-O3", "-std=c++11", "-dynamiclib"] if sys.platform == "darwin" else
             ["g++", "-O3", "-std=c++11", "-shared", "-fPIC", "-pthread"])
    subprocess.run(flags + ["-I", str(WORK / "vendor"), str(cpp), "-o", str(binary)], check=True)
    manifest = dict(commit=commit, source_sha256=source_sha, engine_sha256=sha(binary), headers=headers, flags=flags)
    benchmark.save_checkpoint(manifest_path, manifest)
    return manifest


def validate_table(table):
    if (len(table) != 16 or any(len(row) != 15 for row in table) or
            any(not all(math.isfinite(float(x)) and x >= 0 for x in row) or sum(row) <= 0 for row in table)):
        raise ValueError("Таблица появлений должна содержать 16 конечных ненулевых строк по 15 неотрицательных весов")


def source_hashes(arms, include_model=False):
    paths = [Path(__file__), ROOT / "plan.json", WORK / "bot.py", WORK / "benchmark.py",
             WORK / "vendor" / "engine_adapted.cpp", WORK / "spawn-model.default.json"]
    paths += [p for p in (WORK / "vendor").glob("*.h")]
    sources = {location(p): sha(p) for p in sorted(set(paths))}
    engines = {}
    for arm in arms:
        configure_arm(arm)
        loaded = Path(bot.NATIVE._name)
        engines[arm] = dict(path=location(loaded), sha256=sha(loaded))
    result = dict(files=sources, engines=engines)
    if include_model:
        import value_model
        result["model"] = dict(path="model.bin", sha256=sha(ROOT / "model.bin"))
        result["value_model.py"] = sha(value_model.__file__)
    return result


def play(seed, arm, solver_table, environment_table=None, *, depth=8, think=.01, max_moves=6000, sample=False):
    """Полная партия; без параллельных игр. Модель мира отделена от solver."""
    environment_table = solver_table if environment_table is None else environment_table
    validate_table(solver_table)
    validate_table(environment_table)
    benchmark.apply_table(solver_table)
    rng = random.Random(seed)
    started = time.monotonic()
    board = benchmark.spawn(benchmark.spawn((0,) * 16, rng, environment_table), rng, environment_table)
    moves = 0
    firsts, snapshots, depths, requests = {}, {}, {}, {}
    positions = None if arm == "teacher" else 0
    search_wall, search_calls, fallbacks, cuts = 0.0, 0, 0, 0
    pending, seen, cap_transitions = [], set(), set()
    while moves < max_moves and not bot.reached_goal(board):
        requested = 3 if arm == "teacher" and board.count(bot.CAP) >= 2 else 2 if arm == "teacher" else depth
        info = {}
        began = time.monotonic()
        direction = (benchmark.choose_fixed(board, requested) if arm == "teacher" else
                     bot.choose(board, requested, think, info, policy="progressive" if arm.endswith("progressive") else "legacy"))
        search_wall += time.monotonic() - began
        search_calls += 1
        if arm != "teacher":
            positions += int(info.get("positions", 0))
            fallbacks += bool(info.get("fallback", False))
            cuts += bool(info.get("cut", False))
        if direction is None:
            break
        if direction not in [d for d, _ in bot.legal_moves(board)]:
            raise RuntimeError("Решатель вернул нелегальный ход")
        after = bot.move(board, direction)
        number, caps = moves + 1, after.count(bot.CAP)
        transition = caps != board.count(bot.CAP) and caps not in cap_transitions
        if transition:
            cap_transitions.add(caps)
        if sample and not bot.reached_goal(after) and (number % 32 == 0 or transition or number % 8 == 0 and after.count(0) <= 3):
            packed = bot.pack(after)
            if packed not in seen:
                pending.append(dict(seed=seed, board=list(after), move=number, stage=min(caps, 2)))
                seen.add(packed)
        actual = int(info.get("depth", requested))
        depths[str(actual)] = depths.get(str(actual), 0) + 1
        requests[str(requested)] = requests.get(str(requested), 0) + 1
        board = benchmark.spawn(after, rng, environment_table)
        moves = number
        caps = board.count(bot.CAP)
        if caps and str(caps) not in firsts:
            firsts[str(caps)], snapshots[str(caps)] = moves, list(board)
    result = "goal" if bot.reached_goal(board) else "loss" if not bot.legal_moves(board) else "limit"
    records = []
    if pending and result != "limit":
        records = [dict(row, label=int(result == "goal"), episode_weight=1 / len(pending)) for row in pending]
    return dict(seed=seed, arm=arm, depth=2 if arm == "teacher" else depth,
                endgame_depth=3 if arm == "teacher" else None, think=None if arm == "teacher" else think,
                result=result, moves=moves, caps=board.count(bot.CAP), max=max(board), board=list(board),
                seconds=time.monotonic() - started, search_wall_seconds=search_wall, search_calls=search_calls,
                depth_counts=depths, requested_depth_counts=requests, fallbacks=fallbacks, cuts=cuts,
                positions=positions, cap_reached_at=firsts, cap_boards=snapshots,
                sampled_positions=len(pending), records=records)


def valid_board(board):
    return isinstance(board, list) and len(board) == 16 and all(type(x) is int and 0 <= x <= 15 for x in board)


def validate_games(games, schedule, config):
    if not isinstance(games, list) or len(games) > len(schedule):
        raise ValueError("Некорректный список партий")
    for row, expected in zip(games, schedule):
        try:
            board = row["board"]
            valid = (row["seed"], row["arm"]) == expected and valid_board(board)
            valid = valid and row["result"] in ("goal", "loss", "limit") and 0 <= row["moves"] <= config["max_moves"]
            valid = valid and row["caps"] == board.count(bot.CAP) and row["max"] == max(board)
            valid = valid and math.isfinite(row["seconds"]) and row["seconds"] >= 0
            valid = valid and math.isfinite(row["search_wall_seconds"]) and row["search_wall_seconds"] >= 0
            valid = valid and sum(row["depth_counts"].values()) == row["moves"]
            valid = valid and sum(row["requested_depth_counts"].values()) == row["moves"]
            valid = valid and (row["result"] == "goal") == bot.reached_goal(tuple(board))
            if row["result"] == "loss":
                valid = valid and not bot.legal_moves(tuple(board))
            if not valid:
                raise ValueError("Некорректная партия или нарушен порядок seed/arm")
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("Некорректная запись партии") from exc


def checkpoint(path, kind, config, sources, solver, environment, schedule, resume):
    header = dict(checkpoint_version=1, kind=kind, config=config, source_hashes=sources,
                  solver_table=solver, environment_table=environment)
    path = Path(path)
    if path.exists():
        if not resume:
            raise ValueError("Результат существует; используйте --resume, перезапись запрещена")
        saved = json.loads(path.read_text())
        if not isinstance(saved, dict) or any(saved.get(key) != value for key, value in header.items()):
            raise ValueError("Checkpoint несовместим: изменились параметры, исходники, движок или модель")
        validate_games(saved.get("games"), schedule, config)
        if saved.get("complete") != (len(saved["games"]) == len(schedule)):
            raise ValueError("Некорректный признак завершения checkpoint")
        return saved
    if resume:
        raise ValueError("Checkpoint для --resume не существует")
    return dict(header, games=[], records=[], complete=False)


def save(path, data, schedule):
    data["complete"] = len(data["games"]) == len(schedule)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    benchmark.save_checkpoint(path, data)


def progress(row):
    print(json.dumps({key: row[key] for key in ("seed", "arm", "result", "moves", "caps", "seconds")}), flush=True)


def reject_limits(data):
    if any(row["result"] == "limit" for row in data["games"]):
        raise ValueError("Limit делает опыт недействительным; нужна симметричная поправка протокола, а не проигрыш")


def teacher_config(split):
    p = plan()
    seeds = p["training"]["seeds" if split == "train" else "validation_seeds"]
    return dict(split=split, seeds=seeds, teacher=p["training"]["teacher"], sampling=p["training"]["sampling"],
                max_moves=p["screen"]["max_moves"], label=p["training"]["label"], episode_balance=p["training"]["episode_balance"])


def validate_records(data):
    games = {row["seed"]: row for row in data["games"]}
    groups, seen = {}, set()
    if not isinstance(data.get("records"), list):
        raise ValueError("Нет обучающих записей")
    for row in data["records"]:
        try:
            game = games[row["seed"]]
            key = row["seed"], tuple(row["board"])
            valid = valid_board(row["board"]) and key not in seen and not bot.reached_goal(tuple(row["board"]))
            valid = valid and game["result"] != "limit" and row["label"] == int(game["result"] == "goal")
            valid = valid and row["stage"] == min(row["board"].count(bot.CAP), 2)
            valid = valid and 1 <= row["move"] <= game["moves"]
            valid = valid and math.isfinite(row["episode_weight"]) and row["episode_weight"] > 0
            if not valid:
                raise ValueError("Некорректная обучающая запись")
            seen.add(key)
            groups.setdefault(row["seed"], []).append(row)
        except (KeyError, TypeError) as exc:
            raise ValueError("Некорректная обучающая запись") from exc
    for seed, game in games.items():
        samples = game.get("sampled_positions")
        if type(samples) is not int or not 0 <= samples <= game["moves"]:
            raise ValueError("Некорректное число sampled_positions")
        expected = 0 if game["result"] == "limit" else samples
        if len(groups.get(seed, [])) != expected:
            raise ValueError("Нарушена полнота records эпизода " + str(seed))
    for records in groups.values():
        if not all(math.isclose(row["episode_weight"], 1 / len(records), rel_tol=1e-9) for row in records):
            raise ValueError("Нарушено равное взвешивание эпизода")


def collect(split, resume=False, out=None):
    config = teacher_config(split)
    schedule = [(seed, "teacher") for seed in range(config["seeds"][0], config["seeds"][1] + 1)]
    table = bot.spawn_table(bot.default_spawn_model())
    sources = source_hashes(["teacher"])  # model.bin не участвует в teacher/resume.
    path = Path(out or ROOT / ("teacher-" + split + ".json"))
    data = checkpoint(path, "teacher", config, sources, table, table, schedule, resume)
    validate_records(data)
    reject_limits(data)
    if not path.exists():
        save(path, data, schedule)
    for seed, arm in schedule[len(data["games"]):]:
        configure_arm(arm)
        row = play(seed, arm, table, table, max_moves=config["max_moves"], sample=True)
        data["records"].extend(row.pop("records"))
        data["games"].append(row)
        save(path, data, schedule)
        progress(row)
        reject_limits(data)
    return data


def load_teacher(split):
    """Читает завершённый dataset; fit никогда не продолжает сбор неявно."""
    config = teacher_config(split)
    schedule = [(seed, "teacher") for seed in range(config["seeds"][0], config["seeds"][1] + 1)]
    table = bot.spawn_table(bot.default_spawn_model())
    sources = source_hashes(["teacher"])
    data = checkpoint(ROOT / ("teacher-" + split + ".json"), "teacher", config, sources, table, table, schedule, True)
    validate_records(data)
    return data


def evaluation_started():
    return any((ROOT / name).exists() for name in
               ("evaluation-started.json", "screen.json", "holdout.json", "finalist.json"))


def fit(resume=False):
    import value_model
    training = load_teacher("train")
    validation = load_teacher("validation")
    if not training["complete"] or not validation["complete"]:
        raise ValueError("Данные для fit не завершены")
    reject_limits(training)
    reject_limits(validation)
    if not training["records"] or not validation["records"]:
        raise ValueError("Нет обучающих записей")
    params = plan()["training"]
    config = {key: params[key] for key in ("epochs", "learning_rate", "l2", "patience")}
    config["min_delta"] = params.get("min_delta", .001)
    datasets = {split: sha(ROOT / ("teacher-" + split + ".json")) for split in ("train", "validation")}
    sources = dict(cycle=sha(Path(__file__)), plan=sha(ROOT / "plan.json"), value_model=sha(value_model.__file__))
    model_path, metrics_path = ROOT / "model.bin", ROOT / "training-metrics.json"
    if model_path.exists() or metrics_path.exists():
        if not resume or not model_path.exists() or not metrics_path.exists():
            raise ValueError("Артефакты fit существуют; перезапись или неполный --resume запрещены")
        saved = json.loads(metrics_path.read_text())
        if (saved.get("config") != config or saved.get("datasets") != datasets or saved.get("source_hashes") != sources or
                saved.get("model_sha256") != sha(model_path)):
            raise ValueError("Fit-checkpoint несовместим")
        return saved
    if evaluation_started():
        raise ValueError("Новый fit после начала отбора/контроля запрещён")
    model = value_model.ValueModel()
    metrics = model.fit(training["records"], validation_records=validation["records"], **config)
    brier = float(metrics["validation_brier"])
    prior = float(metrics["stage_prior_validation_brier"])
    if not math.isfinite(brier) or not math.isfinite(prior):
        raise ValueError("Неконечные метрики валидации")
    improvement = prior - brier
    passed = bool(metrics.get("validation_gate_passed", False)) and improvement >= .001
    model.metadata.update(source_sha256=sources, dataset_sha256=datasets)
    model.save(model_path)
    saved = dict(checkpoint_version=1, kind="fit", config=config, datasets=datasets, source_hashes=sources,
                 metrics=metrics, model_sha256=sha(model_path), validation_gate_passed=passed,
                 gate=dict(brier=brier, stage_prior_brier=prior, improvement=improvement, required_improvement=.001, passed=passed))
    benchmark.save_checkpoint(metrics_path, saved)
    return saved


def model_gate():
    path = ROOT / "training-metrics.json"
    if not path.exists():
        return dict(passed=False, metrics_sha256=None)
    data = json.loads(path.read_text())
    gate = data.get("gate", {})
    passed = (data.get("validation_gate_passed") is True and gate.get("passed") is True and
              float(gate.get("improvement", -math.inf)) >= .001)
    if passed and (not (ROOT / "model.bin").exists() or sha(ROOT / "model.bin") != data.get("model_sha256")):
        raise ValueError("Модель не соответствует training-metrics")
    return dict(passed=passed, metrics_sha256=sha(path))


def evaluation_config(stage):
    section = plan()[stage]
    if stage == "screen":
        gate = model_gate()
        arms = []
        for name in section["policies"]:
            if name == "value_progressive_if_validation_passed":
                if gate["passed"]:
                    arms.append("value_progressive")
            elif name in ARMS[:3]:
                arms.append(name)
            else:
                raise ValueError("Недопустимый вариант screen в плане: " + str(name))
        if "baseline_legacy" not in arms or len(arms) != len(set(arms)):
            raise ValueError("Screen требует baseline и уникальные варианты")
        frozen = None
    else:
        frozen = json.loads((ROOT / "finalist.json").read_text())
        if not isinstance(frozen, dict):
            raise ValueError("Некорректная заморозка финалиста")
        frozen_hashes = frozen.get("hashes")
        if not isinstance(frozen_hashes, dict) or not frozen_hashes:
            raise ValueError("Нет замороженных hashes финалиста")
        for relative, digest in frozen_hashes.items():
            if not isinstance(relative, str) or not isinstance(digest, str):
                raise ValueError("Некорректные hashes финалиста")
            path = (WORK / relative).resolve()
            if not path.is_relative_to(WORK.resolve()) or sha(path) != digest:
                raise ValueError("Файл не соответствует заморозке: " + relative)
        candidate = frozen["finalist"]
        if candidate not in ARMS[1:]:
            raise ValueError("Недопустимый единственный финалист")
        gate = model_gate() if candidate == "value_progressive" else None
        if gate is not None and not gate["passed"]:
            raise ValueError("Замороженная learned-модель не прошла gate")
        arms = ["baseline_legacy", candidate]
        if section["seeds"][1] - section["seeds"][0] + 1 != section["games_per_arm"]:
            raise ValueError("Число holdout seed не соответствует games_per_arm")
    config = dict(stage=stage, seeds=section["seeds"], arms=arms, depth=section["depth"],
                  think=section["think_seconds"], max_moves=section["max_moves"], order=section["order"], model_gate=gate)
    if stage == "holdout":
        config["finalist_sha256"] = sha(ROOT / "finalist.json")
    return config


def evaluation_schedule(config):
    schedule = []
    arms = config["arms"]
    for index, seed in enumerate(range(config["seeds"][0], config["seeds"][1] + 1)):
        offset = index % len(arms)
        schedule.extend((seed, arm) for arm in arms[offset:] + arms[:offset])
    return schedule


def summarize(games, arm):
    rows = [row for row in games if row["arm"] == arm]
    moves = sum(row["moves"] for row in rows)
    seconds = sum(row["seconds"] for row in rows)
    return dict(games=len(rows), goals=sum(row["result"] == "goal" for row in rows),
                losses=sum(row["result"] == "loss" for row in rows), limits=sum(row["result"] == "limit" for row in rows),
                moves=moves, seconds=seconds, seconds_per_move=seconds / moves if moves else None,
                search_wall_seconds=sum(row["search_wall_seconds"] for row in rows),
                fallbacks=sum(row["fallbacks"] for row in rows), cuts=sum(row["cuts"] for row in rows),
                positions=sum(row["positions"] for row in rows if row["positions"] is not None))


def evaluate(stage, resume=False, out=None, solver_table=None, environment_table=None):
    config = evaluation_config(stage)
    schedule = evaluation_schedule(config)
    solver = bot.spawn_table(bot.default_spawn_model()) if solver_table is None else solver_table
    environment = solver if environment_table is None else environment_table
    validate_table(solver)
    validate_table(environment)
    sources = source_hashes(config["arms"], include_model="value_progressive" in config["arms"])
    path = Path(out or ROOT / (stage + ".json"))
    data = checkpoint(path, stage, config, sources, solver, environment, schedule, resume)
    reject_limits(data)
    # Не зависит от --out: даже прерванная первая партия закрывает обучение.
    marker = ROOT / "evaluation-started.json"
    if not marker.exists():
        benchmark.save_checkpoint(marker, dict(stage=stage, output=str(path.resolve())))
    if not path.exists():
        data["summaries"] = {name: summarize([], name) for name in config["arms"]}
        save(path, data, schedule)
    for seed, arm in schedule[len(data["games"]):]:
        configure_arm(arm)
        row = play(seed, arm, solver, environment, depth=config["depth"], think=config["think"], max_moves=config["max_moves"])
        row.pop("records", None)
        data["games"].append(row)
        data["summaries"] = {name: summarize(data["games"], name) for name in config["arms"]}
        save(path, data, schedule)
        progress(row)
        reject_limits(data)
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("build-baseline")
    for action in ("collect", "fit", "screen", "holdout"):
        command = sub.add_parser(action)
        command.add_argument("--resume", action="store_true")
        if action == "collect":
            command.add_argument("--split", choices=("train", "validation"), required=True)
        if action != "fit":
            command.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        if args.action == "build-baseline":
            data = build_baseline()
        elif args.action == "collect":
            data = collect(args.split, args.resume, args.out)
        elif args.action == "fit":
            data = fit(args.resume)
        else:
            data = evaluate(args.action, args.resume, args.out)
    except (ValueError, RuntimeError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(action=args.action, complete=data.get("complete"), gate=data.get("gate"))), flush=True)


if __name__ == "__main__":
    main()
