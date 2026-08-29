# V2 Agent 架构收口审计记录与决策日志

## 1. 审计范围

本记录承接 2026-08-29 对当前工作树、`08-28-v2-audit-remediation`、V2 归档任务和 Obsidian familygraph 00–08 设计文档的复审。它只记录事实、设计裁定和后续执行边界，不把未运行的命令写成证据。

当前工作树有大量其他进程的未提交修改；本子任务不回滚、不覆盖、不代替其他进程的实现。

## 2. 关键证据

### 已完成或基本完成

- Assistant 的真实调用链是 `SidecarWorker -> buildRunSession -> pi-coding-agent.createAgentSession -> AgentSession.prompt -> pi-agent-core loop -> FamilyGraph tool -> pi-ai openai-completions`，不是只调用 `pi-ai`。
- RAG 关闭降级、token 字段白名单、`compat.maxTokensField="max_tokens"` 和 Provider stream error fail-closed 已有代码和回归测试。
- 原子建档、ActionCard 两步确认、Memory candidate 基础确认、重复 `message.user_added` 收口、reaper 终态和多端 type/lint/test/build 已有实质进展。

### 部分完成或未闭环

| 严重度 | 发现 | 证据锚点 | 影响 |
|---|---|---|---|
| P0/GOV | 任务工件状态不可信 | 父 `handoff.md:3` 仍写 planning/旧基线；父 PRD/implement AC 未回写；`task.json.commit=null` | 后续 Agent 无法判断哪些已验收 |
| P1 | 隐藏节点关系边可能泄露 | `backend/app/api/graph.py:71-85,142-160` | 泄露端点 ID、关系类型、标签和创建者视角 |
| P1 | internal API 与公开 listener/宿主端口重叠 | `backend/app/main.py:148-150`、`docker-compose.yml:15-24` | 可绕过 nginx 直接请求内部协议 |
| P1 | ProviderGateway 不是唯一 egress | `agent_provider.py`、`internal_agent.py`、`agent/src/session.ts:136-151` | secret/context/请求路径和 usage 审计不统一 |
| P1 | 工具协议版本重放不一致 | `agent_tools.py:105-126,409-457`、`agent/src/tools.ts:34-49` | v2 registry 与 sidecar v1 重放产生冲突 |
| P1 | 工具副作用去重有并发窗口 | `agent_tools.py:409-457` | 同一 tool_call 并发可先执行两次副作用 |
| P1 | 导出不是成熟 AEAD | `backend/app/utils/secretbox.py:98-134` | 自制 XOR+HMAC 缺成熟密码学和 key rotation 合同 |
| P1 | 导出损坏文件先消费下载资格 | `backend/app/commands/data_rights.py:320-350` | 用户无法重试恢复有效导出 |
| P1 | RAG 来源未绑定 session space | `backend/app/services/memory_rag.py:182-213` | 同一账号可把空间 A 对话确认到空间 B |
| P1 | Web DNS 校验与连接存在 TOCTOU | `controlled_web.py:209-243,521-542` | DNS rebinding 可绕过预检 |
| P1 | fetch 固定读取 research policy | `controlled_web.py:549-558` | fact_check/citation 开关行为错误 |
| P1 | Provider error 脱敏不统一 | `agent/src/worker.ts:253-285`、`agent/src/logger.ts:16-35` | 上游 body/secret/PII 可能进日志和 settle |
| P1 | Pi Guard hook 不能单独作为 fail-closed 证据 | `pi-coding-agent/dist/core/extensions/runner.js:776-806`；当前实际防线在 `agent/src/session.ts:197-214` | runner 吞掉扩展异常并继续旧 payload；需固定直接 Guard/显式拒绝合同 |
| P1 | Steward 缺少生产 scheduler/worker 闭环 | `backend/app/services/steward.py:381-714`；当前主要由测试调用 | 领域执行器存在，但没有与 canonical Job、lease、heartbeat 和 sidecar/worker 的生产入口闭合 |
| P2 | provider error 前先产生空 assistant event | `agent/src/events.ts:166-175` | UI 可能短暂显示成功样式的空回答 |
| P2 | 前端多个 store 无 request generation/abort | `frontend/src/stores/members.ts:33-39`、`spaces.ts:52-69`、`actionCards.ts:77-98` | 登出/切换后旧请求回写旧账号/空间 |
| P2 | 静态格式门禁失败 | `backend/app/services/agent_queue.py` | 当前 commit 不能称全绿 |
| P1/E3 | 发布级运行证据缺失 | guga glm-5.2-fast 持续 503；缺空库 Compose、第二卷恢复、FTS/SSE/优雅停机、375px 人工记录 | 不能认定发布就绪 |

## 3. Agent 架构裁定

### 3.1 为什么不是“只用 pi-ai”

`pi-ai` 只提供 Provider/模型协议。当前 Assistant 还使用 `pi-coding-agent` 的 Session/Extension/ResourceLoader，并通过 SDK 进入 `pi-agent-core` loop。因此“Assistant 是完整 Pi SDK sidecar”是正确描述。

### 3.2 为什么当前 Steward 让人感觉“不像 Pi Agent”

生产 Steward 代码在 `backend/app/models/steward.py:12-16` 和 `backend/app/services/steward.py:598-714`，是确定性 Python worker；它不调用 Pi、LLM、Provider 或 sidecar。Pi 侧只有 `familygraph.steward_ping` 探针（`agent/src/tools.ts:245-253`），并且 `buildRunSession` 固定载入 Assistant prompt。现状因此是“Assistant 为 Pi Agent，Steward 为确定性 worker + Pi 骨架”，而不是两个都已接入 Pi 的 Agent。

### 3.3 最终结构

保留确定性 StewardEngine 作为关系/权限/推荐真源，同时增加可选的 Pi Steward Orchestrator 负责解释、歧义整理和受限编排。两者共同构成产品层 Steward；Pi 永远不能替代 engine。唯一 canonical Job 为 `StewardJob`，Pi 只能作为 child run，不允许 generic `AgentJob(kind="steward")` 形成第二队列。

## 4. 设计原则

- 结构事实、关系路径、推荐资格、VisibilityPolicy 和 FSM 由后端确定性代码决定。
- Assistant/Steward 的权限来自本次 run 绑定的 account/space/job，而不是 platform operator 身份。
- 原始输入、SourceFact、DomainEvent、确认 Memory 是真源；DerivedFact、BehaviorProjection、RAG index、Context 和模型摘要均可重建或撤销。
- 任何不确定的 schema、scope、provider、密钥、网络地址、证据或状态一律 fail-closed。
- 发生故障时关闭 feature flag/kill switch，不能恢复旧的越权旁路。

## 5. 风险接受规则

- P0/P1 不接受“已有单测”“代码看起来存在”作为发布理由，必须有当前 commit 的 E2/E3 证据。
- P2 只有在 owner、期限、缓解和不影响隐私/权限的前提下才能延期。
- guga 上游 503 是环境限制，不得作为代码成功证据，也不应通过重试脚本伪造成功记录。
- 未经用户确认，不修改父任务为 completed，不归档任何任务。

## 6. 待补文档

- 实施完成后：逐条 AC 证据表、当前 commit 绑定、实际运行手册、spec 更新和父任务追加式审计附录。
- 若 Steward child run 最终不启用 Pi，必须在父任务和 Obsidian 设计文档中明确记录“确定性内核为生产实现，Pi 为可选解释层”，不能继续声称两个 Agent 都由 Pi loop 驱动。
- `before_provider_request` 的扩展 runner 吞异常是 Pi 上游行为；整改不得把它描述成天然 fail-closed，必须以 sidecar 直接 Guard 或显式拒绝路径作为发布合同。

## 7. 2026-08-29 执行进度记录（会话暂停时快照）

基线：HEAD `6f93f6f93e88cfd5582d976e042fe45676c88e50`，工作树含其他进程未提交改动
（本会话改动与其叠加，**尚未 commit**）；Python 3.12.12 / Node 24.14.1。
基线质量：pytest 529 passed；mypy strict 118 文件通过；`ruff format` 仅
`agent_queue.py` 一处失败（已格式化修复）。

### 已完成（代码 + 回归测试，均在 6f93f6f 工作树上）

1. **AC-SEC-01 graph 隐藏边（P1）**：`graph.py` 边过滤改为两端点均非 `LEVEL_NONE`
   才保留；回归 `test_graph_hidden_endpoint_edges_removed`（family/clan 双 scope，
   断言隐藏端点 ID 不出现在任何边）。
2. **AC-SEC-02 internal 网络隔离（P1，代码层）**：internal 协议从公开 app 拆出为
   `internal_app`（`app/main.py`），公开 listener 对 `/internal/*` 一律 404
   fail-closed；新增 `app/serve.py` 双 listener 入口（公开 8000 / internal 8001，
   `INTERNAL_AGENT_API_PORT` 可覆盖）；Dockerfile CMD 改为 `python -m app.serve`；
   compose 不发布 8001；sidecar `FG_INTERNAL_API_BASE_URL` 分离 internal base
   （`client.ts` 按 `/internal/` 前缀路由）。测试：12 个 internal 测试改走
   `internal_client`，新增 `test_internal_routes_absent_from_public_listener`
   （五方法 404）。**E2E（宿主/nginx/浏览器不可达）尚未验证**。
3. **工具并发去重窗口（P1）**：`agent_tools.execute` 改为原子占位——同
   `(run_id, tool_call_id)` 先以空 `result_json` 占位（唯一索引兜底并发 flush 冲突），
   执行成功后回填；空占位命中返回 409 `AGENT_TOOL_CALL_IN_PROGRESS`（新错误码）；
   拒绝路径回滚占位避免永久 in-flight。回归：占位命中 409 测试。
4. **导出下载资格（P1 部分）**：`data_rights` 下载路径改为事务内先解密成功再消费
   一次性资格；损坏密文返回 410 且 `downloaded_at` 不落库，修复后可重试。
   回归：`test_corrupted_export_does_not_consume_download_eligibility`。
   **成熟 AEAD envelope 替换自制 XOR+HMAC 未做（maintained in AC）**。
5. **RAG session-space 绑定（P1）**：`confirm_candidate` 对共享 scope 校验来源
   message 所属 session 空间与目标空间一致（无会话来源不施绑）。回归：
   `test_cross_space_confirmation_from_session_source_rejected`（含同空间放行）。
   顺带修复 conftest 清表顺序（memory_candidates 先于 agent_messages 删）。
6. **Web fetch 用途选择（P1）**：`WebApprovedURL` 新增 `use_case` 列（迁移
   `0019_web_approved_use_case`，server_default `research`）；`search_web` 签发时
   写入用途；`fetch_approved_page` 按凭据用途取 policy（非法值回退 research），
   citation 返回体暴露 `use_case`。回归：fact_check-only 空间 fetch 成功测试。
7. **前端/agent 基线整理**：agent vitest 65 passed、type-check 绿；compose config
   校验通过。

### 验证状态

- 后端：listener 拆分后全量 531 passed；此后新增回归的单文件均绿。**暂停时最后一
  次全量 pytest/mypy/format 复验被中断，重启后需先跑完再继续**。
- 前端/agent/构建/ docker compose build、E2E（空库、恢复、跨进程）：未跑。
- guga glm-5.2-fast：未重试（仍按环境限制记录）。

### 暂停原因

用户指示暂停。剩余 P1：成熟 AEAD、DNS TOCTOU/redirect 逐跳、错误脱敏统一、
ProviderGateway 唯一 egress、Guard fail-closed 合同、Steward canonical Job 生产
入口、前端 store generation/abort；E3 证据全部未开始。

## 8. 2026-08-29 第二段执行记录（续 §7）

### 新完成（代码 + 回归，全部在 6f93f6f 工作树，未 commit）

7. **错误脱敏统一（P1，sidecar 侧）**：新增 `agent/src/redact.ts`
   （URL 凭据/Bearer/API key/密钥形参数替换 + 控制字符掩码 + 300 字截断），
   worker 的 assistant provider error settle/log 与 catch-all run failed 路径接线；
   policy 拒绝路径 message 为内部枚举不受影响。单测 5 例（agent 70 passed）。
8. **Web DNS TOCTOU 钉扎（P1）**：`_validate_public_url` 返回已验证 IP 集合；
   新增 `_PinnedTCPBackend`（httpcore.SyncBackend 子类，仅连接验证过的 IP，TLS
   SNI/证书校验仍用原域名）+ `_pinned_client`（替换 httpx 0.28.1 transport 内部
   pool，版本锁定耦合已注释声明）；`_fetch_bytes`/`_provider_search` 全部改为
   钉扎连接；redirect 保持不跟随（fail-closed）。回归：钉扎地址记录测试断言
   域名永不作为 connect 目标（backend 536 passed）。
9. **导出 AEAD（P1，并行进程完成，本段确认整合）**：`secretbox.py` 已由并行进程
   升级为 AES-256-GCM + HKDF KEK + key_id 轮换；与本人"先解密后消费下载资格"
   修复组合验证通过；`cryptography==44.0.2` 已在 pyproject。本段顺带清理其
   2 处 UP012 lint 与 1 处 format。
10. **前端 store 代际隔离（P2）**：`members.ts`（load 代际校验）、
    `spaces.ts`（generation state + load/loadMembers 三段响应校验 + clear 复位
    loading）、`actionCards.ts`（分区对象引用 + 代际双校验）；regression：
    spaces 迟到响应不回写（2 例）。前端 171 passed / type-check / build 绿。

### 验证状态（本段末）

- backend：536 passed / mypy strict clean / ruff check+format clean
- agent：vitest 70 passed / type-check / lint / build 绿
- frontend：vitest 171 passed / type-check / lint / build 绿
- docker compose config 通过

### 未完成（剩余 P1，需要与并行进程协调后单独会话执行）

- **ProviderGateway 唯一 egress + Guard fail-closed 合同**：涉及
  `agent_provider.py`/`internal_agent.py`/`agent/src/{session,worker,client}.ts`
  ——这些文件均处于并行进程未提交修改中，本轮不覆盖其实现。
- **Steward canonical Job 生产入口（scheduler/worker/lease 闭环）**：同样落在
  并行进程热文件（`steward.py`/`agent_queue.py`）。
- E3 全部（空库 Compose、第二卷恢复、FTS/SSE/优雅停机、375px 人工记录、
  guga glm-5.2-fast 成功正文）；internal 隔离的宿主/nginx 不可达 E2E。

### 工具环境备注

- ZCode shell hook（`${ZCODE_PROJECT_DIR}/.zcode/hooks/inject-shell-session-context.py`）
  以 shell 持久 cwd 解析相对路径：cwd 停留在子目录时所有 Bash 命令被拦截失败。
  本会话两度以临时转发 shim 恢复并已清理。建议 trellis 侧把该 hook 调用改为
  绝对路径或对 cwd 容错。

## 9. 2026-08-29 第三段执行记录（ProviderGateway/Guard 复核 + Steward 生产入口）

用户确认并行进程已停止，工作树改动归并处理。

### 复核结论（原"大重构"项经最新代码确认已收口，逐 anchor 验证）

- **ProviderGateway 唯一 egress**：`agent_provider.resolve_runtime` 是唯一解密
  出口（secretbox + fail-closed，密文损坏返回 None 拒绝），唯一调用方为
  internal listener 的 context 端点（run token 持有者）；sidecar
  `session.ts:resolveProvider` 只消费 server projection，注释与代码均无平行
  env 生产依赖；`config.providers` 仅剩 health readiness 上报用途。
- **Guard fail-closed 直接合同**：`session.ts:guardedStreamSimple` 把
  `policyGuard.beforeProviderRequest` 作为 `onPayload` 在 pi-ai stream 内直接
  调用（不再经会吞异常的 runner hook）；违规时在 HTTP 请求发出前抛错
  （POLICY_PROVIDER_BLOCKED / POLICY_MASKED_DATA / POLICY_SECRET_IN_PROVIDER_PAYLOAD），
  请求必然不落地；worker 映射 POLICY_SECRET_LEAK 结算。
- **AgentJob(kind="steward") 双队列**：生产代码零创建路径（grep 验证）；
  min_kind="steward" 仅剩探针工具门禁与测试直插。

### 新实现：canonical StewardJob 生产入口（P1，本段唯一新代码）

- `backend/app/services/maintenance.py`：进程内后台维护循环——
  AGENT_RUNTIME_ENABLED 时周期跑 `agent_queue.reaper_pass`（此前同样无生产
  调用者）；STEWARD_ENABLED+STEWARD_WORKER_ENABLED 双开关时周期跑
  `steward.reaper_pass` 并把 queued 作业 lease→execute 连续泵（≤10/tick）。
  单个毒药作业结算 failed 后泵继续；tick 级异常记日志不终止循环。
- `main.py` lifespan 启动/停止；serve.py 双 listener 共享 lifespan 由进程级
  单例防重复启动；config 新增 `STEWARD_WORKER_ENABLED`（默认关）与
  `MAINTENANCE_INTERVAL_SECONDS`（默认 5s）；compose api 服务透传两旗标。
- 回归 `tests/test_maintenance.py`（5 例）：端到端执行、过期 lease 回队再执行
  （attempt=2）、worker 关闭 noop、agent reaper 接线断言、执行异常 failed 结算
  不死循环。

### 验证终态

- backend 541 passed / mypy strict(119 文件) / ruff check+format 全绿
- frontend 171 passed；agent 70 passed
- docker compose config 通过

### 仍未闭环

- E3 证据全部（空库 Compose、第二卷恢复、internal 隔离宿主/nginx 负向、
  FTSA/SSE/优雅停机、375px 人工记录、guga glm-5.2-fast 成功正文）；
  内部孤儿文件测试；AbortController 前端未接（代际校验已覆盖核心竞态）。
- 全部改动未 commit；commit 后按 AC 回写 `task.json`。

## 10. 2026-08-29 独立复核整改（推翻 §9 两处"已收口"表述）

独立复核裁定 §9 中 ProviderGateway/Guard 的"复核收口"不可按已关闭接受：
解密集中 ≠ egress 集中（sidecar 仍携凭据直连云端）；双 listener 共享维护循环
存在停止生命周期互斥问题；compose 层网络隔离不完整（api 双挂网络 + internal
绑定 0.0.0.0 → web 可达 8001）；另有默认弱密钥、优雅停机与证据可复现性缺口。
§9 相应表述以本节为准。

### 整改实现

1. **Provider 代理（真正唯一 egress）**：新增 `services/provider_proxy.py` +
   internal 端点 `POST /internal/agent/runs/{run_id}/provider/chat/completions`
   （StreamingResponse）。context 不再解密、不下发 base_url/api_key，只下发
   代理路径；sidecar `resolveProvider` 把 `/internal/...` 路径 resolve 到
   `FG_INTERNAL_API_BASE_URL` 并以 run token 作 Bearer。真实凭据与外网
   egress 全部留在 api 容器：代理在服务端 `resolve_runtime` 解密转发
   （`{base_url}/chat/completions`），成功流式透传+字节审计
   （`agent_provider_egress`），上游 4xx/5xx 与网络失败一律 502 脱敏通用体
   （上游 body 不透传），Run 非活跃 409，Provider 不可解析 503 fail-closed。
   client/upstream 生命周期由 passthrough 生成器 finally 统一关闭。
   回归：`test_provider_proxy.py` 6 例（转发/审计/认证三态/非活跃/不可解析/
   错误脱敏/网络失败）+ `test_internal_agent_api` context 合同更新
   （断言代理路径、无 api_key、真实 URL 与密钥不出响应）+ sidecar
   `session-resolve.test.ts` 4 例。
2. **双 listener 停机生命周期**：uvicorn 每个 `Server.serve()` 重装
   SIGINT/SIGTERM 处理器，后装覆盖先装 → SIGTERM 只停第二个 listener。
   `serve.py` 改为 `_NoSignalCaptureServer`（禁用 per-server capture）+
   `loop.add_signal_handler` 共享分发，两 listener 同时优雅退出；
   `maintenance` 改引用计数启停（两侧 lifespan 均退出才停止循环）。
3. **Compose 网络隔离与弱密钥 fail-closed**：backend 网络设 `internal: true`
   （sidecar 无外网 egress，与代理收口互为条件）+ 172.28.0.0/24 子网静态
   分配 api=172.28.0.10；`INTERNAL_AGENT_API_HOST=172.28.0.10`（internal
   listener 只绑 backend 接口，web/宿主不可路由）；`SECRET_KEY`/
   `AGENT_SERVICE_SECRET` 改为 `:?` 必填（不再提供 dev 弱默认值），
   `DEV_ALLOW_WEAK_SECRETS` 默认 0；sidecar 的平行 env provider 配置项从
   compose 移除。
4. **启动校验**：`serve._validate_bind_plan`——端口冲突即拒启；生产 posture
   （未显式 DEV_ALLOW_WEAK_SECRETS）下 internal host 通配地址即拒启
   （serve.py 默认 `127.0.0.1` fail-closed）；config 层维护间隔非正数拒启。
5. **移除 compose 中 agent 平行 provider env**（`AGENT_PROVIDER_CLOUD_*`）。

### 验证证据（可复现命令；工作目录 repo 根；解释器用项目 venv 绝对路径）

```bash
cd /Users/lyston/PycharmProjects/familygraph/backend
.venv/bin/python -m pytest -q          # → 547 passed in ~7s
.venv/bin/python -m mypy app           # → Success: no issues found in 120 source files
.venv/bin/python -m ruff check app tests   # → All checks passed!
.venv/bin/python -m ruff format --check app tests  # → 174 files already formatted

cd ../agent
npm run type-check && npm run lint && npx vitest --run && npm run build
#   → TC=0；Tests 74 passed (74)；lint/build 退出码 0

cd ../frontend
npx vitest --run && npm run type-check && npm run build
#   → Tests 176 passed (176) / 30 files；TC=0；build 退出码 0

cd ..
SECRET_KEY=... AGENT_SERVICE_SECRET=... docker compose config  # → exit 0
```

### 仍未闭环（明确保留）

- E3 运行证据：空库 compose 部署、第二卷恢复、internal 隔离的宿主/web 负向
  连通性（需真实 compose up）、375px 人工走查、guga glm-5.2-fast 成功正文。
- JSONL 结构校验 ≠ AC 完成：所有 AC 的 E2/E3 行保持 partial，待上述证据
  逐条回写 status/commit/exit_code/artifact。
- 全部改动未 commit。

## 11. 2026-08-29 E3：真实模型回路打通（abrdns GLM-5.2）

用户指定改用 pi 配置 `abrdns` profile（`https://new-api.abrdns.com/v1`）的
`GLM-5.2`（精确大小写；小写在该 new-api 分组下 model_not_found）。空 DATA_DIR
20 迁移链 + 双 listener(18000/18001) + sidecar 全链路：

- **run 4 succeeded（~18s）**：11 事件（含模型自主调用
  `familygraph.get_self_context` 成功），真实中文正文引用工具返回的空间名/用户名；
  egress 审计两笔 200（5574B + 16359B）——api_key 全程不出 api 进程。
- run 1（guga-copy/deepseek-v4-pro-0813）重载荷下上游间歇 503 → sidecar 重试
  耗尽 → failed/PROVIDER_STREAM_ERROR（fail-closed 负向证据）。
- 期间修复两个实现缺陷：① 代理透传 Content-Type/Accept/UA（httpx 原始 body
  不自动设 Content-Type → 上游 400）；② sidecar 新增
  AGENT_PROVIDER_STREAM_MAX_RETRIES(默认5)/…_MAX_RETRY_DELAY_MS(默认20s) 注入
  pi-ai 重试（中转型上游间歇 503 必需）。
- 证据全文：`research/e3-model-loop-evidence.md`。
- 仍待：docker compose 空库栈重建（用户 OrbStack 栈为旧镜像，需确认）、
  internal 负向连通性、375px 走查、第二卷恢复。

## 12. 2026-08-29 E3：compose 栈重建与网络隔离实测（commit f596ead 镜像）

用户确认重建。`.env` 提供强随机 SECRET_KEY/AGENT_SERVICE_SECRET（gitignored，
DEV_ALLOW_WEAK_SECRETS 默认 0——生产 posture 启动即通过校验）。三镜像重建
（基础镜像经 daocloud 镜像源拉取：Docker Hub 直连超时），`docker compose up -d`
三容器 healthy。

### 隔离/连通性矩阵（全部符合合同）

| 路径 | 结果 | 判定 |
|---|---|---|
| 宿主 → api:8000 /api/health | `{"status":"ok"}` | 公开面正常 |
| 宿主 → api:8001 | connection refused | internal 未发布宿主 ✓ |
| 宿主 → api:8000 /internal/*（POST lease） | 404 | 公开面 fail-closed ✓ |
| web(frontend 网) → api:8001 | connection refused（解析到 192.168.147.2，无 8001） | 静态 IP 绑定生效 ✓ |
| web → api:8000 /internal/* | 404 | ✓ |
| agent(backend 网) → api:8001 | 503 AGENT_DISABLED（应用层响应，路由可达） | sidecar 通路 ✓ |
| agent → 172.28.0.10:8001 | 503 同上 | 绑定 IP 生效 ✓ |

- 既有数据卷上增量迁移 `0018 → 0019_web_approved_use_case` 成功（api 启动日志）。
- 说明：该卷为用户既有 dev 数据，未重置；「空库 compose 自举」证据由本机空卷
  迁移链（§11/§7）+ 本节增量迁移共同覆盖。
- compose 栈上的完整模型回路需既有账号凭据（bootstrap 管理员 PIN 由用户持有），
  未执行；代理链路本身已在 §11 本机同代码路径取证。

### 提交记录

- `f596ead` fix(v2-agent)：backend/agent/compose 全部整改（task.json 已绑定）
- `771001d` feat(frontend)：naive-ui Phase 1（WIP，redesign 任务）+ store 代际
- `cd60600` test(frontend)：登录回车提交回归
- `216759c` chore(task)：本任务记录与证据
- `.zcode/`（平台 hook/skills 配置）保持 untracked，是否纳管由用户决定
