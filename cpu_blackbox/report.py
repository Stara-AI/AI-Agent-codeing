"""Self-contained report UI; coordinates change on zoom, SVG aspect ratio does not."""
import html
import json
from datetime import datetime, timezone


def time_text(value):
    if value is None:
        return "未提供"
    seconds, nanos = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + f".{nanos:09d} UTC"


def build_page(svg, nodes, report, stack_count):
    total = nodes[0]["value"]
    source = report.get("source", "Linux perf")
    gaps = report.get("sample_coverage_gaps_wall_ns", [])
    status = "样本边界有缺口" if report.get("status") == "partial" else "样本边界完整" if report.get("status") == "available" else "覆盖信息未提供"
    start, end = report.get("start_wall_ns"), report.get("end_wall_ns")
    duration = f"{(end-start)/1e9:.3f} 秒" if start is not None and end is not None else "未提供"
    single = (f"当前窗口的 {total} 个样本只有一条调用路径，各层均占 100%，所以横条同宽；点击仍会显示所选函数的样本详情。"
              if stack_count == 1 else "不同调用路径形成分支。点击一个分支可展开其内部，恢复全图可再次比较各分支。")
    summary = html.escape(json.dumps(report, ensure_ascii=False, indent=2))
    page = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CPU 历史采样报告</title>
<style>
*{box-sizing:border-box}body{font-family:system-ui,sans-serif;margin:0;background:#f3f6fa;color:#172033;line-height:1.6}main{max-width:1440px;margin:auto;padding:28px}h1{font-size:28px;margin:0 0 12px}h2{font-size:20px;margin:0 0 12px}.source{padding:12px 16px;background:#fff3cd;border-left:4px solid #ca8a04;border-radius:6px;overflow-wrap:anywhere}.stats{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0}.stat{background:white;border:1px solid #dce3ed;border-radius:8px;padding:12px 18px;min-width:155px}.stat small{display:block;color:#586579}.stat strong{font-size:22px}.card{background:white;border:1px solid #dce3ed;border-radius:10px;padding:20px;margin:18px 0}.times{overflow-wrap:anywhere;font-size:14px}.note{background:#eff6ff;padding:10px 14px;border-radius:6px}.controls{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:14px 0}input,button{font:inherit;padding:8px 12px;border:1px solid #cbd5e1;border-radius:6px}button{background:#f1f5f9;cursor:pointer}input{min-width:220px}.graph-scroll{overflow-x:auto}.graph-scroll svg{display:block;width:100%;min-width:760px;height:auto;background:#fff}g[data-id]{cursor:pointer}g.hit rect{fill:#c026d3}g:focus rect{stroke:#172033;stroke-width:2}text{font-family:monospace}.detail{min-height:28px;overflow-wrap:anywhere;font-size:14px}a{color:#1d4ed8}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;background:#f8fafc;padding:12px}summary{cursor:pointer}#matches{color:#586579;font-size:14px}
</style></head><body><main><h1>CPU 历史采样报告</h1><p id="source-banner" class="source">@@SOURCE@@</p>
<div class="stats"><div class="stat"><small>窗口样本</small><strong>@@TOTAL@@</strong></div><div class="stat"><small>不同调用路径</small><strong>@@STACKS@@</strong></div><div class="stat"><small>窗口长度</small><strong>@@DURATION@@</strong></div><div class="stat"><small>覆盖提示</small><strong style="font-size:17px">@@STATUS@@</strong></div></div>
<p class="times">查询起点：@@START@@<br>查询终点：@@END@@（不包含终点）</p>
<section class="card"><h2>调用栈火焰图</h2><p>从下往上读调用层级；宽度表示选中样本的占比，横轴不是时间。祖先帧包含子调用，不能把各层占比相加。</p><p class="note" id="path-note">@@SINGLE@@</p>
<div class="controls"><button id="reset" type="button">恢复全图</button><input id="search" aria-label="搜索函数" placeholder="搜索函数"><span id="matches"></span></div>
<p id="focus-label" class="detail"></p><div class="graph-scroll">@@SVG@@</div><p id="frame-detail" class="detail" role="status" aria-live="polite"></p>
<p><a href="flamegraph.svg">打开静态 SVG</a> · <a href="stacks.folded">调用栈原始计数</a> · <a href="report.json">查询元数据</a></p></section>
<section class="card"><h2>覆盖与结论边界</h2><p>@@GAPS@@</p><p>CPU 样本提供 on-CPU 路径线索。真实业务根因仍需结合监控、日志与代码验证；合成数据只演示流程。</p><details><summary>查询详情（原始 JSON）</summary><pre>@@SUMMARY@@</pre></details></section>
<script>const nodes=@@NODES@@;
const svg=document.querySelector('svg');
const groups=Array.from(document.querySelectorAll('g[data-id]'));
let selected=nodes[0];
const prefix=(a,b)=>a.length<=b.length&&a.every((part,i)=>part===b[i]);
function details(n){document.getElementById('frame-detail').textContent=`${n.name}：${n.value} 个样本，占全窗口 ${(100*n.value/nodes[0].value).toFixed(2)}%（含子调用）`;}
function search(){const term=document.getElementById('search').value.toLowerCase();let total=0,visible=0;groups.forEach(g=>{const hit=term!==''&&nodes[Number(g.dataset.id)].name.toLowerCase().includes(term);g.classList.toggle('hit',hit);if(hit){total++;if(g.style.display!=='none')visible++;}});document.getElementById('matches').textContent=term?`${visible} 个当前匹配（全图 ${total} 个）`:'';}
function zoom(n){if(n.width<=0)return;selected=n;groups.forEach(g=>{const frame=nodes[Number(g.dataset.id)];const ancestor=prefix(frame.path,n.path);const descendant=prefix(n.path,frame.path);g.style.display=ancestor||descendant?'':'none';if(!ancestor&&!descendant)return;const x=ancestor?0:Math.max(0,(frame.x-n.x)*1200/n.width);const width=ancestor?1200:Math.min(1200-x,frame.width*1200/n.width);const rect=g.querySelector('rect');rect.setAttribute('x',x);rect.setAttribute('width',Math.max(0,width));const text=g.querySelector('text');text.setAttribute('x',x+3);const limit=Math.max(0,Math.floor(width/7)-2);text.textContent=width>30?Array.from(frame.name).slice(0,limit).join(''):'';});document.getElementById('focus-label').textContent=`当前视图：${n.name} · ${n.value} 个样本`;details(n);search();}
groups.forEach(g=>{const n=nodes[Number(g.dataset.id)];g.onclick=()=>zoom(n);g.onmouseenter=()=>details(n);g.onmouseleave=()=>details(selected);g.onfocus=()=>details(n);g.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();zoom(n);}};});
document.getElementById('reset').onclick=()=>zoom(nodes[0]);document.getElementById('search').oninput=search;zoom(nodes[0]);
</script></main></body></html>'''
    values = {
        "SOURCE": html.escape(source), "TOTAL": str(total), "STACKS": str(stack_count),
        "DURATION": duration, "STATUS": status, "START": time_text(start), "END": time_text(end),
        "SINGLE": single, "SVG": svg,
        "GAPS": (f"样本首末边界间记录了 {len(gaps)} 个缺口，合计 {sum(b-a for a,b in gaps)/1e6:.3f} ms。缺口可能来自空闲、丢样或缺失采集，不能据此证明全程连续。" if gaps else "当前样本边界未报告缺口；这也不能单独证明全程连续采集。"),
        "SUMMARY": summary, "NODES": json.dumps(nodes, ensure_ascii=True).replace("<", "\\u003c"),
    }
    # Single substitution pass: user symbols cannot become template placeholders.
    import re
    return re.sub(r"@@(" + "|".join(values) + r")@@", lambda m: values[m.group(1)], page)
