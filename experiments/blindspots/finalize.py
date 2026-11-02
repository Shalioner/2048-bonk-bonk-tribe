"""Validate frozen provenance and compare the sole prespecified holdout finalist."""
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import lab
sys.path.insert(0,str(ROOT.parent/"strategy"))
from stats import compare
frozen=json.loads((ROOT/"finalist.json").read_text())
for relative,digest in frozen["frozen_hashes"].items():
    assert lab.sha(lab.WORK/relative)==digest, relative
for name,digest in frozen["all_training_results"].items():
    assert lab.sha(ROOT/name/"training.json")==digest, name
subprocess.run([sys.executable,str(ROOT/"verify_results.py")],check=True)
integrity=json.loads((ROOT/"integrity.json").read_text())
assert integrity["plateau"]
name=frozen["finalist"]
a=json.loads((ROOT/"baseline/holdout.json").read_text())
b=json.loads((ROOT/name/"holdout.json").read_text())
assert a["complete"] and b["complete"]
assert len(a["games"])==len(b["games"])==frozen["holdout_games_per_arm"]
summary=compare(a,b)
training=set(range(lab.PLAN["training_seeds"][0],lab.PLAN["training_seeds"][1]+1))
control={g["seed"] for g in a["games"]}
assert not training.intersection(control)
assert control==set(range(frozen["holdout_seeds"][0],frozen["holdout_seeds"][1]+1))
subprocess.run(["git","diff","--exit-code",lab.PLAN["baseline_commit"],"--","bot.py","benchmark.py","vendor/engine_adapted.cpp"],check=True)
summary.update(finalist=name,parameters=frozen["parameters"],frozen_provenance_verified=True,
               training_games=frozen["training_total_games"],control_games=2*len(a["games"]),
               total_games=integrity["total_games"],unique_training_seeds=len(training),
               unique_holdout_seeds=len(control),no_seed_overlap=True,limits=0,
               offline_improvement=summary["candidate_goals"]>summary["baseline_goals"] and summary["one_sided_p"]<=.05,
               default_changed=False,phone_or_8_10_validated=False)
summary["difference_percentage_points"]=100*(summary["candidate_goals"]-summary["baseline_goals"])/len(control)
summary["cost"]={}
for label,data in (("baseline",a),("candidate",b)):
    rows=data["games"]
    summary["cost"][label]=dict(nodes=sum(g["nodes"] for g in rows),
        clipped_maxima=sum(g["clipped_nodes"] for g in rows),moves=sum(g["moves"] for g in rows),
        depth4_moves=sum(g["depth_counts"].get("4",0) for g in rows),
        reached_two=sum(any(int(k)>=2 for k in g["cap_boards"]) for g in rows),
        seconds=sum(g["seconds"] for g in rows))
summary["cost"]["node_ratio"]=summary["cost"]["candidate"]["nodes"]/summary["cost"]["baseline"]["nodes"]
(ROOT/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps({key:summary[key] for key in ("finalist","games","baseline_goals","candidate_goals","one_sided_p","offline_improvement","total_games")},indent=2))
