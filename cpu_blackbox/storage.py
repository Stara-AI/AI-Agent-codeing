import contextlib
import json
import math
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

NS = 1_000_000_000

class BlackboxError(Exception):
    pass

class DecodeResourceError(BlackboxError):
    """An intact recording could not be indexed within current resource limits."""
    pass

@dataclass
class Config:
    frequency_hz: int = 49
    rotation_seconds: int = 60
    retention_hours: float = 24
    max_data_bytes: int = 2 * 1024 ** 3
    min_free_bytes: int = 1024 ** 3
    disk_check_seconds: float = 5
    call_graph: str = "fp"
    event: str = "cpu-clock"
    perf_timeout_seconds: float = 120
    max_query_seconds: int = 3600
    max_decode_bytes: int = 256 * 1024 ** 2
    max_report_bytes: int = 64 * 1024 ** 2
    clock_jump_ns: int = 100_000_000

    def validate(self):
        for key in ("frequency_hz", "rotation_seconds", "max_data_bytes", "min_free_bytes",
                    "disk_check_seconds", "retention_hours", "perf_timeout_seconds",
                    "max_query_seconds", "max_decode_bytes", "max_report_bytes", "clock_jump_ns"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise BlackboxError("configuration must be positive: " + key)
        for key in ("frequency_hz", "rotation_seconds", "max_data_bytes", "min_free_bytes", "max_query_seconds", "max_decode_bytes", "max_report_bytes", "clock_jump_ns"):
            if not isinstance(getattr(self, key), int):
                raise BlackboxError(key + " must be an integer")
        if self.call_graph not in ("fp", "dwarf,4096", "dwarf,8192"):
            raise BlackboxError("unsupported call_graph")
        if self.event not in ("cpu-clock", "cycles"):
            raise BlackboxError("event must be cpu-clock or cycles")
        return self

    @classmethod
    def load(cls, path=None):
        try:
            values = json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else {}
            return cls(**values).validate()
        except (TypeError, ValueError) as exc:
            raise BlackboxError("invalid configuration: " + str(exc)) from exc


def parse_time(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone required")
        # Integer arithmetic avoids float boundary errors.
        epoch = datetime(1970, 1, 1, tzinfo=dt.tzinfo)
        delta = dt - epoch
        local_ns = (delta.days * 86400 + delta.seconds) * NS + delta.microseconds * 1000
        return local_ns - int(dt.utcoffset().total_seconds()) * NS
    except ValueError as exc:
        raise BlackboxError("invalid ISO 8601 time with timezone: " + value) from exc


def anchor():
    before = time.monotonic_ns()
    wall = time.time_ns()
    after = time.monotonic_ns()
    return {"mono": (before + after) // 2, "wall": wall, "error_ns": (after - before) // 2}


def write_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def safe_path(root, value):
    root = Path(root).resolve()
    path = Path(value)
    if path.is_symlink():
        raise BlackboxError("symlink not permitted: " + str(path))
    resolved = path.resolve()
    if root not in resolved.parents:
        raise BlackboxError("path outside managed directory: " + str(path))
    return resolved


def disk_bytes(root):
    return sum(p.stat().st_size for p in Path(root).rglob("*")
               if p.is_file() and not p.is_symlink())


@contextlib.contextmanager
def file_lock(path, blocking=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BlackboxError("lock already held: " + str(path)) from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except OSError as exc:
                raise BlackboxError("lock already held: " + str(path)) from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.root / "index.sqlite"), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS sessions(
          id TEXT PRIMARY KEY, boot TEXT, pid INTEGER, process_start TEXT,
          start_mono INTEGER, end_mono INTEGER, config TEXT, state TEXT);
        CREATE TABLE IF NOT EXISTS anchors(
          session TEXT, mono INTEGER, wall INTEGER, error_ns INTEGER, epoch INTEGER);
        CREATE TABLE IF NOT EXISTS segments(
          path TEXT PRIMARY KEY, session TEXT, first_ns INTEGER, last_ns INTEGER,
          size INTEGER, state TEXT, error TEXT);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(segments)")}
        if "sealed_wall_ns" not in columns:
            self.db.execute("ALTER TABLE segments ADD COLUMN sealed_wall_ns INTEGER NOT NULL DEFAULT 0")
        self.db.executescript("""
        CREATE INDEX IF NOT EXISTS segments_query ON segments(session,state,first_ns,last_ns);
        CREATE INDEX IF NOT EXISTS anchors_session ON anchors(session,mono);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def add_anchor(self, sid, point, threshold):
        last = self.db.execute("SELECT * FROM anchors WHERE session=? ORDER BY mono DESC LIMIT 1", (sid,)).fetchone()
        epoch = last["epoch"] if last else 0
        if last and abs((point["wall"] - point["mono"]) - (last["wall"] - last["mono"])) > threshold:
            epoch += 1
        self.db.execute("INSERT INTO anchors VALUES(?,?,?,?,?)", (sid, point["mono"], point["wall"], point["error_ns"], epoch))
        self.db.commit()

    def map_window(self, sid, start, end):
        points = self.db.execute("SELECT * FROM anchors WHERE session=? ORDER BY mono", (sid,)).fetchall()
        if not points:
            raise BlackboxError("no clock mapping for session " + sid)
        if len({p["epoch"] for p in points}) != 1:
            raise BlackboxError("clock jump detected; this session requires manual monotonic-time analysis")
        # Closest anchor; report observed drift rather than pretending exact wall-time mapping.
        point = min(points, key=lambda p: abs(p["wall"] - (start + end) // 2))
        offset = point["wall"] - point["mono"]
        uncertainty = max(p["error_ns"] + abs(p["wall"] - p["mono"] - offset) for p in points)
        return start - offset, end - offset, uncertainty

    def segment(self, path, sid, first, last, state="AVAILABLE", error=""):
        stat = Path(path).stat()
        self.db.execute("INSERT OR REPLACE INTO segments(path,session,first_ns,last_ns,size,state,error,sealed_wall_ns) VALUES(?,?,?,?,?,?,?,?)",
                        (str(path), sid, first, last, stat.st_size, state, error, stat.st_mtime_ns))
        self.db.commit()

    def select(self, sid, start, end):
        return self.db.execute("SELECT * FROM segments WHERE session=? AND state='AVAILABLE' "
                               "AND first_ns < ? AND last_ns >= ? ORDER BY first_ns", (sid, end, start)).fetchall()

    def cleanup(self, cfg, now=None):
        now = time.time_ns() if now is None else now
        removed = []
        with file_lock(self.root / "storage.lock"):
            candidates = []
            rows = self.db.execute("SELECT * FROM segments WHERE state IN ('AVAILABLE','CORRUPT','EMPTY','UNINDEXED')").fetchall()
            for row in rows:
                if row["state"] == "AVAILABLE":
                    point = self.db.execute("SELECT wall,mono FROM anchors WHERE session=? ORDER BY ABS(mono-?) LIMIT 1", (row["session"], row["last_ns"])).fetchone()
                    if not point:
                        continue
                    last_wall = row["last_ns"] + point["wall"] - point["mono"]
                else:
                    # Unknown sample timestamps are not epoch zero: use file completion time.
                    path = safe_path(self.root, row["path"])
                    last_wall = row["sealed_wall_ns"] or (path.stat().st_mtime_ns if path.exists() else now)
                candidates.append((last_wall, row))
            # Monotonic values cannot be compared across boot IDs.
            used, free = disk_bytes(self.root), shutil.disk_usage(self.root).free
            for last_wall, row in sorted(candidates, key=lambda item: item[0]):
                expired = last_wall < now - int(cfg.retention_hours * 3600 * NS)
                if not expired and used < cfg.max_data_bytes and free >= cfg.min_free_bytes:
                    continue
                path = safe_path(self.root, row["path"])
                size = path.stat().st_size if path.exists() else 0
                path.unlink(missing_ok=True)
                used = max(0, used-size)
                free = shutil.disk_usage(self.root).free
                self.db.execute("UPDATE segments SET state='EXPIRED' WHERE path=?", (row["path"],))
                self.db.commit()
                removed.append(str(path))
            self.db.execute("DELETE FROM segments WHERE state='EXPIRED'")
            cutoff = now - int(cfg.retention_hours * 3600 * NS)
            self.db.execute("DELETE FROM anchors WHERE wall < ? AND rowid NOT IN (SELECT MIN(rowid) FROM anchors GROUP BY session)", (cutoff,))
            self.db.commit()
            self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return removed
