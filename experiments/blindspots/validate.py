"""Short contract checks, numerical witness and runner parity. No full games."""
import importlib.util
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import lab
import bot
import benchmark

original = bot.NATIVE
base = lab.load("baseline").NATIVE
model = bot.default_spawn_model()
table = bot.spawn_table(model)
benchmark.apply_table(table)
rng = random.Random(742)
boards = [tuple(rng.randrange(0, 12) for _ in range(16)) for _ in range(80)]
boards += [(11,11,10,10)+(0,)*12, (11,11,11)+(0,)*13,
           (11,11,4,3,2,1,0,0,3,2,1,0,4,3,2,1)]
for board in boards:
    for d, moved in bot.legal_moves(board):
        assert base.execute_move(bot.DIRECTIONS[d], bot.pack(board)) == bot.pack(moved)
        a = original.score_levels(bot.pack(board), bot.DIRECTIONS[d], 1)
        b = base.score_levels(bot.pack(board), bot.DIRECTIONS[d], 1)
        assert a == b, (board,d,a,b)

for name, params in lab.PLAN["variants"].items():
    bot = lab.load(name)
    benchmark.apply_table(table)
    for board in boards:
        assert bool(bot.NATIVE.goal_levels(bot.pack(board))) == bot.reached_goal(board)
        for d, moved in bot.legal_moves(board):
            assert bot.NATIVE.execute_move(bot.DIRECTIONS[d], bot.pack(board)) == bot.pack(moved)
        bonus = params.get("progress", 0) * board.count(11)
        if board.count(11) == 2:
            bonus += params.get("mobility", 0) * len(bot.legal_moves(board))
        if bot.reached_goal(board):
            bonus = 0
        a = bot.NATIVE.lab_heur(bot.pack(board))
        b = base.lab_heur(bot.pack(board)) + bonus
        assert abs(a-b) <= .5, (name,board,a,b)
        rotated = tuple(board[(3-c)*4+r] for r in range(4) for c in range(4))
        assert abs(a - bot.NATIVE.lab_heur(bot.pack(rotated))) <= .5

bot = lab.load("baseline")
benchmark.apply_table(table)
witness = (10,5,1,8,9,11,9,9,7,8,8,8,8,7,4,7)
scores, signed = {}, {}
for d, after in bot.legal_moves(witness):
    scores[str(d)] = bot.NATIVE.score_levels(bot.pack(witness), bot.DIRECTIONS[d], 1)
    # Independent chance enumeration with a signed maximum; no moves -> baseline loss0.
    empty = [i for i,v in enumerate(after) if not v]
    total = 0
    for cell in empty:
        for rank,p in enumerate(table[max(after)], 1):
            if not p:
                continue
            spawned = list(after); spawned[cell] = rank
            legal = bot.legal_moves(tuple(spawned))
            vals = [base.lab_heur(bot.pack(moved)) for _,moved in legal]
            total += (p / sum(table[max(after)])) * (max(vals) if vals else 0) / len(empty)
    signed[str(d)] = total
assert max(scores.values()) < 1e-5 and all(v < 0 for v in signed.values())
assert signed['1'] > signed['0']

# Same transitions and deterministic choices as the existing simulator.
old_limit = lab.PLAN["max_moves"]
lab.PLAN["max_moves"] = 2
row = lab.play("baseline", 202611009, table)
reference = benchmark.play(202611009,3,None,2,table)
for key in ("seed","result","moves","caps","max","board","depth_counts"):
    assert row[key] == reference[key], key
lab.PLAN["max_moves"] = old_limit
result = dict(boards=len(boards), variants=len(lab.PLAN["variants"]),
              baseline_score_parity=True, move_goal_parity=True, bonus_formula_symmetry=True,
              runner_two_move_parity=True, numerical_witness=dict(board=list(witness), baseline=scores, signed_reference=signed),
              technical_seed=202611009, technical_seed_excluded=True)
(ROOT/"validation.json").write_text(json.dumps(result, indent=2)+"\n")
print(json.dumps(result, indent=2))
