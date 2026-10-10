# 可运行测试与执行结果

## 运行环境

主程序和 Python 测试使用标准库，要求 Python >=3.9，无需安装 pytest。前端交互测试可选 Node.js；未安装时该项明确跳过。

真实 Linux 集成测试额外要求 Linux、perf、gcc，以及目标进程采样权限。当前交付机为 Windows，无法运行该项。systemd 的本地测试检查配置契约，不启动真实服务。

## 一条命令运行全部测试

解压项目并进入含 README.md 的 cpu-blackbox 项目根目录：

```bash
python -m unittest discover -s tests -v
```

退出码 0 表示已执行测试通过；unittest 允许跳过，因此还必须检查输出中的 skipped。任何真实验收所需测试被跳过，都不能作为该指标通过的证据。

PowerShell 保存新的执行结果时，可使用：

```powershell
python -m unittest discover -s tests -v 2>&1 | Tee-Object -FilePath artifacts/test-results-new.txt
$LASTEXITCODE
```

Linux 保存输出并保留 Python 退出码的示例：

```bash
python -m unittest discover -s tests -v > artifacts/test-results-new.txt 2>&1
result=$?
cat artifacts/test-results-new.txt
exit "$result"
```

## 按文件运行

将下列命令的文件名替换为表格中的目标文件：

```bash
python -m unittest discover -s tests -p test_core.py -v
```

| 文件 | 用例数 | 覆盖内容 |
| --- | --- | --- |
| [test_core.py](test_core.py) | 24 | 时区、区间边界、调用栈、查询、磁盘预算、清理、报告与转义 |
| [test_evidence.py](test_evidence.py) | 5 | 解码子进程超时/错误、性能判断与可见对话过滤 |
| [test_review.py](test_review.py) | 18 | PID 复用、末片封口、跨 boot 清理、重试索引、配置契约、前端交互和异目录端到端演示 |
| [test_simulation.py](test_simulation.py) | 3 | 合成轮转/历史热点回放、磁盘压力停止和无效时长 |
| [test_analysis.py](test_analysis.py) | 2 | 递归符号去重、独立窗口占比、缺失基线不误判新增热点 |
| [test_linux.py](test_linux.py) | 1 | 真实 perf 采集、轮转和热点结束后的历史查询 |
| 合计 | 53 | 本次 52 通过、1 Linux 集成跳过 |

真实集成测试通过采集就绪门控启动热点，不使用当前 perf top 的调用栈替代历史证据。权限/能力不足会明确跳过并保留原因。

## 本次执行结果

2026-10-10，Windows / Python 3.9.21 / Node.js 22.16.0：

```text
Ran 53 tests in 9.078s
OK (skipped=1)
```

进程退出码：0。跳过原因：`real Linux perf and gcc required`。

- [原始执行日志](../artifacts/test-results.txt)：逐个测试结果及真实程序输出。
- [机器可读结果](../artifacts/test-summary.json)：数量、耗时、环境、代码 revision 与日志校验值。
- [测试报告](../docs/TEST_REPORT.md)：覆盖矩阵、合成演示与尚未验证项。

日志中的 UNINDEXED/timeout、DISK_BUDGET 等输出来自主动注入资源故障的测试。对应测试断言这些行为正确，不能仅凭输出文字判断整个测试失败；应检查每个用例结果和最终汇总。

## 单独验证演示与页面

```bash
python tools/run_simulation_demo.py --output-root artifacts/test-demo-new
node tools/check_report.js artifacts/test-demo-new/report/full/index.html
```

输出目录必须不存在。模拟样本和页面带 SYNTHETIC 标记；页面脚本检查在最小 DOM 环境中执行，不能替代浏览器视觉验收。

可选真实浏览器检查需预先可用的 Playwright 与 Chromium，不是 Python 主程序依赖；在维护环境中执行：

```bash
node tools/check_report_browser.js artifacts/demo/report/full/index.html artifacts/browser-check-new /path/to/chromium
```

Windows 可把最后的参数替换为加引号的 Chrome/Edge exe 路径；Playwright 包需可被 Node 找到。输出目录必须不存在。保存真实页面、缩放、小屏截图和 result.json；本次交付证据位于 artifacts/demo/browser-check。浏览器检查独立于 unittest 总数。

本地测试不证明真实 perf/systemd 兼容性、业务开销 <3% 或 7x24 稳定性。真实业务性能验收使用 tools/benchmark.py，方法见项目 README；生产前仍需目标 Linux 环境执行集成和长期测试。
