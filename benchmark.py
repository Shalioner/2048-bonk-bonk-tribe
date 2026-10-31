"""Офлайн-симуляция без телефона и ADB.

Правила режима: плитки уровня 11 (2048) не сливаются, цель — три такие плитки одновременно.
Новые плитки появляются в случайной пустой клетке; уровень выбирается по строке
spawn-model.default.json для текущей стадии (максимальный уровень после хода).
Ходы выбирает тот же движок, что и на телефоне (bot.choose).

Пример: python3 benchmark.py --games 10 --depth 4
Без --think — один проход с порогом 1e-4 без ограничения времени (воспроизводимо, не зависит
от скорости машины). С --think N — как на телефоне: проходы с ослабляемым отсечением в пределах
жёсткого бюджета N секунд. Результаты пишутся в benchmark.json (в .gitignore).
"""
import argparse, json, random, time
import bot


def spawn(board, rng, table):
    empty = [i for i, v in enumerate(board) if not v]
    cell = rng.choice(empty)
    level = rng.choices(range(1, 16), weights=table[max(board)])[0]
    out = list(board)
    out[cell] = level
    return tuple(out)


def play(seed, depth, think, max_moves, table):
    rng = random.Random(seed)
    board = spawn(spawn((0,) * 16, rng, table), rng, table)
    started = time.monotonic()
    firsts = {}
    result = "limit"
    moves = 0
    for moves in range(max_moves):
        if bot.reached_goal(board):
            result = "goal"
            break
        if think is None:
            saved, bot.THRESHOLDS = bot.THRESHOLDS, (1e-4,)
            try:
                d = bot.choose(board, depth, 1e6)
            finally:
                bot.THRESHOLDS = saved
        else:
            d = bot.choose(board, depth, think)
        if d is None:
            result = "loss"
            break
        board = spawn(bot.move(board, d), rng, table)
        count = board.count(bot.CAP)
        if count and count not in firsts:
            firsts[count] = moves + 1
    return dict(seed=seed, depth=depth, think=think, result=result, moves=moves,
                caps=board.count(bot.CAP), max=max(board), cap_reached_at=firsts,
                seconds=round(time.monotonic() - started, 1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1, help="seed первой партии; далее seed+1, ...")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--think", type=float, default=None,
                    help="бюджет на ход, с; без параметра — один проход 1e-4 без ограничения времени")
    ap.add_argument("--max-moves", type=int, default=6000)
    ap.add_argument("--out", default=str(bot.ROOT / "benchmark.json"))
    args = ap.parse_args()
    table = bot.spawn_table(bot.default_spawn_model())
    rows = []
    for seed in range(args.seed, args.seed + args.games):
        row = play(seed, args.depth, args.think, args.max_moves, table)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    goals = sum(r["result"] == "goal" for r in rows)
    summary = dict(games=len(rows), goals=goals, depth=args.depth, think=args.think)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    with open(args.out, "w") as f:
        json.dump(dict(summary=summary, games=rows), f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
