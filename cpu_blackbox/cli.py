import argparse
import json
import logging
import math
import shutil
import sys
import subprocess
import sqlite3
import time
from pathlib import Path
from .collector import collect, doctor, reindex
from .query import query
from .simulation import simulate
from .storage import BlackboxError, Config, NS, Store, parse_time


def main(argv=None):
    parser = argparse.ArgumentParser(description="Linux continuous CPU profiling and historical flamegraphs")
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--config")
    parser.add_argument("--perf", default="perf")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("doctor")
    check.add_argument("--pid", type=int, required=True)
    capture = commands.add_parser("collect")
    capture.add_argument("--pid", type=int, required=True)
    capture.add_argument("--duration", type=float)
    simulation = commands.add_parser("simulate", help="synthetic workflow demo; not a Linux/perf emulator")
    simulation.add_argument("--duration", type=float, default=10)
    simulation.add_argument("--rotation-seconds", type=int, default=2)
    commands.add_parser("status")
    commands.add_parser("reindex", help="retry retained files that exceeded decode limits")
    report = commands.add_parser("query")
    report.add_argument("--at")
    report.add_argument("--before", type=int, default=30)
    report.add_argument("--after", type=int, default=30)
    report.add_argument("--start")
    report.add_argument("--end")
    report.add_argument("--session")
    report.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = Config.load(args.config)
        if hasattr(args, "pid") and args.pid <= 0:
            raise BlackboxError("PID must be positive")
        if args.command == "doctor":
            result = doctor(args.pid, cfg, args.perf)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["ready"] else 2
        if args.command == "collect":
            if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
                raise BlackboxError("duration must be positive")
            return collect(args.data_dir, args.pid, cfg, args.perf, args.duration)
        if args.command == "simulate":
            cfg.rotation_seconds = args.rotation_seconds
            cfg.validate()
            return simulate(args.data_dir, cfg, args.duration)
        if args.command == "reindex":
            return reindex(args.data_dir, cfg, args.perf)
        if args.command == "status":
            path = Path(args.data_dir) / "status.json"
            if not path.exists():
                raise BlackboxError("no collector status")
            result = json.loads(path.read_text(encoding="utf-8"))
            result["age_seconds"] = (time.time_ns() - result["updated_wall_ns"]) / NS
            result["stale"] = result["state"] == "RUNNING" and result["age_seconds"] > cfg.perf_timeout_seconds + cfg.disk_check_seconds * 2
            result["free_bytes_now"] = shutil.disk_usage(path.parent).free
            print(json.dumps(result, indent=2))
            return 3 if result["stale"] else 0
        if args.at:
            if args.start or args.end or args.before < 0 or args.after < 0:
                raise BlackboxError("use either --at or --start/--end; margins must be nonnegative")
            center = parse_time(args.at)
            start, end = center - args.before * NS, center + args.after * NS
        else:
            if not args.start or not args.end:
                raise BlackboxError("--start and --end are required together")
            start, end = parse_time(args.start), parse_time(args.end)
        store = Store(args.data_dir)
        try:
            result = query(store, cfg, start, end, args.output, args.session, args.perf)
        finally:
            store.close()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 3 if result["status"] == "partial" else 0
    except (BlackboxError, OSError, ValueError, TimeoutError, subprocess.SubprocessError, sqlite3.Error) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
