import hashlib,json,statistics,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'baseline'))
import bot
from search_benchmark import load,stats,corpus
names=['baseline','goal']
paths=[ROOT/'baseline/vendor/engine.dylib',ROOT/'goal-screen/engine.dylib']
libs=[load(p,bot.spawn_table(bot.default_spawn_model())) for p in paths]
boards=corpus(libs[0],bot.spawn_table(bot.default_spawn_model()),seed=2048)
hashes=[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
rows=[]
for index,board in enumerate(boards):
 times={n:[] for n in names};reference=None
 for repeat in range(2):
  order=list(range(2));shift=(index+repeat)%2;order=order[shift:]+order[:shift]
  for which in order:
   start=time.perf_counter();scores=[libs[which].score_levels_thresh(board,d,8,1e-4) for d in range(4)];elapsed=time.perf_counter()-start
   if reference is None:reference=scores
   assert scores==reference,(index,names[which],scores,reference)
   times[names[which]].append(elapsed)
 rows.append(dict(index=index,board=f'{board:016x}',seconds=times,scores=reference))
 summary={n:stats([v for r in rows for v in r['seconds'][n]]) for n in names}
 (ROOT/'goal-screen-results.json').write_text(json.dumps(dict(complete=False,rows=rows,summary=summary,sha256=hashes),indent=2)+'\n')
 print(index,{n:round(statistics.median(v),5) for n,v in times.items()},flush=True)
assert hashes==[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
(ROOT/'goal-screen-results.json').write_text(json.dumps(dict(complete=True,rows=rows,summary=summary,sha256=hashes),indent=2)+'\n')
print(json.dumps(summary,indent=2))
