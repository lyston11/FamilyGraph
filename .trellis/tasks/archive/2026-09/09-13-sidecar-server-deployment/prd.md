# Agent sidecar 服务器常驻部署

## Goal

dev 服务器（lyston）从未部署 agent sidecar，assistant run 会永远停在 queued——这是「用户发消息很久没有回复」的直接根因。需把 sidecar 纳入服务器自动化，作为 systemd user service 常驻，消除对临时手工进程的依赖。

## 过程记录（2026-09-12 真实 Provider E2E）

- 在王氏家族空间（space 2）以王德海账号发起 assistant 会话并提交消息，run #2 入队后连续轮询 2 分钟始终 `queued`，无任何 lease 发生。
- 登录服务器排查：`systemctl --user list-units 'familygraph*'` 仅有 `familygraph-api.service`（public 8000 / internal 8001 / admin 8002）、code-sync timer、db-backup timer；`docker ps` 无 FamilyGraph 容器；`pgrep -af agent` 无 sidecar 进程。结论：**三个 listener 有 systemd 管，执行 run 的 sidecar 没人管**。
- 临时处置（当前仍在服务器上运行，非持久）：
  - 服务器仓库 `agent/` 目录只有源码，`node_modules`/`dist` 不同步：`npm ci` + `npm run build` 在服务器完成（node v22.22.2；package engines 要求 >=24，npm 仅告警未阻断）。
  - 启动方式：`bash -c "set -a; . /home/ubuntu/.config/familygraph/familygraph.env; set +a; FG_API_BASE_URL=http://127.0.0.1:8000 FG_INTERNAL_API_BASE_URL=http://127.0.0.1:8001 AGENT_SIDECAR_ID=dev-e2e-probe HEALTH_PORT=18080 nohup node dist/main.js > /tmp/fg-agent-probe.log 2>&1 &"`
  - 密钥（AGENT_SERVICE_SECRET）只在服务器进程内存，未落本地、未进日志。
  - 健康端口注意：服务器 8080 已被占用（首次启动 EADDRINUSE 崩溃），改用 18080。
  - 启动后 run #2 约 30s 内被租走并 `succeeded`，真实模型回复到达，链路验证通过。
- 风险：nohup 进程在服务器重启/会话清理后消失，assistant 将退回「永远 queued」且无任何告警。

## Requirements

- 新增 `familygraph-agent.service`（systemd user service）纳入 `scripts/install-server-automation.sh`：
  - `WorkingDirectory=/home/ubuntu/projects/FamilyGraph/agent`，`ExecStart` 用服务器 node 跑 `dist/main.js`；
  - 复用 `EnvironmentFile=/home/ubuntu/.config/familygraph/familygraph.env`，并注入 `FG_API_BASE_URL=http://127.0.0.1:8000`、`FG_INTERNAL_API_BASE_URL=http://127.0.0.1:8001`、不冲突的 `HEALTH_PORT`（如 18080）与固定 `AGENT_SIDECAR_ID`；
  - `Restart=on-failure` + 合理 `RestartSec`，日志进 journal。
- 安装脚本包含依赖安装与构建步骤（`npm ci` + `npm run build`），代码更新后可重复执行（幂等）。
- `scripts/dev-up-remote.sh` 健康检查覆盖 sidecar 存活（通过 ssh 服务状态），缺失时给出可执行提示而非静默通过。
- 明确 node 版本策略：服务器 node 22 与 `engines: >=24` 的告警要么消解（升级 node）要么显式记录豁免。

## Acceptance Criteria

- [ ] 服务器重启后 sidecar 由 systemd 自动拉起，`systemctl --user is-active familygraph-agent` 为 active。
- [x] 临时 nohup 进程（dev-e2e-probe）下线，由 service 承接；提交 assistant 消息后 run 能在正常时间内被租走并进入终态。
- [x] `install-server-automation.sh` 幂等重跑不产生重复服务或端口冲突。
- [x] dev-up-remote.sh 探测能发现 sidecar 缺失并提示。
- [x] 运维文档（README 或 spec）补充 sidecar 部署与排障说明。

## 实现记录（2026-09-13）

提交 c819892 `feat(ops): run agent sidecar as systemd service with idempotent install and dev probe`：

- `scripts/install-server-automation.sh`：新增 `familygraph-agent.service`（systemd
  user service）——WorkingDirectory=agent，`npm ci` + `npm run build` 幂等重建，
  `EnvironmentFile` 只读复用（AGENT_SERVICE_SECRET 仅进程内存），固定注入
  `FG_API_BASE_URL`/`FG_INTERNAL_API_BASE_URL=127.0.0.1`、`AGENT_SIDECAR_ID=lyston-server-1`、
  `HEALTH_PORT=18080`（8080 服务器已占用）；`Restart=on-failure`+`RestartSec=5`+
  `TimeoutStopSec=15`；node 二进制安装时解析为绝对路径；`is-active` 非运行时安装器
  输出可执行告警。
- `scripts/dev-up-remote.sh`：新增经 ssh 的 sidecar systemd 存活探测，缺失时输出
  修复命令（安装器 / systemctl start / journalctl 口径），FAILED=1 不再静默通过。
- `README.md` 运维手册新增「Agent sidecar 裸机部署（lyston 服务器，systemd）」：
  角色、环境、健康/日志、四步排障口径、node v22 与 engines >=24 的豁免记录
  （非 engine-strict 仅警告，实测构建运行正常；npm 收紧或 SDK 依赖 24+ 前有效）。

服务器切换实测（lyston）：
- 临时 nohup 进程（PID 329037，09-12 起）确认无 in-flight run（agent_runs 仅 2 条
  succeeded）后 kill，18080 释放，由 service 承接；
- service 启动即 `active`，`sidecar_id=lyston-server-1`，`/healthz`/`/readyz` 均
  process ok + fastapi reachable（provider "missing" 为预期：env 桩仅就绪报告，
  真实凭据由 ProviderGateway 下发）；
- 协议级验证：以同密钥铸造 service token `POST /internal/agent/jobs/lease` → 204
  （认证与租约协议通；完整 run E2E 已由 09-12 探针进程实测，同一代码路径）；
- 安装器幂等重跑两轮：无重复单元、无端口冲突、双服务持续 active；
- `is-enabled=enabled` + `Linger=yes`：服务器重启后自动拉起。

备注：当前本地工作区被并发会话 checkout 在 `feat/steward-term-autofix`（其改动均
未提交，本任务提交经 `git push origin <sha>:main` 直达 main，未动其分支状态）。

## Notes

- 延迟问题（lease 轮询间隔、模型推理时长）不在本任务展开，见 `09-13-agent-latency-tuning`。
- 服务端 env 由本任务只读复用，不得在脚本输出、任务文件或日志中出现任何密钥。
