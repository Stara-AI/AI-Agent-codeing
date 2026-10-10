# 交付清单与验收状态
## 原题四项交付

| 交付要求 | 文件/方式 |
| --- | --- |
| 完整代码、Git 仓库与提交历史 | cpu_blackbox/、tools/、deploy/、本地 .git；artifacts/git-history.bundle 为便于网页上传的历史备份 |
| README、使用与设计说明 | README.md、docs/DESIGN.md、docs/SYSTEMD.md、docs/REVIEW.md |
| 可运行测试与执行结果 | tests/（含运行说明）、artifacts/test-results.txt、artifacts/test-summary.json、docs/TEST_REPORT.md；53 项、52 通过、1 Linux 跳过 |
| AI 完整对话导出 | 由提交者自行整理和另行提供；当前仓库不附带聊天文件 |

附加交付：skills/cpu-profiling/SKILL.md，可复用采集/诊断/历史查询流程；artifacts/demo/report/index.html 为唯一随包演示入口。

演示包含故障、等长基线和完整时段的报告；artifacts/demo/browser-check 为实际浏览器执行证据。外部 Skill 仅参考其分析流程，本项目独立运行，其 Go/perf 引擎没有在 Windows 执行。当前文件夹只保留最新演示；旧运行目录和说明归档保留在原工作目录中。

提交前还需补齐第 4 项真实聊天记录，注明导出截止时间，不能以本文或人工摘要冒充完整对话。

## 功能确认

| 检查项 | 当前结果 | 证据 |
| --- | --- | --- |
| 采集脚本 | 已实现 perf 监督、身份检查、封口与故障状态 | collector.py 与 test_review.py；真实 perf 待验收 |
| 轮转与容量 | 已实现原生轮转、封口索引、保留、空闲阈值和保护性停止 | storage.py、模拟执行与回归测试 |
| 时间查询/火焰图 | 跨片历史窗口和离线页面已跑通 | artifacts/demo/result.json、report/、frontend-check.txt |
| systemd 服务化 | 提供配置、PID 示例与完整部署文档；配置契约通过 | deploy/、docs/SYSTEMD.md；运行待验收 |
| 测试 | 53 项执行，52 通过、1 条 Linux 集成因环境跳过 | artifacts/test-results.txt |
| <3% 与长期运行 | 提供真实业务 A/B 测试工具 | benchmark.py；当前没有真实结果 |

## 获取与恢复

项目已包含 .git 提交历史。使用 GitHub Desktop 或 Git 推送本项目，可保留开发提交；如果使用网页上传文件，可通过 artifacts/git-history.bundle 恢复开发历史，覆盖范围见 artifacts/git-history.json。

从本项目目录可执行：

```bash
git clone artifacts/git-history.bundle cpu-blackbox-repo
cd cpu-blackbox-repo
git log --oneline
python -m unittest discover -s tests -v
python tools/start_demo.py --no-browser
```

报告页面可离线打开。交付文件夹不包含运行期数据库和本地工具依赖；新演示会生成自己的索引与数据。真实采集仍需要 Linux/perf 和适当权限。

Windows 也可运行 run_demo.cmd、run_tests.cmd；首次使用见 START_HERE.md。独立交付目录运行验证见 artifacts/delivery-verification.json，上传步骤见 docs/GITHUB.md。

本次只提交到本地 Git 并提供交付文件，未向面试官、外部平台或远程仓库上传。


AI 会话记录：[筛选副本](AI_CONVERSATION.md)。首页注明删减范围，JSONL 保留工具交互，原版另存于项目外。此副本不是未经删减的完整会话，必要时可另附原版。
