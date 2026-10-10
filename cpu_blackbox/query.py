import collections
import html
import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from .report import build_page
from .storage import BlackboxError, DecodeResourceError, NS, disk_bytes, file_lock, safe_path, write_json

HEADER = re.compile(r"^(.+?)\s+(\d+)(?:/\d+)?\s+(?:\[\d+\]\s+)?(\d+\.\d+):\s+(\S+):\s*(.*)$")
FRAME = re.compile(r"^\s*([0-9a-fA-F]+)\s+(.+?)(?:\s+\((.*)\))?\s*$")

def seconds_ns(value):
    whole, fraction = value.split(".")
    return int(whole) * NS + int(fraction.ljust(9, "0")[:9])


def samples(lines):
    current = None
    for line in lines:
        match = HEADER.match(line.rstrip())
        if match:
            if current is not None:
                yield current
            comm, pid, stamp, event, rest = match.groups()
            current = {"comm": comm.strip(), "pid": int(pid), "time": seconds_ns(stamp), "event": event, "frames": [], "header_pending": False, "header_ip": None}
            # Default perf output puts the sampled instruction on the header.
            if rest.strip():
                frame = FRAME.match(rest)
                if frame:
                    current["frames"].append(frame.group(2))
                    current["header_pending"] = True
                    current["header_ip"] = frame.group(1)
        elif current is not None and line[:1].isspace():
            frame = FRAME.match(line)
            if frame:
                symbol = frame.group(2)
                # Some perf versions repeat the leaf on the header and in the callchain.
                if not (current["header_pending"] and current["frames"][-1] == symbol and current["header_ip"] == frame.group(1)):
                    current["frames"].append(symbol)
                current["header_pending"] = False
        elif not line.strip() and current is not None:
            yield current
            current = None
    if current is not None:
        yield current


def fold(lines, start, end, pid=None):
    counts = collections.Counter()
    events = set()
    total = unknown = 0
    first = last = None
    for sample in samples(lines):
        if not start <= sample["time"] < end or (pid is not None and sample["pid"] != pid):
            continue
        events.add(sample["event"])
        total += 1
        first = sample["time"] if first is None else min(first, sample["time"])
        last = sample["time"] if last is None else max(last, sample["time"])
        frames = sample["frames"] or ["[unknown]"]
        unknown += any("unknown" in frame or frame.startswith("0x") for frame in frames)
        stack = (sample["comm"] + " [" + str(sample["pid"]) + "]",) + tuple(reversed(frames))
        counts[stack] += 1
    if len(events) > 1:
        raise BlackboxError("multiple events cannot be mixed in one flamegraph")
    return counts, {"sample_count": total, "unknown_samples": unknown, "first_ns": first, "last_ns": last, "events": sorted(events)}


def decode(perf, data, output, cfg, budget_root=None):
    """Bounded disk-backed perf script; stderr is retained on errors."""
    output = Path(output)
    error_path = output.with_suffix(".stderr")
    command = [perf]
    if budget_root:
        command += ["--buildid-dir", str(Path(budget_root) / "buildids")]
    command += ["script", "--ns", "-F", "comm,pid,time,event,ip,sym,dso", "-i", str(data)]
    complete = False
    try:
        with output.open("wb") as out, error_path.open("wb") as err:
            child = subprocess.Popen(command, stdout=out, stderr=err)
            started = time.monotonic()
            failure = None
            try:
                while child.poll() is None:
                    if time.monotonic() - started > cfg.perf_timeout_seconds:
                        failure = "perf script timeout"
                    elif output.stat().st_size + error_path.stat().st_size > cfg.max_decode_bytes:
                        failure = "decoded data exceeds budget"
                    elif budget_root and (shutil.disk_usage(budget_root).free < cfg.min_free_bytes or disk_bytes(budget_root) >= cfg.max_data_bytes):
                        failure = "disk budget reached during decode"
                    if failure:
                        break
                    time.sleep(0.05)
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
        combined_bytes = output.stat().st_size + error_path.stat().st_size
        with error_path.open(encoding="utf-8", errors="replace") as error_stream:
            error = error_stream.read(8192)
        if failure or combined_bytes > cfg.max_decode_bytes:
            raise DecodeResourceError(failure or "decoded data exceeds budget")
        if budget_root and (shutil.disk_usage(budget_root).free < cfg.min_free_bytes or disk_bytes(budget_root) >= cfg.max_data_bytes):
            raise DecodeResourceError("disk budget reached during decode")
        if child.returncode != 0:
            raise BlackboxError("perf script failed: " + error)
        complete = True
        return error
    finally:
        error_path.unlink(missing_ok=True)
        if not complete:
            output.unlink(missing_ok=True)



def layout(counts):
    root = {"name": "all samples", "value": 0, "children": {}}
    for stack, value in counts.items():
        node = root
        node["value"] += value
        for frame in stack:
            node = node["children"].setdefault(frame, {"name": frame, "value": 0, "children": {}})
            node["value"] += value
    nodes = []
    total = root["value"]
    def visit(node, left, depth, path):
        nodes.append({"name": node["name"], "value": node["value"], "x": left,
                      "width": node["value"] / total if total else 0, "depth": depth, "path": path})
        cursor = left
        for name, child in sorted(node["children"].items()):
            visit(child, cursor, depth + 1, path + [name])
            cursor += child["value"] / total if total else 0
    visit(root, 0, 0, [])
    return nodes


def render(counts, report, directory, max_bytes):
    directory = Path(directory)
    if directory.exists():
        raise BlackboxError("output must be a new directory")
    directory.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".cpu-report-", dir=str(directory.parent)))
    try:
        nodes = layout(counts)
        height = (max(n["depth"] for n in nodes) + 1) * 22
        boxes = []
        for index, node in enumerate(nodes):
            x, width, y = node["x"] * 1200, node["width"] * 1200, height - (node["depth"] + 1) * 22
            label = html.escape(node["name"])
            short_label = html.escape(node["name"][:max(0, int(width / 7) - 2)])
            pct = 100 * node["value"] / nodes[0]["value"] if nodes[0]["value"] else 0
            boxes.append(f'<g data-id="{index}" tabindex="0"><title>{label}: {node["value"]} samples ({pct:.2f}%)</title>'
                         f'<rect x="{x:.4f}" y="{y}" width="{width:.4f}" height="21" fill="hsl({(index * 17) % 55},85%,65%)" stroke="white"/>'
                         f'<text x="{x+3:.4f}" y="{y+15}" font-size="12" pointer-events="none">{short_label if width > 30 else ""}</text></g>')
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-label="CPU flamegraph">' + "".join(boxes) + '</svg>'
        page = build_page(svg, nodes, report, len(counts))
        (temp / "flamegraph.svg").write_text(svg, encoding="utf-8")
        (temp / "index.html").write_text(page, encoding="utf-8")
        (temp / "stacks.folded").write_text("\n".join(";".join(s.replace(";", ":").replace("\n", " ") for s in stack) + " " + str(value) for stack, value in counts.items()), encoding="utf-8")
        write_json(temp / "report.json", report)
        if disk_bytes(temp) > max_bytes:
            raise BlackboxError("report exceeds configured budget")
        temp.rename(directory)
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def gaps(intervals, start, end):
    result = []
    cursor = start
    for a, b in sorted(intervals):
        a, b = max(a, start), min(b, end)
        if b <= a:
            continue
        if a > cursor:
            result.append([cursor, a])
        cursor = max(cursor, b)
    if cursor < end:
        result.append([cursor, end])
    return result


def query(store, cfg, start, end, output, sid=None, perf="perf"):
    if end <= start or end - start > cfg.max_query_seconds * NS:
        raise BlackboxError("invalid or oversized query window")
    if store.root == Path(output).resolve() or store.root in Path(output).resolve().parents:
        raise BlackboxError("reports must be outside the collection directory")
    with file_lock(store.root / "query.lock", blocking=False):
        sessions = store.db.execute("SELECT * FROM sessions" + (" WHERE id=?" if sid else ""), (sid,) if sid else ()).fetchall()
        chosen = []
        mapping = []
        intervals = []
        with file_lock(store.root / "storage.lock"):
            for session in sessions:
                bounds = store.db.execute("SELECT MIN(first_ns),MAX(last_ns) FROM segments WHERE session=? AND state='AVAILABLE'", (session["id"],)).fetchone()
                points = store.db.execute("SELECT wall-mono FROM anchors WHERE session=?", (session["id"],)).fetchall()
                if bounds[0] is None or not points:
                    continue
                offsets = [p[0] for p in points]
                if bounds[1] + max(offsets) < start or bounds[0] + min(offsets) >= end:
                    continue
                a, b, error = store.map_window(session["id"], start, end)
                rows = store.select(session["id"], a, b)
                if rows:
                    mapping.append({"session": session["id"], "uncertainty_ns": error})
                for row in rows:
                    chosen.append((session, row, a, b))
                    offset = start - a
                    intervals.append((row["first_ns"] + offset, row["last_ns"] + 1 + offset))
            if not chosen:
                raise BlackboxError("no sealed retained samples for this window")
            signatures = {tuple(json.loads(s["config"])[k] for k in ("event", "frequency_hz", "call_graph")) + (json.loads(s["config"]).get("source", "perf"),) for s, _, _, _ in chosen}
            if len(signatures) != 1:
                raise BlackboxError("incompatible sessions; select a single --session")
            needed = sum(row["size"] for _, row, _, _ in chosen)
            if needed + disk_bytes(store.root) >= cfg.max_data_bytes or shutil.disk_usage(store.root).free - needed < cfg.min_free_bytes:
                raise BlackboxError("insufficient query snapshot budget")
            with tempfile.TemporaryDirectory(prefix="query-", dir=str(store.root)) as temp:
                snapshots = []
                for index, (session, row, a, b) in enumerate(chosen):
                    source = safe_path(store.root, row["path"])
                    target = Path(temp) / f"{index}.data"
                    shutil.copyfile(source, target)
                    snapshots.append((session, row, a, b, target))
                # Copies are now stable; cleanup no longer needs the source files.
                # Keep this lock until copies are made, not throughout decoding.
                snapshot_dir = Path(temp)
                retained_dir = store.root / ("query-pinned-" + uuid.uuid4().hex)
                snapshot_dir.rename(retained_dir)
        try:
            all_counts = collections.Counter()
            total = unknown = 0
            warnings = []
            events = set()
            for session, row, a, b, target in snapshots:
                target = retained_dir / target.name
                decoded = retained_dir / "decoded.txt"
                is_simulated = json.loads(session["config"]).get("source") == "synthetic"
                if is_simulated:
                    if target.stat().st_size > cfg.max_decode_bytes:
                        raise BlackboxError("synthetic data exceeds decode budget")
                    decoded = target
                    warning = "Synthetic simulation data; not Linux perf evidence"
                else:
                    warning = decode(perf, target, decoded, cfg, store.root)
                if warning:
                    warnings.append(warning)
                with decoded.open(encoding="utf-8", errors="replace") as stream:
                    counts, stats = fold(stream, a, b, session["pid"])
                decoded.unlink()
                all_counts.update(counts)
                events.update(stats["events"])
                total += stats["sample_count"]
                unknown += stats["unknown_samples"]
            if not total:
                raise BlackboxError("window contains no matching CPU samples")
            if len(events) != 1:
                raise BlackboxError("multiple events in query")
            report = {"start_wall_ns": start, "end_wall_ns": end, "sample_count": total,
                      "unknown_samples": unknown, "events": sorted(events), "clock_mappings": mapping,
                      "sample_coverage_gaps_wall_ns": gaps(intervals, start, end),
                      "coverage_note": "Sample bounds only: gaps can indicate idle periods, loss or missing collection; not proof of uninterrupted coverage.",
                      "segments": [dict(row) for _, row, _, _, _ in snapshots],
                      "warnings": warnings, "config": json.loads(snapshots[0][0]["config"])}
            report["source"] = "SYNTHETIC SIMULATION / 合成模拟，不是真实 Linux perf 采样" if is_simulated else "Linux perf"
            report["status"] = "partial" if report["sample_coverage_gaps_wall_ns"] else "available"
            render(all_counts, report, output, cfg.max_report_bytes)
            return report
        finally:
            shutil.rmtree(retained_dir)
