"""Offline comparison; no ADB, no phone interaction."""
import json, random, time
import bot
results=[]
for name,choose in [("legacy",bot.choose_legacy),("native",bot.choose)]:
    for seed in range(3):
        rng=random.Random(seed)
        def spawn(board):
            board=list(board)
            i=rng.choice([i for i,v in enumerate(board) if not v])
            board[i]=1 if rng.random()<.9 else 2
            return tuple(board)
        board=spawn(spawn((0,)*16));start=time.monotonic()
        for step in range(500):
            d=choose(board)
            if d is None:break
            board=spawn(bot.move(board,d))
        row=dict(engine=name,seed=seed,moves=step+1,max=max(board),empty=board.count(0),seconds=round(time.monotonic()-start,2))
        results.append(row);print(json.dumps(row),flush=True)
(bot.ROOT/"benchmark.json").write_text(json.dumps(results,indent=2))
