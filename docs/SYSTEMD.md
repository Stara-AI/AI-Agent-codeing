# systemd 部署与验收

当前仅审查配置并通过本地契约回归测试；Windows 上没有执行 systemd。以下命令由目标 Linux 管理员运行，不会在本机自动安装服务。

## 前置条件

- 项目完整放在 /opt/cpu-blackbox；/usr/bin/python3 >=3.9，perf 与内核匹配。
- 已存在 cpu-profiler 系统用户/组，对项目有读权限。
- 管理员按内核安全文档提供限定的 perf 权限，保证专用用户能读取目标进程必要信息。模板不自动降低 perf_event_paranoid 或授予全局 root。
- 确认调用栈展开可用。NoNewPrivileges=true 下不能依赖后续 setuid 获权。

例如，尚无该用户时可按本机账户策略创建：

```bash
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin cpu-profiler
```

## 安装配置

从 /opt/cpu-blackbox 运行，先将环境文件中的示例 PID 改为实际业务 PID：

```bash
sudo install -o root -g cpu-profiler -m 0640 config.example.json /etc/cpu-blackbox.json
sudo install -m 0600 deploy/cpu-blackbox.env.example /etc/cpu-blackbox.env
sudoedit /etc/cpu-blackbox.env
sudo install -m 0644 deploy/cpu-blackbox.service /etc/systemd/system/cpu-blackbox.service
sudo systemd-analyze verify /etc/systemd/system/cpu-blackbox.service
sudo -u cpu-profiler /usr/bin/python3 -m cpu_blackbox --config /etc/cpu-blackbox.json doctor --pid 1234
sudo systemctl daemon-reload
sudo systemctl enable --now cpu-blackbox.service
sudo systemctl status cpu-blackbox.service
sudo journalctl -u cpu-blackbox.service -n 100 --no-pager
```

doctor 的 PID 应与环境文件一致；doctor 成功不代表全部符号、systemd 沙箱权限和性能验收成功。StateDirectory 创建 /var/lib/cpu-blackbox，权限 0700。自定义路径时同时修改数据参数、StateDirectory/ReadWritePaths 和配置。

## 验收步骤

1. 确认服务 active，状态 JSON 为 RUNNING，perf 子进程存在。
2. 等待至少两个轮转周期，确认 SQLite 中有多个 AVAILABLE 分片；观察 perf.log 的丢样/权限/展开警告。
3. 热点结束后从已保留时间窗口查询，检查 sample_count、unknown_samples、warnings、clock_mappings 和缺口，打开 index.html。
4. 运行 `sudo systemctl stop cpu-blackbox.service`，确认末片登记、会话 STOPPED、perf 子进程退出；用 journalctl 检查失败情况。
5. 在隔离测试数据目录验证磁盘压力退出码 75，服务不进入重启循环。排除压力后由管理员显式启动。
6. 运行真实 Linux 集成测试和代表性业务 A/B 性能验收；保留原始日志。生产前另做 7x24 长跑与故障注入。

KillMode=mixed 避免 systemd 在监督进程通知 perf 前就同时终止所有进程。TimeoutStopSec=150 是示例；增加 perf_timeout_seconds 或积压多个待解码分片时必须同步调整停止预算。超时强制清理可能使末片损坏。

目标退出结束会话，Restart=on-failure 不会自动发现重启后的新 PID。需要更换 PID 时更新环境文件，再显式启动服务。

参考：[官方 KillMode 文档](https://github.com/systemd/systemd/blob/main/man/systemd.kill.xml)、[官方 StateDirectory 文档](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml)。
