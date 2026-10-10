import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from cpu_blackbox.collector import doctor
from cpu_blackbox.query import query
from cpu_blackbox.storage import Config, NS, Store, anchor

@unittest.skipUnless(platform.system()=="Linux" and shutil.which("perf") and shutil.which("gcc"), "real Linux perf and gcc required")
class LinuxIntegration(unittest.TestCase):
    def test_capture_rotate_replay_hotspot(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            exe=root/"hotspot"
            gate=root/"start-hotspot"
            source=Path(__file__).resolve().parents[1]/"tools"/"hotspot.c"
            subprocess.run(["gcc","-g","-O0","-fno-omit-frame-pointer",str(source),"-o",str(exe)],check=True)
            target=subprocess.Popen([str(exe),"12",str(gate)])
            capture=None
            try:
                cfg=Config(rotation_seconds=1,min_free_bytes=1,disk_check_seconds=0.2)
                check=doctor(target.pid,cfg)
                if not check["ready"]: self.skipTest(json.dumps(check))
                config=root/"config.json"
                config.write_text(json.dumps(asdict(cfg)))
                with (root/"collector.log").open("w") as log:
                    capture=subprocess.Popen([sys.executable,"-m","cpu_blackbox","--data-dir",str(root/"data"),"--config",str(config),"collect","--pid",str(target.pid),"--duration","8"],stdout=log,stderr=log)
                    deadline=time.monotonic()+20
                    while time.monotonic()<deadline:
                        if capture.poll() is not None: self.fail((root/"collector.log").read_text())
                        state=root/"data"/"status.json"
                        if state.exists() and json.loads(state.read_text())["state"]=="RUNNING": break
                        time.sleep(.05)
                    else: self.fail("collector did not become ready")
                    point=anchor()
                    gate.touch()  # Hot phase starts only after the actual collector is ready.
                    capture.wait(timeout=45)
                self.assertEqual(capture.returncode,0,(root/"collector.log").read_text())
                store=Store(root/"data")
                try:
                    self.assertGreaterEqual(store.db.execute("SELECT COUNT(*) FROM segments WHERE state='AVAILABLE'").fetchone()[0],2)
                    # Query the interior of the former hot phase after it has finished.
                    report=query(store,cfg,point["wall"]+int(2.2*NS),point["wall"]+int(4.8*NS),root/"report")
                    self.assertGreater(report["sample_count"],0)
                    self.assertIn("hot_leaf",(root/"report"/"stacks.folded").read_text())
                    self.assertTrue((root/"report"/"flamegraph.svg").exists())
                finally: store.close()
            finally:
                for child in (capture,target):
                    if child is not None:
                        if child.poll() is None: child.terminate()
                        try: child.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            child.kill(); child.wait()
