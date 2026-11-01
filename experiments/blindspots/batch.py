"""Run at most two independent offline processes; each owns its variant output."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parent
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("variants", nargs="+")
ap.add_argument("--stage", choices=["training","holdout"], default="training")
ap.add_argument("--resume", action="store_true")
args = ap.parse_args()
def run(name):
    command = [sys.executable,str(ROOT/"lab.py"),"run",name,"--stage",args.stage]
    if args.resume:
        command.append("--resume")
    with (ROOT/(name+"-"+args.stage+".log")).open("w") as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    data=json.loads((ROOT/name/(args.stage+".json")).read_text())
    print(json.dumps(dict(variant=name,stage=args.stage,summary=data["summary"])),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(run,args.variants))
