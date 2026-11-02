"""Офлайн-симуляция без телефона и ADB.

Правила режима: плитки уровня 11 (2048) не сливаются, цель — три такие плитки одновременно.
Новые плитки появляются в случайной пустой клетке; уровень выбирается по строке
spawn-model.default.json для текущей стадии (максимальный уровень после хода).
Ходы выбирает тот же C++-движок, что и на телефоне.

Пример: python3 benchmark.py --games 10 --depth 4
Без --think — один проход с порогом 1e-4 без ограничения времени (воспроизводимо, не зависит
от скорости машины). С --think N — как на телефоне: проходы с ослабляемым отсечением в пределах
жёсткого бюджета N секунд. После каждой партии checkpoint атомарно сохраняется в --out.
Повторите команду с --resume, чтобы продолжить тот же набор без повтора завершённых партий.
Результаты пишутся в benchmark.json (в .gitignore).
"""
import argparse, ctypes, hashlib, json, os, random, tempfile, time
from pathlib import Path
import bot


def spawn(board, rng, table):
    empty = [i for i, v in enumerate(board) if not v]
    cell = rng.choice(empty)
    level = rng.choices(range(1, 16), weights=table[max(board)])[0]
    out = list(board)
    out[cell] = level
    return tuple(out)


def apply_table(table):
    if len(table) != 16 or any(len(row) != 15 for row in table):
        raise ValueError("Модель должна содержать 16 стадий по 15 уровней")
    flat = [float(v) for row in table for v in row]
    if bot.NATIVE.set_spawn_table((ctypes.c_float * len(flat))(*flat), 16, 15) != 0:
        raise ValueError("Некорректная модель появлений")


def choose_fixed(board, depth):
    legal = bot.legal_moves(board)
    if not legal:
        return None
    allowed = sum(1 << bot.DIRECTIONS[d] for d, _ in legal)
    direction = bot.NATIVE.solve_levels(bot.pack(board), allowed, depth)
    return bot.DIRECTIONS.index(direction) if direction >= 0 else None


def play(seed, depth, think, max_moves, table, endgame_depth=None, policy="legacy"):
    # Solver and simulator must use the same frozen distribution throughout a game.
    apply_table(table)
    rng = random.Random(seed)
    board = spawn(spawn((0,) * 16, rng, table), rng, table)
    started = time.monotonic()
    firsts = {}
    moves = 0
    depth_counts = {}
    while moves < max_moves and not bot.reached_goal(board):
        search_depth = endgame_depth if endgame_depth is not None and board.count(bot.CAP) >= 2 else depth
        d = choose_fixed(board, search_depth) if think is None else bot.choose(board, search_depth, think, policy=policy)
        if d is None:
            break
        key = str(search_depth)
        depth_counts[key] = depth_counts.get(key, 0) + 1
        board = spawn(bot.move(board, d), rng, table)
        moves += 1
        count = board.count(bot.CAP)
        if count and count not in firsts:
            firsts[count] = moves
    # Check the final state too: the last permitted move can win or lose.
    result = "goal" if bot.reached_goal(board) else "loss" if not bot.legal_moves(board) else "limit"
    return dict(seed=seed, depth=depth, endgame_depth=endgame_depth, depth_counts=depth_counts,
                think=think, result=result, moves=moves,
                caps=board.count(bot.CAP), max=max(board), cap_reached_at=firsts,
                board=list(board), seconds=round(time.monotonic() - started, 4))


def summarize(rows, depth, think):
    goals = [r for r in rows if r["result"] == "goal"]
    total_moves = sum(r["moves"] for r in rows)
    seconds = sum(r["seconds"] for r in rows)
    return dict(games=len(rows), goals=len(goals),
                losses=sum(r["result"] == "loss" for r in rows),
                limits=sum(r["result"] == "limit" for r in rows),
                win_rate=len(goals) / len(rows), depth=depth, think=think,
                mean_caps=sum(r["caps"] for r in rows) / len(rows),
                mean_moves=total_moves / len(rows),
                mean_goal_moves=sum(r["moves"] for r in goals) / len(goals) if goals else None,
                seconds=round(seconds, 4),
                seconds_per_move=seconds / total_moves if total_moves else None)


def source_hashes():
    paths = ("bot.py", "vendor/engine_adapted.cpp", "benchmark.py")
    hashes = {name: hashlib.sha256((bot.ROOT / name).read_bytes()).hexdigest() for name in paths}
    try:
        # The loaded library can be an experimental engine outside bot.ENGINE.
        hashes["loaded_engine"] = hashlib.sha256(Path(bot.NATIVE._name).read_bytes()).hexdigest()
    except (OSError, TypeError) as exc:
        raise ValueError(f"Не удалось прочитать загруженный движок для checkpoint: {exc}") from exc
    return hashes


def checkpoint(rows, config, model, sources, table):
    comparisons = [summarize(group, depth, config["think"])
                   for depth in config["depths"]
                   for group in [[r for r in rows if r["depth"] == depth]] if group]
    summary = (comparisons[0] if len(config["depths"]) == 1 and comparisons else
               dict(games=len(rows), depths=config["depths"], think=config["think"]))
    return dict(checkpoint_version=1, config=config, spawn_model=model, source_hashes=sources,
                summary=summary, comparisons=comparisons, spawn_table=table, games=rows,
                complete=len(rows) == config["games"] * len(config["depths"]))


def save_checkpoint(path, data):
    # Same-directory replace keeps the previous valid JSON if writing is interrupted.
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as f:
            temporary = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def resume_rows(path, config, model, sources, table):
    try:
        saved = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"Не удалось прочитать checkpoint {path}: {exc}") from exc
    if (not isinstance(saved, dict) or saved.get("checkpoint_version") != 1 or saved.get("config") != config or
            saved.get("spawn_model") != model or saved.get("source_hashes") != sources or
            saved.get("spawn_table") != table):
        raise ValueError("Checkpoint несовместим: изменились параметры, модель или исходники; выберите другой --out")
    rows = saved.get("games")
    if not isinstance(rows, list):
        raise ValueError("Некорректные партии в checkpoint")
    expected = {(seed, depth) for depth in config["depths"]
                for seed in range(config["seed"], config["seed"] + config["games"])}
    seen = set()
    for row in rows:
        try:
            key = (row["seed"], row["depth"])
            valid = (key in expected and key not in seen and row["think"] == config["think"] and
                     row["result"] in ("goal", "loss", "limit") and
                     0 <= row["moves"] <= config["max_moves"] and 0 <= row["caps"] <= 16 and
                     row["seconds"] >= 0)
            seen.add(key)
        except (KeyError, TypeError):
            valid = False
        if not valid:
            raise ValueError("Некорректная или повторная партия в checkpoint")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1, help="seed первой партии; далее seed+1, ...")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--depths", type=int, nargs="+", help="сравнить глубины на одинаковых seed, например 3 4 5")
    ap.add_argument("--endgame-depth", type=int, default=None,
                    help="глубина поиска при двух или более готовых плитках 11")
    ap.add_argument("--think", type=float, default=None,
                    help="бюджет на ход, с; без параметра — один проход 1e-4 без ограничения времени")
    ap.add_argument("--search-policy", choices=("legacy", "progressive"), default="progressive",
                    help="при --think: планировщик поиска, как в bot CLI")
    ap.add_argument("--max-moves", type=int, default=6000)
    ap.add_argument("--out", default=str(bot.ROOT / "benchmark.json"))
    ap.add_argument("--resume", action="store_true", help="продолжить совместимый --out, пропуская готовые партии")
    args = ap.parse_args(argv)
    depths = args.depths or [args.depth]
    if args.games < 1 or args.max_moves < 0 or any(d < 1 for d in depths):
        ap.error("games и depth должны быть положительными, max-moves — неотрицательным")
    if args.endgame_depth is not None and args.endgame_depth < 1:
        ap.error("endgame-depth должен быть положительным")
    if len(set(depths)) != len(depths):
        ap.error("depths не должны повторяться")
    if args.think is not None and args.think < 0:
        ap.error("think должен быть неотрицательным")
    if args.think is not None and args.search_policy == "progressive" and not hasattr(bot.NATIVE, "solve_budget_progressive"):
        ap.error("Движок не поддерживает progressive: пересоберите sh vendor/build.sh")
    model = bot.default_spawn_model()
    table = bot.spawn_table(model)
    config = dict(games=args.games, seed=args.seed, depths=depths, think=args.think,
                  max_moves=args.max_moves, endgame_depth=args.endgame_depth,
                  search_policy=args.search_policy if args.think is not None else None)
    try:
        sources = source_hashes()
        rows = resume_rows(args.out, config, model, sources, table) if args.resume else []
    except ValueError as exc:
        ap.error(str(exc))
    done = {(r["seed"], r["depth"]) for r in rows}
    save_checkpoint(args.out, checkpoint(rows, config, model, sources, table))
    for depth in depths:
        for seed in range(args.seed, args.seed + args.games):
            if (seed, depth) in done:
                continue
            if args.think is not None:
                row = play(seed, depth, args.think, args.max_moves, table, args.endgame_depth, policy=args.search_policy)
            elif args.endgame_depth is None:
                row = play(seed, depth, args.think, args.max_moves, table)
            else:
                row = play(seed, depth, args.think, args.max_moves, table, args.endgame_depth)
            rows.append(row)
            save_checkpoint(args.out, checkpoint(rows, config, model, sources, table))
            print(json.dumps(row, ensure_ascii=False), flush=True)
        group = [r for r in rows if r["depth"] == depth]
        print(json.dumps(summarize(group, depth, args.think), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
