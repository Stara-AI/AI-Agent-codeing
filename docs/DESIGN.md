# CPU Blackbox 实现设计

当前状态：本地可测实现。宿主为 Windows，无 Linux/WSL；真实 perf、systemd、7x24 与业务损耗 <3% 待目标环境验收。

## 需求与实现

| 需求 | 实现 | 验收边界 |
| --- | --- | --- |
| 持续 CPU 采样 | Python 监督进程 + perf record 指定 PID | perf 调用与生命周期有回归测试，真实 Linux 尚未执行 |
| 历史窗口回查 | SQLite 分片索引 + 墙上/单调时钟映射 + 半开区间过滤 | 本地跨片查询、边界与时区已验证 |
| 前端火焰图 | 项目自带离线 SVG/HTML，缩放、复位、搜索 | 页面脚本已执行检查；没有实际浏览器渲染验收 |
| 自动轮转与磁盘保护 | perf 原生输出切换；保留期、字节预算和空闲阈值 | 合成流程验证轮转、查询、磁盘停止；真实轮转待验证 |
| 可复用操作流程 | skills/cpu-profiling/SKILL.md | Skill 校验通过；依赖随包项目 CLI |
| 交付证据 | Git 历史、测试输出、可见 AI 对话、ZIP/Git bundle | 交付包只包含已提交的最新文件及完整 Git 历史 |

## 架构

```text
collect supervisor -> perf record -> sealed perf.data -> perf script -> SQLite index
query -> clock mapping -> locked snapshot -> perf script -> window filter
      -> folded stack counts -> flamegraph.svg / index.html / report.json
simulate -> timestamped synthetic stacks -> same index/query/report pipeline
systemd -> foreground supervisor lifecycle + restart backoff
```

Python >=3.9，主程序只有标准库依赖。真实采集依赖目标内核适配的 perf；不下载第三方火焰图脚本，不依赖 Perl/CDN。可选 Node.js 检查生成页面的实际交互脚本。

## 采集与进程身份

指定一个业务 PID，记录 boot ID、/proc/PID/stat 的启动时间、perf 版本、配置和命令。退出或 PID 被复用时结束当前会话，不静默跟踪新进程。目标自动重启发现不在当前范围内。

默认 cpu-clock、49 Hz、frame pointer、60 秒轮转。默认值为测试起点；DWARF 与 cycles 为显式配置选择，不能假设相同开销。doctor 对目标做短时真实附着，核查事件、权限、CLOCK_MONOTONIC、轮转选项；不修改内核权限限制。

采集使用参数数组调用 subprocess。监督进程 SIGINT/SIGTERM 请求结束循环，再对 perf 发送 SIGINT，等待封口；超时强杀并记录 FORCED_STOP。启动或运行异常记录 FAILED，最后释放句柄、信号处理器和 SQLite 连接。

## 轮转、封口和恢复

perf 使用原生 --switch-output，避免周期性退出再附着。检查活跃 perf 的 /proc/PID/fd，只处理已经关闭的 perf.data.*；无法检查活跃文件时明确失败，避免把仍在写入的数据发布。

| 分片状态 | 含义 | 查询 |
| --- | --- | --- |
| AVAILABLE | 解码成功且有样本 | 可查询 |
| EMPTY | 解码成功但无样本 | 不查询 |
| CORRUPT | perf script 失败 | 不查询，保留诊断 |
| UNINDEXED | 超时或资源限制，未证明文件损坏 | 保留文件，调整限制后 reindex |
| EXPIRED | 清理过程中的删除状态 | 完成清理后移除索引行 |

ACTIVE 文件不登记为可查询片。正常停止将最后的 perf.data 改为 perf.data.final，再验证发布。恢复中验证遗留文件；同一次系统启动下发现原 perf 身份仍存活则拒绝第二个采集器。不同 boot ID 不用相同 PID/start ticks 判断孤儿进程。

重建索引要求停止采集并取得 collector.lock。reindex 会跳过 synthetic 会话，对 UNINDEXED 重新尝试；仍可能受磁盘与解码限制约束。

## 时间模型

用户输入必须是带时区的 ISO 8601；内部纳秒整数采用 [start,end) 过滤。perf 显式使用 CLOCK_MONOTONIC；定期记录 realtime/monotonic 配对锚点及读取误差。

映射采用最近锚点，并报告偏移漂移造成的可观测误差。系统时间跳变的会话保守拒绝自动查询，不能保证时钟同步误差的绝对上界。不同启动和会话分别映射。采样覆盖以实际首末样本估计；缺口可能是业务空闲、丢样或缺失采集，不能据此声称全程连续。

## 历史查询与前端

查询只读取已封口且仍保留的 AVAILABLE 分片；最新可见延迟为轮转周期加解析发布时间。查询跨度有上限，可指定 --session。

在 storage.lock 下选择并复制快照，之后释放锁进行解码；保留清理不会损坏快照输入。query.lock 限制单个并发查询。事件、频率、栈展开和真实/合成来源不兼容时拒绝混合。

perf script 显式指定 comm,pid,time,event,ip,sym,dso，避免默认线程标识被误当成进程 PID。解码使用有时限和容量限制的磁盘临时文件，stderr 有界读取。递归栈保留，头部重复叶节点只在 IP 和符号都相同时去重。

计数累加后生成离线 SVG/HTML、folded stacks 与 JSON。临时报告目录生成完成并检查字节预算后发布；现存报告不覆盖。文本和嵌入 JSON 做转义。宽度表示样本数，横轴不是时间。热点用于定位线索，不能自动证明业务根因。

退出码：0 正常；2 错误；3 部分样本覆盖报告或状态陈旧；75 磁盘压力停止。零样本不生成成功报告。

## 磁盘与保留

默认保留 24 小时、预算 2 GiB、最低空闲 1 GiB、检查周期 5 秒。统计管理目录的分片、ACTIVE、索引、日志、buildids、解码和查询快照。报告目录必须在采集目录外，由报告预算单独约束。

只删除索引登记的封口文件，安全路径校验拒绝越界和符号链接。淘汰按墙上时间最老优先，不比较不同启动的单调时间。没有样本时间的 EMPTY/CORRUPT/UNINDEXED 按文件完成时间保留，不能把时间 0 当成刚生成数据已过期。

清理只扫描一次目录容量，再按实际删除大小递减，避免逐片重复遍历整个目录。仍不足则停止采集；systemd 不自动重启退出码 75。

限制：预算为软限制；轮询间隔、perf 写入和并发快照可能短时超限，硬容量上限需文件系统配额。build ID 缓存、会话目录/日志和 SQLite 已分配空间不自动无限压缩，应按部署文档维护。查询崩溃遗留快照只能在确认无查询后处理。CPU/RSS 没有程序内硬上限，需实测并用 systemd/cgroup 约束。索引保存绝对文件路径，当前未实现数据目录迁移，应保持部署数据路径稳定。

## systemd

deploy/cpu-blackbox.service 是可部署模板，使用专用用户、私有状态目录、日志、启动节流、故障重启和磁盘压力停止策略。KillMode=mixed 先向监督进程发送 TERM，由它通知 perf 封口；剩余进程在超时或主进程退出后受控制组清理。

部署步骤、PID 环境文件、权限预检、配置验证和停止验收见 SYSTEMD.md。当前机器不能运行 systemd，配置契约测试不能替代 systemd-analyze 或真实 stop/start 验收。

## 性能与生产验收

tools/benchmark.py 在持久业务 PID 上执行至少五轮交错 A/B，负载命令返回吞吐、P95/P99、错误率和时长 JSON。每轮覆盖至少两次轮转，计算吞吐下降和保守置信上界，上界 <3% 才标记该负载通过。

测试指标构造数据不是真实测量；固定低请求速率不能证明业务损耗。还需记录目标/采集器 CPU、RSS、IO、数据增长、尾延迟和错误率，并完成 7x24、磁盘满、崩溃、服务重启和符号升级故障验收。

未支持：服务 PID 自动发现、整机/容器目标、off-CPU、通用 JIT/解释器业务栈、自动根因归因。保留对应二进制、调试符号和 build ID cache，防止旧样本不可解析。

## 依据

- [perf record](https://man7.org/linux/man-pages/man1/perf-record.1.html)
- [perf script](https://www.man7.org/linux/man-pages/man1/perf-script.1.html)
- [Linux perf security](https://cdn.kernel.org/doc/html/latest/admin-guide/perf-security.html)
- [systemd kill 官方源文档](https://github.com/systemd/systemd/blob/main/man/systemd.kill.xml)
- [systemd exec 官方源文档](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml)
