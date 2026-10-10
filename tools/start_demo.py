"""Run the portable demo from any directory; optionally open its offline report."""
import argparse
import json
import os
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

project = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--no-browser", action="store_true")
parser.add_argument("--no-pause", action="store_true", help=argparse.SUPPRESS)
args = parser.parse_args()
output = project / "runs" / ("demo-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
run = subprocess.run([sys.executable, str(project / "tools/run_simulation_demo.py"), "--output-root", str(output)],
                     cwd=project, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
if run.returncode:
    raise SystemExit(run.returncode)
result = json.loads((output / "result.json").read_text(encoding="utf-8"))
page = Path(result["report"])
print("Report: " + str(page))
if not args.no_browser:
    webbrowser.open(page.as_uri())
