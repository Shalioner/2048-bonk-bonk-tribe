"""Small diagnostic on training snapshots only; not a candidate game/control."""
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import lab
import benchmark
import bot
s=(ROOT/"baseline/engine.cpp").read_text()
s=lab.replace_once(s,"float best = 0.0f; bool legal=false, nonnegative=false;", "float best = -1e9f; bool legal=false, nonnegative=false;")
s=lab.replace_once(s,"float best=-1; int result=-1;", "float best=-1e30f; int result=-1;")
cpp=ROOT/"signed-reference.cpp"
cpp.write_text(s)
binary=ROOT/"build"/("signed-reference.dylib" if sys.platform=="darwin" else "signed-reference.so")
flags=["clang++","-O3","-std=c++11","-dynamiclib"] if sys.platform=="darwin" else ["g++","-O3","-std=c++11","-shared","-fPIC","-pthread"]
subprocess.run(flags+["-I",str(lab.WORK/"vendor"),str(cpp),"-o",str(binary)],check=True)
reference=bot.load_engine(binary)
base=lab.load("baseline").NATIVE
training=json.loads((ROOT/"baseline/training.json").read_text())
corpus=[]
for game in training["games"][:3]:
    for caps,board in sorted(game["cap_boards"].items()):
        if int(caps)<3:
            corpus.append(dict(seed=game["seed"],caps=int(caps),board=board))
rows=[]
for row in corpus:
    board=tuple(row["board"])
    scores={}
    choices={}
    for name,engine in (("baseline",base),("signed",reference)):
        bot.NATIVE=engine
        benchmark.apply_table(training["spawn_table"])
        scores[name]={str(d):float(engine.score_levels(bot.pack(board),bot.DIRECTIONS[d],3)) for d,_ in bot.legal_moves(board)}
        choices[name]=benchmark.choose_fixed(board,3)
    rows.append(dict(**row,scores=scores,choices=choices,changed=choices["baseline"]!=choices["signed"]))
result=dict(source_sha256=lab.sha(cpp),engine_sha256=lab.sha(binary),
            policy="signed child maximum; terminal no-legal-move=-1e9; goal=+1e9; root accepts negative scores. Diagnostic changes loss semantics; not a pure numerical patch.",
            corpus="first <=3 completed baseline training games, first/second cap snapshots; no control; exact boards included",
            rows=rows,changed=sum(r["changed"] for r in rows))
(ROOT/"numerical-probe.json").write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps(dict(positions=len(rows),changed=result["changed"])))
