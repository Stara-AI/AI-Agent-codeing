"""Linux paired A/B benchmark against a persistent service PID.
The load runner command must print JSON metrics, not arbitrary text.
"""
import argparse
import json
import math
import platform
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cpu_blackbox.storage import Config, write_json


def evaluate(pairs):
    if len(pairs)<5: return {"verdict":"INSUFFICIENT", "reason":"at least five paired rounds required"}
    losses=[(a["throughput"]-b["throughput"])/a["throughput"]*100 for a,b in pairs]
    mean=statistics.mean(losses)
    # Conservative 95% t multiplier for >=5 pairs. Small samples are not silently trusted.
    margin=2.776*statistics.stdev(losses)/math.sqrt(len(losses))
    upper=mean+margin
    enough=all(min(a["duration_seconds"],b["duration_seconds"]) >= 2*a["rotation_seconds"] for a,b in pairs)
    verdict="PASS_FOR_THIS_WORKLOAD" if upper<3 and enough else ("FAIL" if mean>=3 else "INCONCLUSIVE")
    return {"verdict":verdict,"mean_throughput_loss_pct":mean,"upper_95_pct":upper,
            "per_pair_loss_pct":losses,"includes_two_rotations_per_round":enough,
            "scope":"Only supplied workload/configuration; tail latency and production SLO require separate review."}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid",type=int,required=True)
    parser.add_argument("--runner-json",required=True,help='JSON argv list for load runner; stdout must be metrics JSON')
    parser.add_argument("--config")
    parser.add_argument("--rounds",type=int,default=5)
    parser.add_argument("--warmup",type=float,default=3)
    parser.add_argument("--output",default="artifacts/benchmark")
    parser.add_argument("--timeout",type=float,default=300)
    args=parser.parse_args()
    if platform.system()!="Linux": parser.error("real perf benchmark requires Linux")
    if args.rounds<5 or args.pid<=0 or args.warmup<0: parser.error("invalid benchmark parameters")
    runner=json.loads(args.runner_json)
    if not isinstance(runner,list) or not runner or not all(isinstance(v,str) for v in runner): parser.error("runner must be a JSON argv list")
    cfg=Config.load(args.config)
    output=Path(args.output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    config=output/"config.json"
    write_json(config,cfg.__dict__)
    pairs=[]
    for index in range(args.rounds):
        metrics={}
        for mode in (["baseline","profiled"] if index%2==0 else ["profiled","baseline"]):
            child=None
            logfile=None
            try:
                if mode=="profiled":
                    root=output/f"data-{index}"
                    logfile=(output/f"collector-{index}.log").open("w")
                    child=subprocess.Popen([sys.executable,"-m","cpu_blackbox","--data-dir",str(root),"--config",str(config),"collect","--pid",str(args.pid)],stdout=logfile,stderr=logfile)
                    deadline=time.monotonic()+30
                    while time.monotonic()<deadline:
                        if child.poll() is not None: raise RuntimeError("collector failed; inspect log")
                        state=root/"status.json"
                        if state.exists() and json.loads(state.read_text())["state"]=="RUNNING": break
                        time.sleep(0.1)
                    else: raise RuntimeError("collector did not become ready")
                # Warm up with actual requests, then discard their measurements.
                subprocess.run(runner,capture_output=True,text=True,check=True,timeout=args.timeout)
                time.sleep(args.warmup)
                run=subprocess.run(runner,capture_output=True,text=True,check=True,timeout=args.timeout)
                value=json.loads(run.stdout)
                for key in ("throughput","p95_ms","p99_ms","error_rate","duration_seconds"):
                    if key not in value or not isinstance(value[key],(int,float)) or not math.isfinite(value[key]) or value[key]<0:
                        raise ValueError("invalid runner metric: "+key)
                if value["throughput"]<=0: raise ValueError("zero throughput cannot be a baseline")
                value["rotation_seconds"]=cfg.rotation_seconds
                metrics[mode]=value
                write_json(output/f"round-{index}-{mode}.json",value)
            finally:
                if child:
                    if child.poll() is None: child.send_signal(signal.SIGINT)
                    try: child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        child.kill();child.wait()
                    if logfile: logfile.close()
                    if child.returncode!=0: raise RuntimeError("collector failed during measurement; inspect log")
        pairs.append((metrics["baseline"],metrics["profiled"]))
        write_json(output/"raw-pairs.json",pairs)
    summary=evaluate(pairs)
    write_json(output/"summary.json",summary)
    print(json.dumps(summary,indent=2))
    return 0 if summary["verdict"]=="PASS_FOR_THIS_WORKLOAD" else 3

if __name__=="__main__": raise SystemExit(main())
