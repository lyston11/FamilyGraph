# 远端 backend/sidecar 守护核查（已达标，无需实现）

> 父任务：09-15-agent-audit-remediation。优先级 P0。
> 状态：**审核发现误判——远端守护已完备**。本任务从"实现"转为"核查记录"，不产生代码改动。

## 背景

2026-09-15 深度审核时误判远端 backend/sidecar 为裸进程：当时用 `systemctl list-units`（system 级）与 `systemctl status familygraph-api.service`（system 级）查询，均为 not-found。实际项目在 `scripts/install-server-automation.sh` 中已有完整的 **systemd user 级**自动化安装器，且已安装运行。

## 核查结论（2026-09-15 复核）

- `familygraph-api.service`（user 级）：active (running)，PID 2392816，`python -m app.serve`，EnvironmentFile=familygraph.env，Restart=on-failure，TimeoutStopSec=15 与 serve.py 关停对齐。
- `familygraph-agent.service`（user 级）：active (running)，PID 2392817，`node dist/main.js`，健康端口 18080（`/healthz` 返回 process ok / fastapi reachable），After=familygraph-api。
- `loginctl show-user ubuntu -p Linger` = yes：注销/重启后自愈。
- 定时器：`familygraph-code-sync.timer`（每 30 分钟 commit+rebase+push）、`familygraph-db-backup.timer`（每小时备份）均 active。
- 远端 sidecar 日志干净：仅 run settled 记录，无 401。

## Requirements

- R1（仅文档性）：确保审核报告与父任务 PRD 中的"裸进程无守护"表述被本记录纠正。
- R2：无代码改动、无部署动作。

## Acceptance Criteria

1. 本核查记录归档。父任务 PRD 已更新误判说明。
2. 远端服务保持 active（用户要求的服务器常驻运行已满足）。

## 回滚

不适用。
