"""Фиксированный geometry pure-math цикл: inventory, build, screen, freeze, control, finalize."""
import argparse
from contextlib import contextmanager
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
WORK = ROOT.parents[1]
sys.path.insert(0, str(WORK))
LIBRARY = "engine.dylib" if sys.platform == "darwin" else "engine.so"


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=1, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def plan():
    return read(ROOT / "plan.json")


@contextmanager
def lease():
    import fcntl
    with (ROOT / ".cycle.lock").open("a") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Другой этап geometry цикла уже выполняется") from exc
        yield


def progress(stage, **values):
    print(json.dumps(dict(stage=stage, **values)), flush=True)


def seed_values(value):
    """Консервативно резервирует наблюдавшиеся и прежние объявленные seeds."""
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if (key == "seed" or key.endswith("seed_start")) and type(item) is int:
                count = value.get("games", 1)
                count = count if type(count) is int and 0 < count <= 200000 else 1
                result.update(range(item, item + count))
            elif key in ("seeds", "seen_seeds") and isinstance(item, list):
                result.update(s for s in item if type(s) is int)
            elif key in ("new_ranges", "reserved_ranges") and isinstance(item, dict):
                for bounds in item.values():
                    if (isinstance(bounds, list) and len(bounds) == 2 and all(type(n) is int for n in bounds)
                            and 0 <= bounds[1] - bounds[0] <= 200000):
                        result.update(range(bounds[0], bounds[1] + 1))
            result.update(seed_values(item))
    elif isinstance(value, list):
        for item in value:
            result.update(seed_values(item))
    return result


def inventory():
    out = ROOT / "seed-inventory.json"
    if out.exists() or (ROOT / "screen.json").exists():
        raise ValueError("Inventory уже зафиксирован; перезапись запрещена")
    p = plan()
    space = Path(p["workspace"])
    roots = [x for x in space.iterdir() if x.is_dir() and (x.name.startswith("2048") or x.name == "merge-game-bot")]
    roots += [x for x in (space / "output").iterdir() if x.is_dir() and x.name.startswith("2048") and x != WORK.parent]
    history = p["history"]
    for item in history:
        archive = Path(item["root"]).resolve()
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=archive, text=True).strip()
        if head != item["commit"]:
            raise ValueError("History HEAD mismatch: " + item["name"])
        if archive not in roots and not any(archive.is_relative_to(x.resolve()) for x in roots):
            roots.append(archive)
    files, seen, errors = [], set(), []
    paths = sorted({path.resolve() for root in roots for path in root.rglob("*.json")
                    if not any(part in (".git", ".venv", "node_modules") for part in path.parts)})
    for path in paths:
        try:
            seen.update(seed_values(read(path)))
            files.append(dict(path=str(path), sha256=sha(path)))
        except (ValueError, OSError, RecursionError) as exc:
            errors.append(dict(path=str(path), error=str(exc)))
    ranges = {stage: [p[stage]["seed_start"], p[stage]["seed_start"] + p[stage]["games"] - 1]
              for stage in ("screen", "control")}
    overlap = {stage: sorted(seen.intersection(range(lo, hi + 1))) for stage, (lo, hi) in ranges.items()}
    data = dict(version=1, archive_commit=p["archive_commit"], archive_root=p["archive_root"], history=history,
                roots=[str(x.resolve()) for x in roots], excluded_current_experiment_root=str(WORK.parent.resolve()),
                files=files, seen_seeds=sorted(seen), reserved_ranges=ranges,
                overlap=overlap, errors=errors,
                scope="all JSON in listed 2048 history roots including prior math and TD; current experiment/dependencies excluded; unknown external seeds unverified")
    missing_history = [item["name"] for item in history
                       if not any(Path(f["path"]).is_relative_to(Path(item["root"]).resolve()) for f in files)]
    missing_old = {name: sorted(set(range(lo, hi + 1)) - seen)
                   for name, (lo, hi) in p["required_seen_ranges"].items()}
    if errors or any(overlap.values()) or missing_history or any(missing_old.values()):
        raise ValueError("Seed audit неполон/обнаружил пересечения: " + json.dumps(
            dict(errors=errors, overlap=overlap, missing_history=missing_history, missing_old=missing_old)))
    atomic(out, data)
    return data


def sources():
    inventory_data = read(ROOT / "seed-inventory.json")
    ranges = {stage: [plan()[stage]["seed_start"], plan()[stage]["seed_start"] + plan()[stage]["games"] - 1]
              for stage in ("screen", "control")}
    seen = set(inventory_data.get("seen_seeds", []))
    if (inventory_data.get("version") != 1 or inventory_data.get("archive_commit") != plan()["archive_commit"] or
            inventory_data.get("reserved_ranges") != ranges or inventory_data.get("history") != plan()["history"] or
            inventory_data.get("errors") or
            any(set(range(lo, hi + 1)) - seen for lo, hi in plan()["required_seen_ranges"].values()) or
            any(inventory_data.get("overlap", {"missing": [1]}).values()) or
            any(seen.intersection(range(lo, hi + 1)) for lo, hi in ranges.values())):
        raise ValueError("Нет успешного seed audit")
    paths = [Path(__file__).resolve(), ROOT / "plan.json", ROOT / "seed-inventory.json", WORK / "bot.py", WORK / "benchmark.py",
             WORK / "spawn-model.default.json", WORK / "vendor/engine_adapted.cpp", ROOT / "features.py",
             WORK / "vendor/geometry_features.h"] + list((WORK / "vendor").glob("*.h"))
    return {str(path.relative_to(WORK)): sha(path) for path in sorted(set(paths))}


def pinned_source():
    p = plan()
    data = subprocess.check_output(["git", "show", p["pure_commit"] + ":vendor/engine_adapted.cpp"], cwd=WORK)
    if hashlib.sha256(data).hexdigest() != p["pure_sha256"]:
        raise ValueError("Pinned pure source SHA mismatch")
    return data


def variant_source(base, arm):
    if arm["kind"] == "baseline":
        if arm["strength"] != 0:
            raise ValueError("Baseline strength должен быть 0")
        return base
    # Compile current bytes, avoiding import-cache/pyc reuse after source changes.
    # No feature module import and no native build happens at runner import time.
    path = ROOT / "features.py"
    namespace = {"__file__": str(path), "__name__": "bonk_geometry_features"}
    exec(compile(path.read_bytes(), str(path), "exec"), namespace)
    result = namespace["variant_source"](base, arm)
    if not isinstance(result, bytes):
        raise ValueError("Geometry producer должен вернуть bytes")
    return result


def engine_path(arm):
    return ROOT / "build" / arm / LIBRARY


def check_build():
    data = read(ROOT / "build/manifest.json")
    if data.get("version") != 1 or data.get("sources") != sources():
        raise ValueError("Build sources несовместимы; implicit rebuild запрещён")
    expected = {a["name"] for a in plan()["arms"]}
    if set(data.get("engines", {})) != expected:
        raise ValueError("Build arms несовместимы")
    base = pinned_source()
    for arm in plan()["arms"]:
        name = arm["name"]
        entry = data["engines"][name]
        cpp = engine_path(name).with_name("engine.cpp")
        if (entry.get("sha256") != sha(engine_path(name)) or entry.get("source_sha256") != sha(cpp) or
                cpp.read_bytes() != variant_source(base, arm)):
            raise ValueError("Изменён фактический бинарник/исходник " + name)
    return data


def build():
    if (ROOT / "build/manifest.json").exists():
        return check_build()
    if (ROOT / "screen.json").exists() or (ROOT / "finalist.json").exists():
        raise ValueError("Новая сборка после начала evaluation запрещена")
    fingerprint = sources()
    base = pinned_source()
    intent = ROOT / "build/intent.json"
    if intent.exists() and read(intent) != fingerprint:
        raise ValueError("Чужая незавершённая сборка")
    if not intent.exists() and (ROOT / "build").exists() and any((ROOT / "build").iterdir()):
        raise ValueError("Чужой build без intent")
    atomic(intent, fingerprint)
    flags = ["clang++", "-O3", "-std=c++11", "-dynamiclib", "-pthread"] if sys.platform == "darwin" else ["g++", "-O3", "-std=c++11", "-shared", "-fPIC", "-pthread"]
    compiler = subprocess.check_output([flags[0], "--version"], text=True)
    engines = {}
    for arm in plan()["arms"]:
        path = engine_path(arm["name"])
        path.parent.mkdir(parents=True, exist_ok=True)
        cpp = path.with_name("engine.cpp")
        cpp.write_bytes(variant_source(base, arm))
        temporary = path.with_name("pending-" + path.name)
        subprocess.run(flags + ["-I", str(WORK / "vendor"), str(cpp), "-o", str(temporary)], check=True)
        os.replace(temporary, path)
        engines[arm["name"]] = dict(sha256=sha(path), source_sha256=sha(cpp))
    if sources() != fingerprint:
        raise ValueError("Sources изменились во время сборки; manifest не публикуется")
    data = dict(version=1, sources=fingerprint, pure_commit=plan()["pure_commit"], compiler=compiler, flags=flags, engines=engines)
    atomic(ROOT / "build/manifest.json", data)
    return data


def configure(arm):
    bot = importlib.import_module("bot")
    bot.NATIVE = bot.load_engine(engine_path(arm))
    return bot


def play(seed, label, arm):
    bot = configure(arm)
    benchmark = importlib.import_module("benchmark")
    config = plan()["search"]
    table = bot.spawn_table(bot.default_spawn_model())
    benchmark.apply_table(table)
    rng = random.Random(seed)
    began = time.monotonic()
    board = benchmark.spawn(benchmark.spawn((0,) * 16, rng, table), rng, table)
    moves = positions = fallbacks = cuts = calls = 0
    wall = 0.0
    depths, requests, cap_boards = {}, {}, {}
    while moves < config["max_moves"] and not bot.reached_goal(board):
        info = {}
        started = time.monotonic()
        direction = bot.choose(board, config["depth"], config["think"], info, policy=config["policy"])
        wall += time.monotonic() - started
        calls += 1
        positions += int(info.get("positions", 0))
        fallbacks += bool(info.get("fallback", False))
        cuts += bool(info.get("cut", False))
        if direction is None:
            break
        if direction not in [d for d, _ in bot.legal_moves(board)]:
            raise ValueError("Нелегальный ход")
        after = bot.move(board, direction)
        board = after if bot.reached_goal(after) else benchmark.spawn(after, rng, table)
        moves += 1
        actual, requested = str(info["depth"]), str(info["requested_depth"])
        depths[actual], requests[requested] = depths.get(actual, 0) + 1, requests.get(requested, 0) + 1
        caps = board.count(bot.CAP)
        if caps in (1, 2) and str(caps) not in cap_boards and bot.legal_moves(board):
            cap_boards[str(caps)] = list(board)
    result = "goal" if bot.reached_goal(board) else "loss" if not bot.legal_moves(board) else "limit"
    return dict(seed=seed, arm=label, source_arm=arm, result=result, board=list(board), moves=moves, caps=board.count(bot.CAP),
                seconds=time.monotonic() - began, search_wall_seconds=wall, search_calls=calls, depth_counts=depths,
                requested_depth_counts=requests, fallbacks=fallbacks, cuts=cuts, positions=positions, cap_boards=cap_boards)


def schedule(stage, finalist=None):
    p = plan()
    arms = [a["name"] for a in p["arms"]] if stage == "screen" else p["control"]["labels"]
    return [(seed, label, label if stage == "screen" or label == "baseline" else finalist)
            for offset, seed in enumerate(range(p[stage]["seed_start"], p[stage]["seed_start"] + p[stage]["games"]))
            for label in arms[offset % len(arms):] + arms[:offset % len(arms)]]


def validate_games(games, expected):
    bot = importlib.import_module("bot")
    maximum = plan()["search"]["max_moves"]
    if not isinstance(games, list) or len(games) > len(expected):
        raise ValueError("Неверный games prefix")
    for row, match in zip(games, expected):
        try:
            board = row["board"]
            valid = isinstance(board, list) and len(board) == 16 and all(type(v) is int and 0 <= v <= 15 for v in board)
            valid = valid and (row["seed"], row["arm"], row["source_arm"]) == match
            valid = valid and row["result"] in ("goal", "loss", "limit") and type(row["moves"]) is int and 0 <= row["moves"] <= maximum
            valid = valid and row["caps"] == board.count(11) and (row["result"] == "goal") == bot.reached_goal(tuple(board))
            valid = valid and (row["result"] == "loss") == (not bot.reached_goal(tuple(board)) and not bot.legal_moves(tuple(board)))
            valid = valid and all(math.isfinite(row[k]) and row[k] >= 0 for k in ("seconds", "search_wall_seconds"))
            valid = valid and sum(row["depth_counts"].values()) == row["moves"] == sum(row["requested_depth_counts"].values())
            valid = valid and all(1 <= int(k) <= plan()["search"]["depth"] for k in row["depth_counts"])
            valid = valid and all(int(k) == plan()["search"]["depth"] for k in row["requested_depth_counts"])
            valid = valid and all(type(n) is int and n > 0 for n in [*row["depth_counts"].values(), *row["requested_depth_counts"].values()])
            valid = valid and all(type(row[k]) is int and row[k] >= 0 for k in ("search_calls", "fallbacks", "cuts", "positions"))
            valid = valid and row["moves"] <= row["search_calls"] <= row["moves"] + 1
            valid = valid and max(row["fallbacks"], row["cuts"]) <= row["search_calls"]
            for cap, snapshot in row["cap_boards"].items():
                valid = valid and cap in ("1", "2") and isinstance(snapshot, list) and len(snapshot) == 16
                valid = valid and all(type(v) is int and 0 <= v <= 15 for v in snapshot)
                valid = valid and snapshot.count(11) == int(cap) and not bot.reached_goal(tuple(snapshot)) and bool(bot.legal_moves(tuple(snapshot)))
            if not valid:
                raise ValueError("Некорректная партия/seed/arm/telemetry")
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("Некорректная запись партии") from exc


def checkpoint(stage, resume, *, validate_only=False):
    manifest = check_build()
    frozen = verify_freeze() if stage == "control" else None
    expected = schedule(stage, frozen["finalist"] if frozen else None)
    header = dict(version=1, stage=stage, config=dict(search=plan()["search"], stage=plan()[stage]), sources=sources(),
                  engines=manifest["engines"], freeze_sha256=sha(ROOT / "finalist.json") if frozen else None)
    path = ROOT / (stage + ".json")
    if path.exists():
        if not resume:
            raise ValueError("Checkpoint существует; требуется --resume")
        data = read(path)
        if any(data.get(k) != v for k, v in header.items()):
            raise ValueError("Checkpoint config/source/binary/freeze несовместим")
        validate_games(data["games"], expected)
        if data.get("complete") != (len(data["games"]) == len(expected)):
            raise ValueError("Некорректный complete")
        if any(g["result"] == "limit" for g in data["games"]):
            raise ValueError("Сохранённый limit; этап недействителен")
        if data["complete"]:
            return data
    else:
        if resume:
            raise ValueError("Checkpoint для --resume отсутствует")
        data = dict(header, games=[], complete=False)
    if validate_only:
        raise ValueError("Этап не завершён; неявное продолжение запрещено")
    atomic(path, data)
    for seed, label, arm in expected[len(data["games"]):]:
        data["games"].append(play(seed, label, arm))
        validate_games(data["games"], expected)
        data["complete"] = len(data["games"]) == len(expected)
        atomic(path, data)
        progress(stage, seed=seed, arm=label, result=data["games"][-1]["result"])
        if data["games"][-1]["result"] == "limit":
            raise ValueError("Limit сохранён без превращения в loss; этап остановлен")
    return data


def summarize(games):
    result = {}
    for arm in sorted({g["arm"] for g in games}):
        rows = [g for g in games if g["arm"] == arm]
        moves = sum(g["moves"] for g in rows)
        wall = sum(g["search_wall_seconds"] for g in rows)
        goals = sum(g["result"] == "goal" for g in rows)
        depths = {key: sum(g["depth_counts"].get(key, 0) for g in rows)
                  for key in sorted({key for g in rows for key in g["depth_counts"]}, key=int)}
        result[arm] = dict(games=len(rows), goals=goals, win_rate=goals / len(rows), limits=sum(g["result"] == "limit" for g in rows),
                           moves=moves, total_wall=sum(g["seconds"] for g in rows), search_wall=wall,
                           choose_wall_per_move=wall / moves if moves else None, depth_counts=depths,
                           positions=sum(g["positions"] for g in rows),
                           positions_scope="legacy search counter excludes extra immediate-risk execute_move checks")
    return result


def decision(screen):
    table = summarize(screen["games"])
    order = [a["name"] for a in plan()["arms"]]
    finalist = min(order, key=lambda a: (-table[a]["goals"], a != "baseline", table[a]["choose_wall_per_move"]
                                         if table[a]["choose_wall_per_move"] is not None else math.inf, order.index(a)))
    return finalist, table


def round_stop(table):
    best = table["baseline"]["goals"]
    rounds = []
    for number in (1, 2):
        previous = best
        best = max([best] + [table[a["name"]]["goals"] for a in plan()["arms"] if a["round"] == number])
        rounds.append(dict(round=number, previous_best=previous, best_goals=best, improved=best > previous))
    reason = "local_plateau" if not any(r["improved"] for r in rounds) else "bounded_neighborhood_exhausted"
    return reason, rounds


def required_freeze_paths():
    paths = [WORK / p for p in sources()] + [ROOT / "screen.json", ROOT / "build/manifest.json"]
    engines = [engine_path(a["name"]) for a in plan()["arms"]]
    paths += engines + [p.with_name("engine.cpp") for p in engines]
    return {str(path.relative_to(WORK)): path for path in sorted(set(paths))}


def freeze():
    path = ROOT / "finalist.json"
    if path.exists() or (ROOT / "control.json").exists():
        raise ValueError("Повторная заморозка запрещена")
    screen = checkpoint("screen", True, validate_only=True)
    finalist, table = decision(screen)
    reason, rounds = round_stop(table)
    data = dict(version=1, finalist=finalist, screen=table, policy=plan()["control"], selection=plan()["selection"],
                stop_reason=reason, rounds=rounds,
                hashes={key: sha(path) for key, path in required_freeze_paths().items()})
    atomic(path, data)
    return data


def verify_freeze():
    data = read(ROOT / "finalist.json")
    if (data.get("version") != 1 or data.get("policy") != plan()["control"] or data.get("selection") != plan()["selection"]
            or data.get("finalist") not in [a["name"] for a in plan()["arms"]] or not data.get("hashes")):
        raise ValueError("Некорректный freeze")
    required = required_freeze_paths()
    if not isinstance(data["hashes"], dict) or set(data["hashes"]) != set(required):
        raise ValueError("Неверный обязательный набор файлов freeze")
    for key, path in required.items():
        if sha(path) != data["hashes"][key]:
            raise ValueError("Замороженный файл изменён: " + key)
    screen = checkpoint("screen", True, validate_only=True)
    finalist, table = decision(screen)
    reason, rounds = round_stop(table)
    if (data["finalist"] != finalist or data.get("screen") != table or
            data.get("stop_reason") != reason or data.get("rounds") != rounds):
        raise ValueError("Freeze не соответствует исходному screen selection")
    return data


def paired(games):
    groups = {arm: {g["seed"]: g for g in games if g["arm"] == arm} for arm in ("baseline", "finalist")}
    base, candidate = groups["baseline"], groups["finalist"]
    if not base or base.keys() != candidate.keys() or sum(map(len, groups.values())) != len(games):
        raise ValueError("Неполные/дублирующиеся пары")
    if any(g["result"] == "limit" for g in games):
        raise ValueError("Нельзя проверять цензурированные партии")
    gained = sorted(s for s in base if candidate[s]["result"] == "goal" and base[s]["result"] != "goal")
    lost = sorted(s for s in base if candidate[s]["result"] != "goal" and base[s]["result"] == "goal")
    n = len(gained) + len(lost)
    p = sum(math.comb(n, k) for k in range(len(gained), n + 1)) / 2 ** n if n else 1.0
    return dict(gained=gained, lost=lost, one_sided_p=p, improved=len(gained) > len(lost) and p <= .05)


def finalize():
    frozen = verify_freeze()
    data = checkpoint("control", True, validate_only=True)
    test = paired(data["games"])
    arms = summarize(data["games"])
    base_cost, candidate_cost = (arms[a]["choose_wall_per_move"] for a in ("baseline", "finalist"))
    ratio = candidate_cost / base_cost if base_cost and candidate_cost is not None else None
    result = dict(version=1, finalist=frozen["finalist"], paired=test, arms=arms,
                  confirmed_improvement=frozen["finalist"] != "baseline" and test["improved"],
                  choose_wall_ratio=ratio, actual_cost_within_10_percent=ratio is not None and .9 <= ratio <= 1.1,
                  freeze_sha256=sha(ROOT / "finalist.json"), control_sha256=sha(ROOT / "control.json"),
                  stop_reason=frozen["stop_reason"], rounds=frozen["rounds"], stop=plan()["stop"], scope=plan()["scope"])
    atomic(ROOT / "summary.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("inventory", "build", "screen", "freeze", "control", "finalize"):
        command = sub.add_parser(action)
        if action in ("screen", "control"):
            command.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        with lease():
            if args.action in ("screen", "control"):
                result = checkpoint(args.action, args.resume)
            else:
                result = globals()[args.action]()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    progress("done", action=args.action, complete=result.get("complete"))


if __name__ == "__main__":
    main()
