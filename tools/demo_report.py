"""Generate a clearly labelled synthetic offline report; not a perf validation."""
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cpu_blackbox.query import render
out = Path(sys.argv[1] if len(sys.argv)>1 else "artifacts/demo-report")
counts=Counter({("online-service [4242]","request_handler","delete_table","scan_pages"): 420,
                ("online-service [4242]","request_handler","gc_worker","mark_objects"): 210,
                ("online-service [4242]","request_handler","lock_retry","spin_loop"): 100,
                ("online-service [4242]","request_handler","serialize_response"): 70})
render(counts,{"source":"SYNTHETIC DEMO / 合成演示数据，不能作为真实采集或根因证据", "sample_count":800,
               "status":"demo","scenario":"过去时间窗口的 CPU 火焰图页面演示", "limitations":"真实 Linux 集成和 <3% 性能验收尚未执行"},out,64*1024**2)
print(str(out.resolve()/"index.html"))
