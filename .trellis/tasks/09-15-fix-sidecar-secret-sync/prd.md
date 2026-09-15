# 修复本地 sidecar 401：同步 AGENT_SERVICE_SECRET

> 父任务：09-15-agent-audit-remediation。优先级 P0。

## 背景

本地 agent sidecar（PID 60095，监听 8080）经 launchd SSH 隧道（com.familygraph.dev-tunnel，映射 8000/8001/8002）轮询远端 backend `/internal/agent/jobs/lease`，持续返回 401 `AGENT_TOKEN_INVALID`——本地根 `.env` 与远端 `/home/ubuntu/.config/familygraph/familygraph.env` 的 `AGENT_SERVICE_SECRET` 不一致。本地开发时 agent 任务无人消费，且 401 轮询持续刷远端安全审计。

## Requirements

- R1：以远端 env 为准，把远端 `AGENT_SERVICE_SECRET` 同步进本地根 `.env`（密钥值不写入本任务文件、不提交、不打印）。
- R2：重启本地 sidecar（dev-up 流程或手动），验证 `.dev-logs/agent-sidecar.log` 不再出现 401，lease 请求返回 200/204（无任务时返回空 job）。
- R3：远端侧不改任何东西；远端已有自己的 sidecar 正常运行。

## Acceptance Criteria

1. 本地 sidecar 日志连续 30s 无 401。
2. `curl -s -X POST http://localhost:8001/internal/agent/jobs/lease`（带正确 service token 由 sidecar 自身发起）不再 401。
3. `.env` 变更不进 git（确认 .gitignore 覆盖）。

## 回滚

把本地 `.env` 的 `AGENT_SERVICE_SECRET` 改回原值即可。
