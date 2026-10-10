import json
import logging
import os
import platform
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from .query import decode, samples
from .storage import BlackboxError, DecodeResourceError, Config, NS, Store, anchor, disk_bytes, file_lock, safe_path, write_json

LOG = logging.getLogger("cpu-blackbox")


def identity(pid):
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        return fields[19]  # proc stat field 22, after pid and comm.
    except (OSError, IndexError) as exc:
        raise BlackboxError("target process does not exist or cannot be read") from exc


def command(perf, pid, cfg, output, buildid_dir=None):
    cache = Path(buildid_dir) if buildid_dir else Path(output).parent / "buildids"
    return [perf, "--buildid-dir", str(cache), "record", "-p", str(pid), "-e", cfg.event, "-F", str(cfg.frequency_hz),
            "--call-graph", cfg.call_graph, "-k", "CLOCK_MONOTONIC", "--timestamp-boundary",
            "--switch-output=" + str(cfg.rotation_seconds) + "s",
            "--no-no-buildid", "--no-no-buildid-cache", "-o", str(output)]


def doctor(pid, cfg, perf="perf"):
    result = {"platform": platform.platform(), "python": platform.python_version(), "checks": {}}
    if platform.system() != "Linux":
        result["checks"]["linux"] = "FAIL: Linux required for perf collection"
        result["ready"] = False
        return result
    binary = shutil.which(perf)
    if not binary:
        result["checks"]["perf"] = "FAIL: perf executable missing"
        result["ready"] = False
        return result
    identity(pid)
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
    result["perf_version"] = version.stdout.strip()
    result["checks"]["perf"] = "PASS" if version.returncode == 0 else "FAIL"
    result["perf_event_paranoid"] = Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip()
    # A real short attach verifies permissions, event support and monotonic clock.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="cpu-doctor-") as temp:
        args = command(binary, pid, cfg, Path(temp) / "perf.data")
        args += ["--", "sleep", "0.2"]
        probe = subprocess.run(args, capture_output=True, text=True, timeout=15)
        result["checks"]["attach"] = "PASS" if probe.returncode == 0 else "FAIL"
        result["attach_diagnostics"] = probe.stderr[-8192:]
    result["ready"] = all(value == "PASS" for value in result["checks"].values())
    result["notes"] = "Attach success is not proof of good symbols or <3% business overhead. Validate both separately."
    return result


def open_perf_paths(child):
    paths = set()
    directory = Path(f"/proc/{child.pid}/fd")
    if not directory.exists():
        if child.poll() is None:
            raise BlackboxError("cannot inspect live perf file descriptors")
        return paths
    for fd in directory.iterdir():
        try:
            paths.add(str(Path(os.readlink(fd)).resolve()))
        except PermissionError as exc:
            raise BlackboxError("cannot inspect live perf file descriptors") from exc
        except FileNotFoundError:
            continue
    return paths


def index_sealed(store, sid, session_dir, cfg, perf, child=None, retry_pending=False):
    active = open_perf_paths(child) if child and child.poll() is None else set()
    for path in sorted(session_dir.glob("perf.data.*")):
        if path.is_symlink() or str(path.resolve()) in active:
            continue
        prior = store.db.execute("SELECT state FROM segments WHERE path=?", (str(path.resolve()),)).fetchone()
        if prior and not (retry_pending and prior["state"] == "UNINDEXED"):
            continue
        decoded = session_dir / (".decode-" + uuid.uuid4().hex)
        try:
            warning = decode(perf, path, decoded, cfg, store.root)
            first = last = None
            count = 0
            with decoded.open(encoding="utf-8", errors="replace") as stream:
                for sample in samples(stream):
                    count += 1
                    first = sample["time"] if first is None else min(first, sample["time"])
                    last = sample["time"] if last is None else max(last, sample["time"])
            store.segment(path.resolve(), sid, first or 0, last or 0, "AVAILABLE" if count else "EMPTY", warning)
        except DecodeResourceError as exc:
            store.segment(path.resolve(), sid, 0, 0, "UNINDEXED", str(exc))
            LOG.warning("recording retained but not indexed %s: %s", path, exc)
            if "disk budget" in str(exc):
                raise
        except BlackboxError as exc:
            store.segment(path.resolve(), sid, 0, 0, "CORRUPT", str(exc))
            LOG.error("cannot index %s: %s", path, exc)
        finally:
            decoded.unlink(missing_ok=True)


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def recover(store, cfg, perf):
    current_boot = boot_id()
    for row in store.db.execute("SELECT * FROM sessions WHERE state='RUNNING'").fetchall():
        folder = safe_path(store.root, store.root / row["id"])
        # Single collector lock is held. A surviving perf child remains unsafe to touch.
        state_file = folder / "process.json"
        if state_file.exists() and row["boot"] == current_boot:
            info = json.loads(state_file.read_text())
            try:
                if identity(info["perf_pid"]) == info["perf_start"]:
                    raise BlackboxError("orphan perf still alive; stop PID " + str(info["perf_pid"]) + " before recovery")
            except BlackboxError as exc:
                if "orphan" in str(exc):
                    raise
        base = folder / "perf.data"
        if base.exists():
            base.rename(folder / "perf.data.recovered")
        index_sealed(store, row["id"], folder, cfg, perf)
        store.db.execute("UPDATE sessions SET state='INTERRUPTED' WHERE id=?", (row["id"],))
        store.db.commit()


def stop_perf(child):
    if child.poll() is None:
        child.send_signal(signal.SIGINT)
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            return False
    return True


def collect(root, pid, cfg, perf="perf", duration=None):
    if platform.system() != "Linux":
        raise BlackboxError("collection requires Linux; portable unit tests do not validate perf")
    os.umask(0o077)
    with file_lock(Path(root) / "collector.lock", blocking=False):
        status = doctor(pid, cfg, perf)
        if not status["ready"]:
            raise BlackboxError(json.dumps(status, ensure_ascii=False))
        store = Store(root)
        child = None
        stderr = None
        sid = None
        session_dir = None
        stopping = False
        reason = "STOPPED"
        old_handlers = {}
        def stop(signum, frame):
            nonlocal stopping
            stopping = True
        try:
            recover(store, cfg, perf)
            target_start = identity(pid)
            sid = uuid.uuid4().hex
            session_dir = store.root / sid
            session_dir.mkdir(mode=0o700)
            point = anchor()
            boot = boot_id()
            store.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)",
                             (sid, boot, pid, target_start, point["mono"], None, json.dumps(asdict(cfg)), "RUNNING"))
            store.db.commit()
            store.add_anchor(sid, point, cfg.clock_jump_ns)
            args = command(perf, pid, cfg, session_dir / "perf.data", store.root / "buildids")
            write_json(session_dir / "metadata.json", {"doctor": status, "command": args, "config": asdict(cfg), "boot_id": boot, "process_start": target_start})
            stderr = (session_dir / "perf.log").open("ab")
            child = subprocess.Popen(args, stdout=stderr, stderr=stderr)
            write_json(session_dir / "process.json", {"perf_pid": child.pid, "perf_start": identity(child.pid)})
            for sig in (signal.SIGTERM, signal.SIGINT):
                old_handlers[sig] = signal.signal(sig, stop)
            started = time.monotonic()
            while not stopping:
                if child.poll() is not None:
                    reason = "PERF_EXITED"
                    break
                if duration and time.monotonic() - started >= duration:
                    break
                try:
                    if identity(pid) != target_start:
                        reason = "TARGET_CHANGED"
                        break
                except BlackboxError:
                    reason = "TARGET_EXITED"
                    break
                store.add_anchor(sid, anchor(), cfg.clock_jump_ns)
                store.cleanup(cfg)
                used, free = disk_bytes(store.root), shutil.disk_usage(store.root).free
                if used >= cfg.max_data_bytes or free < cfg.min_free_bytes:
                    reason = "DISK_BUDGET"
                    break
                index_sealed(store, sid, session_dir, cfg, perf, child)
                write_json(store.root / "status.json", {"state": "RUNNING", "updated_wall_ns": time.time_ns(), "session": sid,
                           "collector_pid": os.getpid(), "perf_pid": child.pid, "data_bytes": disk_bytes(store.root),
                           "free_bytes": shutil.disk_usage(store.root).free})
                until = time.monotonic() + cfg.disk_check_seconds
                while not stopping and child.poll() is None and time.monotonic() < until:
                    time.sleep(0.1)
        except DecodeResourceError as exc:
            reason = "DISK_BUDGET" if "disk budget" in str(exc) else "FAILED"
        except BaseException:
            reason = "FAILED"
            raise
        finally:
            try:
                if child:
                    finalized = stop_perf(child)
                    if not finalized:
                        reason = "FORCED_STOP"
                    try:
                        base = session_dir / "perf.data"
                        if base.exists():
                            base.rename(session_dir / "perf.data.final")
                        index_sealed(store, sid, session_dir, cfg, perf)
                    except DecodeResourceError:
                        reason = "DISK_BUDGET"
                    except (BlackboxError, OSError) as exc:
                        reason = "FAILED"
                        LOG.error("final recording publication failed: %s", exc)
                if sid is not None:
                    point = anchor()
                    store.add_anchor(sid, point, cfg.clock_jump_ns)
                    store.db.execute("UPDATE sessions SET state=?,end_mono=? WHERE id=?", (reason, point["mono"], sid))
                    store.db.commit()
                    write_json(store.root / "status.json", {"state": reason, "updated_wall_ns": time.time_ns(), "session": sid,
                               "collector_pid": os.getpid(), "data_bytes": disk_bytes(store.root), "free_bytes": shutil.disk_usage(store.root).free})
            finally:
                if stderr:
                    stderr.close()
                for sig, handler in old_handlers.items():
                    signal.signal(sig, handler)
                store.close()
        if reason == "DISK_BUDGET":
            return 75  # systemd RestartPreventExitStatus avoids a tight disk-full loop.
        if reason not in ("STOPPED", "TARGET_EXITED"):
            raise BlackboxError("collection ended: " + reason)
        return 0


def reindex(root, cfg, perf="perf"):
    """Retry retained UNINDEXED files after raising decode limits; no active collector."""
    if platform.system() != "Linux":
        raise BlackboxError("real recording reindex requires Linux/perf")
    with file_lock(Path(root) / "collector.lock", blocking=False):
        store = Store(root)
        try:
            recover(store, cfg, perf)
            for session in store.db.execute("SELECT * FROM sessions").fetchall():
                if json.loads(session["config"]).get("source") == "synthetic":
                    continue
                directory = safe_path(store.root, store.root / session["id"])
                index_sealed(store, session["id"], directory, cfg, perf, retry_pending=True)
        finally:
            store.close()
    return 0
