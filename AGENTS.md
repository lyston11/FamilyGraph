# Agent Notes Instructions

本项目采用 [write-notes-like-deepseek](.write-notes-like-deepseek/SKILL.md) 记录需要长期保留的工程决策。`.trellis/` 目录仅作为历史资料保留，不再作为任务、规范或会话入口；新的工作不得创建或更新 Trellis task/spec/workspace 文件。

## 架构决策留痕

对行为、架构、协议、跨模块约定、测试策略或删除面有非显然取舍时：

1. 先阅读 `.write-notes-like-deepseek/SKILL.md` 及相关 `references/`。
2. 动手前在 `.agent-notes/proposed/<class>/` 写提案；完成后在同一次提交中转为 `.agent-notes/implemented/<class>/`。
3. 每篇 Note 必须记录至少两个真实备选，并包含“不做/复用已有能力”的选项。
4. 受决策保护的核心入口保留 `Note: ... — 见 .agent-notes/...` 反向注释。
5. 纯格式、无歧义改名、依赖补丁和发布打标不需要 Note；拿不准时按需要记录。

Note 生命周期只有 `proposed`、`implemented`、`rejected`、`archived`，类别只有 `feature`、`bug-fix`、`simplification`、`architecture`、`process`、`testing`。路径即状态，不建立中心索引。

提交前运行：

```bash
npm run verify-agent-notes
```

看板可用 `npm run init-board` 生成 `board.html`，默认读取 `.agent-notes/`。

# 开发服务启动与验证

按需要选择一种模式，两个脚本都幂等并在结束前执行健康检查：

| 场景 | 命令 | 服务位置 |
|---|---|---|
| 远程开发（已安装 `com.familygraph.dev-tunnel`，默认） | `./scripts/dev-up-remote.sh` | 后端、SQLite、dbx 在服务器；本地前端和 SSH 隧道 |
| 全本地开发或无远程隧道 | `./scripts/dev-up.sh` | 三个后端 listener 和两个前端均在本机 |

远程脚本将本地 `8000/8001/8002/4225` 映射到服务器，前端仍使用 `5173/5174`。两种模式不要同时运行，以免端口冲突。若用户只要求验证一个已有服务，可直接访问其健康端点；启动完整开发环境时优先使用对应的一键脚本。脚本日志位于 `.dev-logs/`（已忽略）。

AI 沙箱可能在命令结束时回收 `nohup` 子进程；需要持续运行时使用平台的后台任务机制，并在同一任务中完成健康检查。单独启动某个 listener 仅用于故障排查或定位测试，不改变一键启动约定。

完成标准：目标服务的健康端点返回 HTTP 200，前端端口可访问；若启动失败，保留日志并报告阻塞原因，不把“进程已派生”当作成功。

远程后端更新：服务器执行 `git pull` 后运行 `systemctl --user restart familygraph-api`；有新迁移时先执行 `alembic upgrade head`。服务器日志使用 `journalctl --user -u familygraph-api -f`。数据库备份和 dbx 均由服务器侧运维流程负责。
