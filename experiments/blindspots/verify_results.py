"""Verify all available result artifacts and deterministic training selection."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import lab
import benchmark
import bot

variants=dict(lab.PLAN["variants"])
round_plan=list(lab.PLAN["rounds"])
extension=None
if (ROOT/"extension-plan.json").exists():
    import extension
    variants.update(extension.PLAN["variants"])
    round_plan.extend(extension.PLAN["rounds"])
files=[]
for name,params in variants.items():
    folder=ROOT/name
    if not (folder/"manifest.json").exists():
        continue
    runner=lab if name in lab.PLAN["variants"] else extension
    plan_file="plan.json" if runner is lab else "extension-plan.json"
    manifest=json.loads((folder/"manifest.json").read_text())
    assert manifest["cpp_sha256"]==lab.sha(folder/"engine.cpp")
    assert manifest["runner_sha256"]==lab.sha(Path(runner.__file__))
    assert (folder/"engine.cpp").read_text()==lab.source(params)
    for stage in ("training","holdout"):
        p=folder/(stage+".json")
        if not p.exists():
            continue
        data=json.loads(p.read_text())
        low,high=lab.PLAN["training_seeds" if stage=="training" else "holdout_seeds"]
        assert data["complete"]
        assert data["experiment"]["variant"]==name and data["experiment"]["parameters"]==params
        assert data["experiment"]["manifest"]==manifest
        for source in ("bot.py","benchmark.py","vendor/engine_adapted.cpp"):
            assert data["source_hashes"][source]==lab.PLAN["source_sha256"][source]
        rows=data["games"]
        assert [g["seed"] for g in rows]==list(range(low,high+1))
        assert data["source_hashes"]["loaded_engine"]==manifest["engine_sha256"]
        assert data["source_hashes"]["cpp"]==manifest["cpp_sha256"]
        assert data["source_hashes"]["lab"]==lab.sha(Path(runner.__file__))
        assert data["source_hashes"]["plan"]==lab.sha(ROOT/plan_file)
        assert data["spawn_table"]==bot.spawn_table(bot.default_spawn_model())
        assert data["summary"]==benchmark.summarize(rows,3,None)
        for g in rows:
            b=tuple(g["board"])
            assert len(b)==16 and all(isinstance(x,int) and 0<=x<=11 for x in b)
            assert g["caps"]==b.count(11) and g["max"]==max(b)
            assert sum(g["depth_counts"].values())==g["moves"]
            assert g["result"] in ("goal","loss"), "censored result"
            assert (g["result"]=="goal")==bot.reached_goal(b)
            if g["result"]=="loss": assert not bot.legal_moves(b)
            assert g["clipped_nodes"]<=g["nodes"]
            for caps,board in g["cap_boards"].items():
                assert board.count(11)==int(caps)
                assert 1<=g["cap_reached_at"][caps]<=g["moves"]
            if "focus_empty" in params:
                base=json.loads((ROOT/"baseline"/(stage+".json")).read_text())
                baseline=next(b for b in base["games"] if b["seed"]==g["seed"])
                for caps in ("1","2"):
                    assert g["cap_boards"].get(caps)==baseline["cap_boards"].get(caps)
                    assert g["cap_reached_at"].get(caps)==baseline["cap_reached_at"].get(caps)
                if "2" not in g["cap_boards"]:
                    assert g["depth_counts"].get("4",0)==0
        files.append(dict(variant=name,stage=stage,games=len(rows),goals=data["summary"]["goals"],
                          clips=sum(g["clipped_nodes"] for g in rows),nodes=sum(g["nodes"] for g in rows)))

incumbent="baseline"
best_goals=next(x["goals"] for x in files if x["variant"]=="baseline" and x["stage"]=="training")
rounds=[]
no_gain=0
for names in round_plan:
    available=[x for x in files if x["variant"] in names and x["stage"]=="training"]
    if len(available)!=len(names): break
    before=best_goals
    for name in names:
        goals=next(x["goals"] for x in available if x["variant"]==name)
        if goals>best_goals:
            incumbent,best_goals=name,goals
    no_gain=no_gain+1 if best_goals==before else 0
    rounds.append(dict(variants={x["variant"]:x["goals"] for x in available},incumbent=incumbent,goals=best_goals,no_gain_streak=no_gain))
result=dict(files=files,total_games=sum(x["games"] for x in files),rounds=rounds,
            incumbent=incumbent,training_goals=best_goals,plateau=(len(rounds)>=3 and no_gain>=2) or len(rounds)==len(round_plan) and len(round_plan)>3)
(ROOT/"integrity.json").write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps({key:result[key] for key in ("total_games","incumbent","training_goals","plateau")},indent=2))
