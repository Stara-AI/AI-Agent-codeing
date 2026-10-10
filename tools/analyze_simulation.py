"""Apply the incident/baseline workflow to retained SYNTHETIC demo data only."""
import argparse
import html
import json
import sys
from collections import Counter
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from cpu_blackbox.query import query
from cpu_blackbox.report import time_text
from cpu_blackbox.storage import BlackboxError, Config, Store, parse_time, write_json

SKILL_URL = "https://github.com/hyf020908/auto-profiling-skill/blob/227f31fa37926a097c16b9ca9ee7e017f8746441/SKILL.md"


def load_counts(directory):
    result = Counter()
    for line in (Path(directory) / "stacks.folded").read_text(encoding="utf-8").splitlines():
        stack, count = line.rsplit(" ", 1)
        result[tuple(stack.split(";"))] += int(count)
    return result


def symbol_counts(counts):
    """Inclusive counts, deduplicated per sample for recursive symbols."""
    symbols = Counter()
    for stack, count in counts.items():
        for symbol in set(stack[1:]):
            symbols[symbol] += count
    return symbols


def compare(target, baseline):
    target_total, base_total = sum(target.values()), sum(baseline.values())
    if not target_total or not base_total:
        return []  # An absent baseline is not a measured 0%.
    target_symbols, base_symbols = symbol_counts(target), symbol_counts(baseline)
    result = []
    for name in target_symbols.keys() | base_symbols.keys():
        a, b = target_symbols[name] * 100 / target_total, base_symbols[name] * 100 / base_total
        result.append({"symbol": name, "target_pct": a, "baseline_pct": b, "delta_pp": a-b})
    return sorted(result, key=lambda row: (-row["delta_pp"], -row["target_pct"], row["symbol"]))


def cell(value):
    return "<code>" + html.escape(str(value)).replace("|", "&#124;").replace("\n", " ") + "</code>"


def analyze(data, output):
    data, output = Path(data).resolve(), Path(output).resolve()
    manifest = json.loads((data / "simulation.json").read_text(encoding="utf-8"))
    if manifest.get("source") != "synthetic":
        raise BlackboxError("this tool accepts synthetic simulation data only")
    if output == data or data in output.parents:
        raise BlackboxError("reports must be outside collection data")
    if output.exists():
        raise BlackboxError("output root must be a new directory")
    sid = manifest["session"]
    start, end = parse_time(manifest["hot_window_start"]), parse_time(manifest["hot_window_end"])
    baseline_start = start - (end-start)
    store = Store(data)
    try:
        session = store.db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not session or json.loads(session["config"]).get("source") != "synthetic":
            raise BlackboxError("missing synthetic session")
        settings = json.loads(session["config"])
        allowed = {f.name for f in fields(Config)}
        cfg = Config(**{key: value for key, value in settings.items() if key in allowed})
        cfg.validate()
        bounds = store.db.execute("SELECT MIN(first_ns),MAX(last_ns) FROM segments WHERE session=? AND state='AVAILABLE'", (sid,)).fetchone()
        anchors = store.db.execute("SELECT * FROM anchors WHERE session=?", (sid,)).fetchall()
        if bounds[0] is None or not anchors:
            raise BlackboxError("no retained mapped samples")
        anchor = min(anchors, key=lambda point: abs(point["mono"]-(bounds[0]+bounds[1])//2))
        offset = anchor["wall"]-anchor["mono"]
        full_start, full_end = bounds[0]+offset, bounds[1]+offset+1
        output.mkdir(parents=True)
        target = query(store, cfg, start, end, output / "incident", sid)
        baseline = None
        baseline_error = None
        try:
            baseline = query(store, cfg, baseline_start, start, output / "baseline", sid)
        except BlackboxError as exc:
            if str(exc) not in ("no sealed retained samples for this window", "window contains no matching CPU samples"):
                raise
            baseline_error = str(exc)
        full = query(store, cfg, full_start, full_end, output / "full", sid)
    finally:
        store.close()
    target_counts = load_counts(output / "incident")
    baseline_counts = load_counts(output / "baseline") if baseline else Counter()
    deltas = compare(target_counts, baseline_counts)
    result = {
        "source": "SYNTHETIC SIMULATION; not Linux perf evidence",
        "skill_reference": SKILL_URL, "skill_engine_executed": False,
        "implementation": "Existing CPU Blackbox simulation/query; incident and baseline workflow adapted from the requested Skill",
        "session": sid, "target_samples": target["sample_count"],
        "baseline_samples": baseline["sample_count"] if baseline else None,
        "full_samples": full["sample_count"], "baseline_error": baseline_error,
        "target_window_ns": [start, end], "baseline_window_ns": [baseline_start, start],
        "deltas": deltas, "target_gaps": target["sample_coverage_gaps_wall_ns"],
        "baseline_gaps": baseline["sample_coverage_gaps_wall_ns"] if baseline else None,
        "report": str(output / "index.html"),
    }
    write_json(output / "comparison.json", result)
    lines = ["# 合成 CPU 样本：故障窗口与基线分析\n\n",
             f"采用 [auto-profiling-skill]({SKILL_URL}) 的事故回查、等长基线和检查报告流程；执行引擎为本项目的合成模拟/查询代码。没有执行该 Skill 的 Go/perf 采集引擎。\n\n",
             "**数据为 SYNTHETIC；不能用于认定真实业务根因、CPU 利用率或 <3% 开销。**\n\n",
             f"- 故障窗口：{time_text(start)} → {time_text(end)}，{target['sample_count']} 个样本。\n",
             f"- 基线窗口：{time_text(baseline_start)} → {time_text(start)}，紧邻且等长；" + (f"{baseline['sample_count']} 个样本。\n" if baseline else "无可用样本，不能计算可靠增量。\n"),
             f"- 全保留时段：{full['sample_count']} 个样本，{len(load_counts(output/'full'))} 条不同调用路径。它用于解释火焰图分支，不作为等长基线。\n\n",
             "## 调用路径与宽度\n\n| 故障调用路径 | 样本 | 全窗口占比 |\n| --- | ---: | ---: |\n"]
    for stack, count in target_counts.most_common(10):
        lines.append(f"| {cell(' → '.join(stack))} | {count} | {100*count/target['sample_count']:.2f}% |\n")
    if len(target_counts) == 1:
        lines.append("\n所有故障窗口样本经过同一条路径，各层都包含全部样本，因此横条同宽。这是数据的统计结果。\n")
    lines.append("\n## 相对基线的符号占比\n\n")
    if deltas:
        lines.append("| 符号（inclusive） | 故障占比 | 基线占比 | 增量 |\n| --- | ---: | ---: | ---: |\n")
        for row in deltas[:10]:
            lines.append(f"| {cell(row['symbol'])} | {row['target_pct']:.2f}% | {row['baseline_pct']:.2f}% | {row['delta_pp']:+.2f} pp |\n")
        positive = [row for row in deltas if row["delta_pp"] > 0]
        if positive:
            best = positive[0]
            lines.append(f"\n合成样本中，{cell(best['symbol'])} 的占比增长最大，为 {best['delta_pp']:.2f} 个百分点。基线可能包含热点开始阶段，请查看基线图；不能把它假设为完全正常。\n")
    else:
        lines.append("基线样本不足，未把缺失基线当作 0% 来宣称新增热点。\n")
    lines.append("\n各符号的 inclusive 占比包含子调用，同一递归符号每个样本只计一次；各行不能相加。占比增长不等于绝对 CPU 用量增长。\n\n## 覆盖与限制\n\n")
    for label, report in (("故障", target), ("基线", baseline), ("完整时段", full)):
        if report:
            missing = report["sample_coverage_gaps_wall_ns"]
            uncertainty = max(m["uncertainty_ns"] for m in report["clock_mappings"])
            lines.append(f"- {label}：{len(report['segments'])} 个分片；{len(missing)} 个样本边界缺口（合计 {sum(b-a for a,b in missing)/1e6:.3f} ms）；时钟映射不确定度 {uncertainty/1e6:.3f} ms；未知符号样本 {report['unknown_samples']}。\n")
    lines.append("\n样本边界不能证明连续采集；缺口可能是空闲、丢样或缺失。模拟的已知阶段切换用于验证历史回查，真实 CPU 飙升还需要监控、日志、发布事件与代码证据。Linux/perf、systemd、业务损耗和长期稳定性均待真实环境验证。\n")
    (output / "analysis.md").write_text("".join(lines), encoding="utf-8")
    base_link = '<a href="baseline/index.html">查看等长基线</a>' if baseline else '<span>基线无可用样本</span>'
    body = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CPU 模拟回查对比</title>
<style>body{{font-family:system-ui;max-width:1060px;margin:40px auto;padding:0 24px;background:#f3f6fa;color:#172033;line-height:1.8}}section{{background:white;padding:24px;border-radius:10px;margin:20px 0;border:1px solid #dce3ed}}a{{color:#1d4ed8}}.source{{background:#fff3cd;padding:12px}}nav{{display:flex;gap:24px;flex-wrap:wrap}}</style>
<h1>CPU 历史回查：三个窗口对比</h1><p class="source">SYNTHETIC 合成模拟；本页没有真实 Linux CPU 测量。</p>
<section><h2>1. 故障窗口：{target['sample_count']} 个样本</h2><p>窗口里只有热点路径时，各层都是 100%，横条同宽是正常现象。</p><a href="incident/index.html">查看故障窗口火焰图</a></section>
<section><h2>2. 紧邻的等长基线：{baseline['sample_count'] if baseline else '缺失'} 个样本</h2><p>比较函数样本占比的变化。基线可能含热点起始阶段，应按实际数据解释。</p>{base_link}</section>
<section><h2>3. 全保留时段：{full['sample_count']} 个样本</h2><p>包含正常与热点阶段，可以看到 normal_work 和 hot_leaf 分支。横轴仍表示样本占比。</p><a href="full/index.html">查看完整时段火焰图</a></section>
<nav><a href="analysis.md">阅读分析报告</a><a href="comparison.json">查看对比数值</a></nav></html>'''
    (output / "index.html").write_text(body, encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-root")
    args = parser.parse_args()
    output = args.output_root or str(Path.cwd() / "autoprof-reports" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    try:
        print(json.dumps(analyze(args.data_dir, output), ensure_ascii=True, indent=2))
    except (BlackboxError, OSError, ValueError, KeyError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
