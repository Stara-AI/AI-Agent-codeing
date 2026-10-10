"""Cross-platform synthetic recorder. Does not emulate a Linux kernel or perf."""
import json
import math
import os
import shutil
import signal
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from .storage import BlackboxError, NS, Store, anchor, disk_bytes, file_lock, write_json


def simulate(root, cfg, duration=10):
    if not math.isfinite(duration) or duration <= 0:
        raise BlackboxError("simulation duration must be finite and positive")
    with file_lock(Path(root)/"collector.lock",blocking=False):
        store=Store(root)
        sid=uuid.uuid4().hex
        folder=store.root/sid
        folder.mkdir()
        point=anchor()
        options=dict(asdict(cfg),source="synthetic")
        store.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)",
                         (sid,"SIMULATED-NOT-LINUX",os.getpid(),"synthetic",point["mono"],None,json.dumps(options),"RUNNING"))
        store.db.commit()
        store.add_anchor(sid,point,cfg.clock_jump_ns)
        stopping=False
        state="STOPPED"
        old_handlers={}
        active=folder/"synthetic.active"
        stream=active.open("w",encoding="utf-8")
        first=last=None
        index=0
        def stop(signum,frame):
            nonlocal stopping
            stopping=True
        def seal():
            nonlocal stream,first,last,index
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            if first is not None:
                target=folder/f"samples-{index:06d}.synthetic"
                active.rename(target)
                store.segment(target,sid,first,last)
                index+=1
            else:
                active.unlink(missing_ok=True)
            first=last=None
        def publish():
            write_json(store.root/"status.json",{"state":state if stopping else "RUNNING","source":"synthetic",
                       "warning":"SIMULATION: not real Linux/perf samples", "session":sid,"collector_pid":os.getpid(),
                       "updated_wall_ns":time.time_ns(),"data_bytes":disk_bytes(store.root),"free_bytes":shutil.disk_usage(store.root).free})
        scenario_point=anchor()
        started=scenario_point["mono"]/NS
        rotate=started+cfg.rotation_seconds
        check=started
        try:
            for sig in (signal.SIGINT,signal.SIGTERM):
                old_handlers[sig]=signal.signal(sig,stop)
            while not stopping:
                now=time.monotonic()
                elapsed=now-started
                if elapsed >= duration:
                    break
                if now>=check:
                    store.add_anchor(sid,anchor(),cfg.clock_jump_ns)
                    store.cleanup(cfg)
                    if disk_bytes(store.root)>=cfg.max_data_bytes or shutil.disk_usage(store.root).free<cfg.min_free_bytes:
                        state="DISK_BUDGET"
                        break
                    publish()
                    check=now+cfg.disk_check_seconds
                if now>=rotate:
                    seal()
                    stream=active.open("w",encoding="utf-8")
                    rotate=now+cfg.rotation_seconds
                stamp=time.monotonic_ns()
                sample_elapsed=(stamp-scenario_point["mono"])/NS
                leaf="hot_leaf" if duration*0.3<=sample_elapsed<duration*0.6 else "normal_work"
                stream.write(f"simulated-service {os.getpid()} [000] {stamp//NS}.{stamp%NS:09d}: cpu-clock: 100 {leaf} (synthetic-app)\n"
                             f"        100 {leaf} (synthetic-app)\n        200 request_handler (synthetic-app)\n        300 main (synthetic-app)\n\n")
                first=stamp if first is None else first
                last=stamp
                time.sleep(min(1/cfg.frequency_hz,max(0,duration-elapsed)))
        except BaseException:
            state="FAILED"
            raise
        finally:
            try:
                seal()
                stopping=True
                end=anchor()
                store.add_anchor(sid,end,cfg.clock_jump_ns)
                store.db.execute("UPDATE sessions SET state=?,end_mono=? WHERE id=?",(state,end["mono"],sid))
                store.db.commit()
                publish()
                manifest={"source":"synthetic", "warning":"Synthetic stack records, not Linux CPU measurements", "session":sid,
                          "segments":index,"duration_seconds":time.monotonic()-started,
                          "hot_window_start":datetime.fromtimestamp((scenario_point["wall"]+int(duration*.35*NS))/NS,timezone.utc).isoformat(),
                          "hot_window_end":datetime.fromtimestamp((scenario_point["wall"]+int(duration*.55*NS))/NS,timezone.utc).isoformat(),
                          "query_note":"Query uses the interior of the hot phase, excluding transition sampling", "state":state}
                write_json(store.root/"simulation.json",manifest)
            finally:
                if not stream.closed:
                    stream.close()
                for sig,handler in old_handlers.items():
                    signal.signal(sig,handler)
                store.close()
        print(json.dumps(manifest,indent=2))
        return 75 if state=="DISK_BUDGET" else 0
