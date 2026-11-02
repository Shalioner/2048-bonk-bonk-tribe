"""Freeze exactly one finalist after local plateau and before any holdout games."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import lab
subprocess.run([sys.executable,str(ROOT/"verify_results.py")],check=True)
integrity=json.loads((ROOT/"integrity.json").read_text())
assert integrity["plateau"], "Local plateau not reached"
assert not list(ROOT.glob("*/holdout.json")), "Holdout already started"
assert not (ROOT/"finalist.json").exists(), "Already frozen"
variants=[row for row in integrity["files"] if row["stage"]=="training" and row["variant"]!="baseline"]
# Integrity order is exactly the preregistered order; max retains first tie.
candidate=max(variants,key=lambda row:row["goals"])["variant"]
runner="lab.py" if candidate in lab.PLAN["variants"] else "extension.py"
paths=[ROOT/"plan.json",ROOT/"extension-plan.json",ROOT/"lab.py",ROOT/"extension.py",
       lab.WORK/"bot.py",lab.WORK/"benchmark.py",lab.WORK/"vendor/engine_adapted.cpp",lab.WORK/"spawn-model.default.json"]
for name in ("baseline",candidate):
    paths += [ROOT/name/"engine.cpp",ROOT/name/"manifest.json",ROOT/name/"training.json"]
    paths += list((ROOT/"build"/name).glob("engine.*"))
result=dict(finalist=candidate,parameters=json.loads((ROOT/candidate/"manifest.json").read_text())["parameters"],
            training_games_per_variant=lab.PLAN["training_seeds"][1]-lab.PLAN["training_seeds"][0]+1,
            baseline_training_goals=next(r["goals"] for r in integrity["files"] if r["variant"]=="baseline" and r["stage"]=="training"),
            candidate_training_goals=next(r["goals"] for r in variants if r["variant"]==candidate),
            plateau_rounds=integrity["rounds"],training_total_games=integrity["total_games"],
            frozen_at_utc=datetime.now(timezone.utc).isoformat(),
            training_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
            holdout_seeds=lab.PLAN["holdout_seeds"],holdout_games_per_arm=60,no_further_tuning=True,
            holdout_policy=lab.PLAN["holdout_decision"],
            frozen_hashes={str(p.relative_to(lab.WORK)):lab.sha(p) for p in paths},
            all_training_results={r["variant"]:lab.sha(ROOT/r["variant"]/"training.json") for r in integrity["files"] if r["stage"]=="training"},
            commands=[["python3","experiments/blindspots/lab.py","run","baseline","--stage","holdout"],
                      ["python3","experiments/blindspots/"+runner,"run",candidate,"--stage","holdout"]])
(ROOT/"finalist.json").write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps(dict(finalist=candidate,training_goals=result["candidate_training_goals"],training_games=integrity["total_games"])))
