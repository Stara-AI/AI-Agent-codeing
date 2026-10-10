"""Package committed sources plus complete Git history; publish ZIP atomically."""
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
root=Path(__file__).resolve().parents[1]
if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=root).strip():
    raise SystemExit('Commit tracked changes before packaging; ZIP must match Git history.')
revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root).decode().strip()
bundle=root.parent/'cpu-blackbox-history.bundle'
output=root.parent/'cpu-blackbox-delivery.zip'
temp_bundle=bundle.with_suffix('.bundle.tmp')
temp_zip=output.with_suffix('.zip.tmp')
try:
    subprocess.run(['git','bundle','create',str(temp_bundle),'--all'],cwd=root,check=True)
    subprocess.run(['git','archive','--format=zip','--prefix=cpu-blackbox/','--output',str(temp_zip),revision],cwd=root,check=True)
    with zipfile.ZipFile(temp_zip,'a',zipfile.ZIP_DEFLATED) as archive:
        archive.write(temp_bundle,'cpu-blackbox-history.bundle')
        archive.writestr('DELIVERY.json',json.dumps({'git_commit':revision,'packaged_at':datetime.now(timezone.utc).isoformat()},indent=2))
    with zipfile.ZipFile(temp_zip) as archive:
        bad=archive.testzip()
        if bad: raise RuntimeError('corrupt member: '+bad)
    temp_bundle.replace(bundle)
    temp_zip.replace(output)
finally:
    temp_bundle.unlink(missing_ok=True)
    temp_zip.unlink(missing_ok=True)
print(str(output))
