# 手动上传 GitHub

提交整个项目文件夹里的内容：README、源码、deploy、tools、tests、skills、docs 和 artifacts。Python >=3.9；主程序无需联网安装依赖。运行入口见 START_HERE.md。

## 推荐：GitHub Desktop

1. 选择 File → Add Local Repository，选择这个项目文件夹（已有隐藏 .git 目录）。
2. 选择 Publish repository，设置仓库名称和可见性。
3. 发布后将仓库链接发给面试官。

这个方式会保留已有开发提交历史。

## 使用 Git

先在 GitHub 创建空仓库，不勾选自动添加 README。然后在本项目目录运行，将 URL 替换为你自己的仓库：

```bash
git status
git remote add origin https://github.com/YOUR_USERNAME/cpu-blackbox.git
git push -u origin master
```

当前交付分支为 master。若你自行改过分支名称，最后一条替换为实际分支。

## 使用网页 Upload files

上传所有项目文件和子目录，包括 artifacts 中的测试结果和 git-history.bundle。AI 对话记录由你自行整理后补充。网页上传无法保留本地 .git 提交历史，会产生新的上传提交；历史备份可以独立恢复：

```bash
git clone artifacts/git-history.bundle cpu-blackbox-history
cd cpu-blackbox-history
git log --oneline
```

备份范围见 artifacts/git-history.json；备份形成后的归档提交在正常 Git 推送的仓库历史中。README 与测试报告说明真实 Linux 验证范围，提交时应保留这些说明。
