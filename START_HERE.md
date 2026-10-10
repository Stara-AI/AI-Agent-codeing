# 交付入口

进入项目文件夹（含 README.md）。需要 Python >=3.9，主程序只用标准库，不需联网安装依赖。GitHub 下载的源码也可直接运行。

Windows：双击 **run_demo.cmd** 演示，双击 **run_tests.cmd** 跑测试。如果使用 Anaconda，在 Anaconda Prompt 中进入项目目录运行这两个文件。

通用命令（从项目目录运行，Linux 可使用 python3）：

```bash
python -m cpu_blackbox --help
python -m unittest discover -s tests -v
python tools/start_demo.py --no-browser
```

最后一条生成新的合成采集、轮转、历史回查与火焰图，输出 runs/demo-* 中的页面路径；省略 --no-browser 会尝试打开浏览器。直接预览 [随包演示](artifacts/demo/report/index.html)。

Windows 演示是合成数据。真实采集需 Linux/perf、采集权限和可展开的目标调用栈，操作见 README。真实 perf、systemd、业务损耗 <3% 与 7x24 尚未在本机实测；测试报告明确区分已验证和未验证。

