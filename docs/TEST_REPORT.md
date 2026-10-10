# 本地测试报告

环境：Windows 10 build 26200，Python 3.9.21，Node.js 22.16.0；无 Linux/WSL。

## 运行与本次结果

从项目根目录执行：

```bash
python -m unittest discover -s tests -v
```

本次执行日期：2026-10-10（Asia/Shanghai）；耗时 **9.078 秒**，进程退出码 **0**。

```text
Ran 53 tests in 9.078s
OK (skipped=1)
```

运行条件与按文件执行步骤见 [tests/README.md](../tests/README.md)。原始输出见 [test-results.txt](../artifacts/test-results.txt)，机器可读汇总见 [test-summary.json](../artifacts/test-summary.json)，包含测试时的 Git revision、时间和日志 SHA-256。


当前结果：**53 项，52 项通过，1 项真实 Linux perf 集成测试跳过，0 项失败。** 完整输出见 artifacts/test-results.txt。可选前端测试需要 Node.js，未安装时额外跳过该项。

| 范围 | 证据 | 状态 |
| --- | --- | --- |
| 时间、过滤与调用栈 | 时区、纳秒半开区间、PID/线程 PID、递归帧、事件隔离 | 本地通过 |
| 存储、轮转与保留 | 跨片选择、ACTIVE 保护、跨 boot 墙上时间排序、损坏片保留、旧索引迁移 | 本地通过 |
| 资源与解码 | 快照/报告预算、真实子进程超时停止、有界 stderr、UNINDEXED 保留与重试 | 本地通过；perf 使用替代子进程/Mock |
| 采集生命周期 | perf 启动失败、PID 复用、SIGINT 封口、停止超时强杀与回收 | 本地 Mock 回归通过 |
| 查询与前端 | 跨片窗口、无数据、路径、HTML/JS 转义、权重布局、缩放/复位/搜索 | 本地通过 |
| systemd | mixed 停止顺序、退出码 75 重启保护、私有状态目录、PID 参数 | 配置契约通过，未运行服务 |
| AI 对话与性能评估 | 可见记录过滤、指标噪声/时长不足不误判通过 | 本地通过；指标人工构造 |
| 真实 Linux perf | 编译热点目标、真实轮转、结束后回查 | 当前环境跳过 |

完整合成演示：`python tools/run_simulation_demo.py --output-root artifacts/demo`（目录须不存在）。实际启动合成采集子进程，执行 6 秒、1 秒轮转；子进程结束后调用同一查询实现回查。随包结果为 6 个分片、41 个热点窗口样本；采集退出码 0，查询状态映射为退出码 3（样本覆盖提示），热点检出且正常阶段被排除。结果、采集输出和页面保存在 artifacts/demo。

`node tools/check_report.js artifacts/demo/report/full/index.html` 验证了实际生成页面的来源标识、分支坐标、祖先/子路径、固定比例、复位和搜索；结果见 frontend-check.txt。这部分执行环境是最小 DOM 的 Node VM。

本次另外通过 Playwright + 本机 Chrome，在真实浏览器中离线检查 report/full/index.html。缩放前后 SVG 高度稳定，搜索、复位、无脚本错误与 390 px 小屏页面宽度检查通过。结果见 artifacts/demo/browser-check/result.json，截图为 full.png、zoom.png、mobile.png。此浏览器检查独立于 53 项 unittest，不验证真实 perf 或性能指标。

指定 Skill 的基线流程已在合成数据中执行：随包故障 41 样本、紧邻等长基线 40 样本、完整时段 203 样本；hot_leaf 故障占比 100%，基线 22.5%，增量 77.5 个百分点。完整时段出现两条路径，单路径故障图各层同宽符合统计。新增测试验证递归符号只计一次、各窗口独立归一化和缺失基线不误判新增热点；端到端测试同时核查三个窗口输出。详见 report/analysis.md 与 report/comparison.json。

Skill：`quick_validate.py skills/cpu-profiling` 已通过。模拟数据始终标注 SYNTHETIC，不能作为真实 CPU、业务根因或 <3% 性能证据。

尚未验证：真实 perf 兼容性和符号质量、真实停止/崩溃恢复、systemd start/stop、代表性业务 <3%、7x24。测试覆盖不表示不存在其他缺陷。旧生成产物在原工作目录归档，当前交付文件夹不重复包含。
