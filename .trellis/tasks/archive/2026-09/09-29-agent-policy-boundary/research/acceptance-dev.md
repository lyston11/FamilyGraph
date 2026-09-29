# 开发环境验收记录（2026-09-29）

环境：`/home/ubuntu/projects/FamilyGraph`（systemd 用户单元 `familygraph-api` /
`familygraph-agent`，`~/.config/familygraph/familygraph.env`）。
**线上（`/home/ubuntu/fg-prod`、`familygraph-prod-*`）在本任务中未被操作。**

## 部署版本

| 项 | 值 |
|---|---|
| 代码 | `19e3df9`（`git merge --ff-only origin/main`） |
| agent 构建 | `agent/dist/policy.js` 含新码 `POLICY_SECRET_IN_PROVIDER_PAYLOAD`、中性占位文本、`instruction_marker` 规则 |
| 数据库 | `0055_steward_assist_execution_unit`（本任务无迁移） |
| 服务 | 两个用户单元已重启，`active` |
| 健康 | 家庭 `:8000/api/health` 200；管理 `:8002/admin-api/health` 200；sidecar `:18080/healthz` 200；`:8001/health` 404（内部 listener 不暴露健康路由）；家庭 listener 上 `/admin-api/health` 404（隔离未破） |

有效配置（从运行进程的环境读取，不输出密钥）：`AGENT_RUNTIME_ENABLED=1`、
`STEWARD_ENABLED=1`、`STEWARD_WORKER_ENABLED=1`、`STEWARD_PI_RUNTIME_ENABLED=1`、
`FG_AGENT_ROLE=both`、四类 `STEWARD_ASSIST_*` 均启用、`POLICY_GUARD_ENABLED` 默认开启。

## AC-8 四项分列

| 项 | 结果 |
|---|---|
| 部署有效启用 | ✅ 上述版本与服务状态 |
| 真实模型调用 | ✅ run 65（`kind=steward`），egress 审计 `target_id=65`、`status=succeeded`、`upstream_status=200`、`bytes_read=504401` |
| 取得合法输出 | ✅ attempt 1057（candidate）`status=succeeded`、`applied_at=08:22:44`、`total_tokens=4147`、`output_json` 82 字符 |
| 自动写回/可见结果 | ⚠️ `applied_at` 已写；但**未观察到用户可见的称谓/推荐变化**——本批其余 attempt 均 `skipped/provider_unavailable` 或 `budget_exhausted`，与本次改动无关（空间 2 的 model 不在 provider 白名单内，见下）。不得据此宣称业务改善 |

## 关键判定：修复改变了结果，不是输入变了

同一空间、同一 kind、**完全相同 `input_hash`** 的三次 attempt：

| attempt | run | 结果 | input_hash |
|---|---|---|---|
| 1025 | 61 | `failed` / `POLICY_SECRET_LEAK`（旧代码，06:22） | `eb8f967b…` |
| 1049 | 64 | `succeeded`（中间版本，07:52） | `eb8f967b…` |
| 1057 | 65 | `succeeded`（新代码，08:22） | `eb8f967b…` |

输入字节完全一致而结果不同，说明触发条件不在输入，而在模型自身措辞（旧代码扫描
assistant 上一轮文本）。这印证了机制缺陷，同时**不**构成对历史 run 48/61 具体触发文本的
回溯证明——那两次未记录违规类型与文本，仍属未知。

## 误报机制的证据

线上开发库中 `steward_generation_views.skeleton_json` 的 `masked` 来源已核实为
`{"__masked__": true}`（可见性遮罩占位符，即"该字段对当前 viewer 不可见"的**授权**信号），
不是受限数据。契约检查只匹配精确字段名 `masked`，因此占位符不会触发阻断；`mask**` 子串
匹配会让每个含遮罩字段的投影失败，该区分已由测试锁定（M9 变异验证）。

## 未执行/未观察到

- **Assistant 真实链路**：开发库最后一次 assistant run 是 2026-09-20，本任务期间无 assistant
  流量，因此**没有**观察到真实 assistant 请求。assistant 路径由
  `agent/test/worker.integration.test.ts` 的真实 SDK 集成用例覆盖（含出站计数、重试、错误分类），
  以及 `frontend/src/api/__tests__/agentErrors.spec.ts` 的文案断言。不得宣称已做真实 assistant 验收。
- **空间 2 的 `provider_unavailable`**：其 `model='workbuddy/gpt-5.6-sol'` 不在 provider 2
  `allowed_models=['deepseek-v4.1-flash']` 内。属既有配置漂移，按 PRD 不在本任务范围，未修改。
- **历史 run 48/61 的具体触发文本**：无记录，不可恢复，未伪造归因。
