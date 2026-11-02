import json,random,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]))
import bot
rng=random.Random(202611021)
ranks=[r for r in range(1,16) if r!=11]
checked=0
for empties,iterations in [(1,5000),(2,1000),(3,1000)]:
 for _ in range(iterations):
  board=[rng.choice(ranks) for i in range(16)];slots=rng.sample(range(16),2+empties)
  for i in slots[:2]:board[i]=11
  for i in slots[2:]:board[i]=0
  existing=any(board[a]!=0 and board[a]<11 and board[a]==board[b] for a in range(16) for b in ([a+1] if a%4<3 else [])+([a+4] if a<12 else []))
  for pos in slots[2:]:
   neighbors=[q for q in (pos-4,pos+4,pos-1,pos+1) if 0<=q<16 and abs(q//4-pos//4)+abs(q%4-pos%4)==1]
   mergeable={board[q] for q in neighbors if 0<board[q]<11}
   for rank in range(1,16):
    child=list(board);child[pos]=rank;child=tuple(child)
    actual=child.count(11)<3 and not bot.legal_moves(child)
    predicted=empties==1 and not existing and rank not in mergeable and rank!=11
    assert actual==predicted,(board,pos,rank,actual,predicted)
    if empties>=2:assert bot.legal_moves(child)
    checked+=1
print(json.dumps(dict(generator_seed=202611021,boards=7000,spawn_children_checked=checked,exact_equal=True,scope='synthetic board identity; no full games or independent win-rate control'),indent=2))
