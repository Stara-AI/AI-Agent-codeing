"""Run the complete synthetic capture -> rotation -> historical query demo."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

project=Path(__file__).resolve().parents[1]

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-root",default="artifacts/simulation-run")
args=parser.parse_args()
root=Path(args.output_root).resolve()
root.mkdir(parents=True,exist_ok=False)
data=root/"data"
child_env=dict(os.environ,PYTHONIOENCODING="utf-8")
run=subprocess.run([sys.executable,"-m","cpu_blackbox","--data-dir",str(data),"simulate","--duration","6","--rotation-seconds","1"],capture_output=True,text=True,encoding="utf-8",env=child_env,cwd=project,timeout=20)
(root/"capture-output.txt").write_text(run.stdout+run.stderr,encoding="utf-8")
if run.returncode: raise SystemExit(run.returncode)
manifest=json.loads((data/"simulation.json").read_text(encoding="utf-8"))
from analyze_simulation import analyze
comparison=analyze(data,root/"report")
report=json.loads((root/"report"/"incident"/"report.json").read_text(encoding="utf-8"))
stacks=(root/"report"/"incident"/"stacks.folded").read_text(encoding="utf-8")
(root/"query-output.txt").write_text(json.dumps(report,indent=2),encoding="utf-8")
result={"source":"SYNTHETIC SIMULATION; no Linux kernel/perf", "capture_exit_code":run.returncode,
        "query_exit_code":3 if report["status"]=="partial" else 0,"segments":manifest["segments"],"sample_count":report["sample_count"],
        "hotspot_replayed_after_end":"hot_leaf" in stacks,"normal_phase_excluded":"normal_work" not in stacks,
        "report":str(root/"report"/"index.html"),"incident_report":str(root/"report"/"incident"/"index.html"),
        "comparison_report":comparison["report"]}
(root/"result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps(result,indent=2))
if not result["hotspot_replayed_after_end"] or not result["normal_phase_excluded"]: raise SystemExit(2)
