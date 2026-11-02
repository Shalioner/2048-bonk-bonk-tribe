import ctypes as C,json,hashlib,sys,pathlib
archive=pathlib.Path('/Users/shalioner/orca/projects/Всякое разное/output/2048-td-port-20261102/worktree')
source=archive/'experiments/td/holdout.json'
data=json.loads(source.read_text());boards=[[1,1]+[0]*14]
for row in [g for g in data['games'] if g['arm']=='heuristic'][:10]:
 boards += list(row['cap_boards'].values())+[row['board']]
boards += [[11,11,11]+[0]*13]
model=json.loads((archive/'spawn-model.default.json').read_text());stages=sorted(map(int,model['stages']))
table=[float(v) for s in range(16) for v in (list(model['stages'][str(max((k for k in stages if k<=s),default=stages[0]))])+[0]*15)[:15]]
def run(path):
 lib=C.CDLL(str(path));lib.init_tables()
 lib.set_spawn_table.argtypes=[C.POINTER(C.c_float),C.c_int,C.c_int];assert lib.set_spawn_table((C.c_float*240)(*table),16,15)==0
 lib.score_levels_thresh.argtypes=[C.c_uint64,C.c_int,C.c_int,C.c_float];lib.score_levels_thresh.restype=C.c_float
 lib.execute_move.argtypes=[C.c_int,C.c_uint64];lib.execute_move.restype=C.c_uint64
 return [dict(board=b,scores=[lib.score_levels_thresh(sum(v<<(4*i) for i,v in enumerate(b)),m,d,t) for m in range(4)],depth=d,threshold=t,moves=[lib.execute_move(m,sum(v<<(4*i) for i,v in enumerate(b))) for m in range(4)]) for b in boards for d in [1,3] for t in [0.0,1e-4]]
old=archive/'vendor/engine.dylib';new=pathlib.Path(sys.argv[1]).resolve();left,right=run(old),run(new);assert left==right,'pure evaluator/search differs'
result=dict(boards=len(boards),comparisons=len(left),exact_equal=True,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),old_engine_sha256=hashlib.sha256(old.read_bytes()).hexdigest(),new_engine_sha256=hashlib.sha256(new.read_bytes()).hexdigest(),scope='fixed-depth parity; old heuristic selected, no ML; not a new win-rate control',rows=right)
pathlib.Path(sys.argv[2]).write_text(json.dumps(result,indent=2)+'\n');print({k:v for k,v in result.items() if k!='rows'})
