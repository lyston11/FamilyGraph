# Journal - lyston (Part 1)

> AI development session journal
> Started: 2026-08-25

---



## Session 1: v1 全量交付：M0-M4 十六子任务完成+全项目复审

**Date**: 2026-08-26
**Task**: v1 全量交付：M0-M4 十六子任务完成+全项目复审
**Branch**: `main`

### Summary

FamilyGraph v1 功能开发完毕：16 子任务全部实现归档；全项目复审修复分层/异步阻塞等 5 项；M2/M3 专项重验 ALL PASS；终态门禁后端 118 测试+mypy strict 全绿、前端四门禁全绿、docker e2e 通过。

### Main Changes

## 会话成果

**v1 全量交付**：M0-M4 共 16 个业务子任务全部实现、验证并归档，另完成全项目代码复审。

### 里程碑
- **M0**：FastAPI+Vue3 骨架与部署（m0a）；名字+PIN 认证安全基座——限流锁定、challenge 落库防重放、refresh 轮换+重用检测、首启管理员引导（m0b）
- **M1**：档案建档向导+一次性 PIN+ClaimState 认领+custody 代管权矩阵（m1a）；四分类关系 FSM+世代一致性校验+合并请求（m1b）；家庭空间成员 FSM+幂等邀请（m1c）；Vue Flow 三布局画布+lunar 公农历互补（m1d）
- **M2**：visibility.py 授权单点+IDOR 矩阵测试（m2a）；家族连通视图+摘要卡（m2b）；join-by-user 申请流+断连即时降级（m2c）
- **M3**：附件安全校验链+相册+孤儿清扫（m3a）；历别切换自动互填（m3b）；可见性范围统计页（m3c）；全局搜索+画布筛选（m3d）
- **M4**：响应式+a11y 基线（m4a）；管理员后台+审计时间线（m4b）；online backup 演练+README 运维指南+回归清单（m4c）

### 全项目复审（用户要求专项）
修复 5 项：前端视图直连 axios 分层违规（8 处下沉 api 层）、async 路由阻塞 PIL 重编码、conftest _TABLES 缺 attachments、architecture 限流措辞对齐实现、目录文档刷新。登记 HANDOFF Q8（删除空间 owner 级联策略，v2 引导流）。

### M2/M3 复审验收（用户要求专项）
IDOR 矩阵 5 用例 + 申请流 3 用例 + 附件链 5 用例 + 端到端旅程 9 步 ALL PASS。

### 终态门禁
后端 ruff/format/mypy strict(52 文件) + pytest **118 passed**；前端 lint/type-check/vitest/build 全绿；docker compose e2e 复验通过。共 40 commits。

### 遗留
手机视口人工走查；Element Plus 按需引入；HANDOFF Q8（v2）。


### Git Commits

| Hash | Message |
|------|---------|
| `4444f7d` | (see git log) |
| `eb506c3` | (see git log) |
| `a0b8c61` | (see git log) |
| `7e28a9d` | (see git log) |
| `e0bb9ff` | (see git log) |
| `28be7a9` | (see git log) |

### Testing

- [OK] backend: pytest 118 passed (ruff/mypy strict clean); frontend: vitest 9 files + build clean; docker compose e2e verified

### Status

[OK] **Completed**

## Session 6: V2 Agent Runtime final gate recheck

**Date**: 2026-08-30
**Task**: V2 Agent Runtime Full Repair
**Branch**: `feat/frontend-role-boundaries`

### Summary

重新执行并复核当前工作树：Backend `pytest -q` **587 passed**，Agent **83 tests / 12
files**，Frontend（保留并行 redesign 修改）**249 tests / 40 files**；三端
type-check/lint/build、Compose config、`git diff --check` 与 Trellis validate 全部
通过。Agent 集成测试因需要本地 loopback 监听，受限沙箱的 EPERM 已在获准权限下
复跑通过。

复核期间修正 `test_internal_agent_api.py` 的错位断言，并将 sidecar ContextOut
归一化改为核心字段、消息、context block 全部严格 fail-closed，畸形投影统一为
`invalid_context_projection`，不得启动 Pi 或发起 Provider/工具请求；合同已同步到
`.trellis/spec/backend/agent-runtime.md` §9。
同时修正 heartbeat 401/403/409/410 未触发中止的竞态，授权撤销现在立即 abort
Pi stream 且跳过 settle。
Allowed runtime snapshot 另补非空 `provider_revision` 校验，配置/密钥轮换无法被
缺 revision 的快照绕过。

### Status

[WIP] **本地质量门禁全绿；真实 liu-dada/gpt-5.6-sol 成功正文回显仍待上游恢复，任务保持 in_progress**

## Session 5: V2 Agent Runtime 全面复核与修复

**Date**: 2026-08-30
**Task**: 08-30-v2-agent-runtime-full-repair（继续 in_progress）

复核确认 Assistant 运行时由 `pi-coding-agent` 承载 session/agent loop，模型协议由
`pi-ai` 的 `openai-responses` adapter 承载，不存在独立 pi-sdk。云 Provider 固定为
`liu-dada/gpt-5.6-sol`（`https://api.liu-dada.com/v1`，reasoning、text+image、
272000/60000、五级 thinking）。

本轮修复：denied/no-provider snapshot 不可复活与严格类型校验；Provider/工具
dispatch 的数据库 CAS cancellation fence；凭据 header 变体阻断；事件 flusher
单 pump 保序重试；移除完整 transcript 不再使用的消息上限配置；Compose 不能关闭
Provider profile 门禁。未读取或写入任何密钥、run token、密文。

门禁：backend 571 pytest、ruff/format/mypy；agent 80 tests、type-check/lint/build；
frontend 242 tests、type-check/lint/build；Compose config、Trellis validate、diff
check 全部通过。唯一未闭环是外部 liu-dada 成功正文证据，因此任务保持 in_progress。

## 2026-08-30 会话：V2 Agent Runtime Full Repair 最终核验与取消竞态加固

任务：`08-30-v2-agent-runtime-full-repair`（保持 `in_progress`，未归档）。工作树含其他进程的 frontend redesign，未执行 reset/checkout。

### 本次核验与修复

- 确认运行时是 `pi-coding-agent` session/loop + `pi-ai` protocol adapter（无独立 pi-sdk）；首版云模型严格使用 `liu-dada/gpt-5.6-sol` / `openai-responses`，不使用 guga 或 luna 作为运行配置。
- Provider profile、provider_name/id 语义、runtime snapshot、ProviderGateway 唯一 egress、credential-key policy guard、空/非法 body fail-closed、Responses wire、sidecar cancellation 均已落地。
- 新增后端取消竞态门禁：`agent_tools.execute` 在分发前复核 `cancel_requested`；Provider proxy 在建立上游连接前及流式 chunk 边界复核 Run 状态/取消标记；新增回归测试。
- Provider 上游连接异常路径显式关闭 `httpx.AsyncClient`，避免连接池泄漏。
- Provider proxy 额外绑定 run snapshot 的 model/stream/token cap，防止持有效 token 的 sidecar 改模型或绕过输出上限。
- `.env` 权限保持 `0600`；不读取、不输出、不记录密钥。

### 门禁证据

- backend `pytest -q`：560 passed；ruff check/format、mypy（120 files）通过。
- agent type-check/lint/build 通过；Vitest 12 files / 78 tests passed。
- frontend（包含其他进程 dirty redesign）type-check/lint/build 通过；Vitest 37 files / 233 tests passed。
- `docker compose config --quiet` 通过；`task.py validate 08-30-v2-agent-runtime-full-repair` 通过（implement 8/8、check 5/5）。

### 未闭环

- 真实 `liu-dada/gpt-5.6-sol` 成功正文回显尚未取得；仅有本地 Responses wire stub 与失败/取消回归证据。上游恢复后补脱敏 E2E（provider/model/status/字节数/正文长度，不含 key/token）。
- Session history 按公开合同恢复 user/assistant 文本与 citation；Pi 私有 tool call/result 不写 AgentMessage。若需恢复工具结果，另立任务设计受控摘要与敏感级别。

状态：**代码与本地门禁通过；真实 provider success evidence 待补，任务保持 in_progress。**

### Next Steps

- v1 已可发布使用；待办：手机视口人工走查、迁云按 README 清单执行；v2 计划见 HANDOFF（agent 推荐/互反称谓/Q8 等）

---

## 2026-08-26 · V2.1 Agent Runtime（Pi Sidecar 与安全工具协议）

### 交付
- B1 后端核心：agent 五表+Provider 配置（迁移 0009）、durable queue、两级 token、工具协议注册表、internal 六端点
- B2 agent/ sidecar：Pi SDK 0.84.3 集成（noTools:"all"+allowlist 测试工具）、policy guard、worker 循环、health、Dockerfile
- B3 浏览器面：Session/Message(Idempotency)/Run/SSE(Last-Event-ID)、feature flag 默认关、Provider 治理端点+策略矩阵
- B4 合同对齐：compose 真实联调抓出三处漂移（lease 形状/token typ/settle 字段）并修复

### 终态门禁
backend pytest **271 passed** + ruff/mypy strict 绿；agent type-check/lint/vitest(32)/build 全绿；
docker compose config + 全栈 up 三服务 healthy；E2E：入队→租约→context→PROVIDER_UNRESOLVED 可解释失败→SSE 重放与 Last-Event-ID 续传→幂等重放同 Run。
trellis-check PASS（7 非阻塞项，#1/#2 当场修复，其余移交 V2.2/V2.4，见任务 notes）。

### 教训（已入 spec/backend/agent-runtime.md §6）
双侧各自 mock 自测不能证明合同；internal 协议任务验收必须含 compose 实联。共享字面量一侧定义常量、另一侧逐字断言。

### Next Steps
归档 V2.1 → 启动 V2.2 只读 Assistant（问答/关系解释/悬浮 UI），为其注册 Foundation 只读 scoped tools。

---

## 2026-08-26 · V2.2 只读 Assistant（问答/关系解释/全局悬浮 UI）

### 交付（三并行块）
- C1 后端：AgentQueryService + 六个 familygraph.*@1 只读工具（purpose=agent 投影、防枚举同码、BFS 剪枝、8KB 截断、23 表零写入快照测试）
- C2 sidecar：九工具声明接线、ASSISTANT_SYSTEM_PROMPT（事实三态/唯一真源/只读边界/对抗拒绝）、allowlist 放行单测
- C3 前端：api/store(space_id 分区)/useAgentStream(fetch-SSE+Last-Event-ID+refresh 重连)/八组件（Launcher/Panel 双容器/ScopeBanner/工具 chip/ErrorNotice）/clearSession 联动

### 终态门禁
backend **281 passed** + mypy strict 83 文件；agent vitest **44**；frontend vitest **112 (21 files)**；三端 lint/type-check/build 全绿。
compose E2E：入队→租约→context→无真实 key 可解释失败 PROVIDER_UNRESOLVED（预期）。trellis-check PASS（3 项非阻塞，#1/#2 文案映射当场修，#3 移交 V2.3）。

### 联调修复
cursor string vs integer 双侧漂移一处（sidecar 对齐后端 integer）——再次验证 spec §6「双侧各自实现必漂移」教训，本次在派发前预先逐字段核对才抓出。

### Next Steps
归档 V2.2 → V2.3 Relationship Intelligence（确定性关系推理与称谓系统）。


## Session 2: Completed V2.4 Steward and ActionCard

**Date**: 2026-08-27
**Task**: Completed V2.4 Steward and ActionCard
**Branch**: `main`

### Summary

Delivered space-scoped Steward jobs, DomainEvent-triggered scheduling, deterministic recommendation matrix, ActionCard FSM with dedupe/cooldown/supersede, revalidated execute commands, and shared Inbox/Assistant rendering. Added cross-space, evidence, membership, target-space, disclosure, idempotency, and two-step confirmation tests. Updated backend/frontend code-specs and passed 445 backend tests plus 159 frontend tests with mypy/ruff/type-check/lint/build.

### Git Commits

| Hash | Message |
|------|---------|
| `6b63aee` | (see git log) |

### Status

[OK] **Completed**

## Session 3: V2.6 Controlled Web — regression repair, disclosure fix, PII minimization

**Date**: 2026-08-27
**Task**: 08-26-v2-6-controlled-web (in_progress)
**Branch**: `main`

### Summary

Resumed V2.6 work and found the working tree had 4 regressions plus an undisclosable tool bug over an otherwise complete controlled-web implementation (models/schemas/service/api/migration):

- Restored `config.POLICY_GUARD_ENABLED` (V2.5) that the V2.6 config block had overwritten — fixed 19 failing tests.
- Restored `memory_router` mount in `main.py` that the controlled-web router insertion had deleted.
- Restored `TOOL_RECORD_TERM_USAGE` ToolSpec in `agent_tools.py` that the web tool insertion had replaced.
- Removed duplicate `backend/alembic/` tree (alembic.ini `script_location=migrations`).
- Fixed tool disclosure bug: `default_allowlist` now excludes web tools from the static traversal and only adds them when `agent_tools_enabled` returns True (AC-W1 — disabled tools must never be advertised).
- Implemented WEB-3 query PII/secret minimization (`_sanitize_query`, `WEB_QUERY_BLOCKED`): resident ID / phone / email / secret tokens / opaque hex / masked placeholders / clustered CJK address tokens are fail-closed before quota and egress.
- Added 39 targeted tests covering default-off, dual opt-in, SSRF (loopback/RFC1918/metadata/port/credentials), allowlist filtering, one-use/expiry/cross-account tokens, quota/budget, usage never stores raw query, PII/secret rejection, and tool disclosure mirroring policy.
- Wrote `.trellis/spec/backend/controlled-web.md` and registered it in the backend spec index.

Backend: 497 tests pass, mypy clean. Frontend: type-check/lint/168 tests pass (no web integration yet — separate work surface).

### Status

[OK] **In progress**

## Session 4: V2.6 Controlled Web — sidecar alignment, citation pipeline, deploy hardening

**Date**: 2026-08-27
**Task**: 08-26-v2-6-controlled-web (in_progress)
**Branch**: `main`

### Summary

Closed the remaining V2.6 gaps across all three layers and verified an empty-volume Compose E2E:

- Registered `search_web`/`fetch_approved_page` in the agent sidecar `tools.ts` (same names/schema as backend) — without this the sidecar's fail-closed allowlist check would reject any run once the backend discloses web tools.
- Built the web-citation pipeline: sidecar `RunEventBuffer` extracts `trust=external` citations from `fetch_approved_page` results and attaches them to the next `message.assistant_added` event's `web_citations`; frontend store parses them and `WebCitationList.vue` renders external sources distinctly from local Memory citations (AC-W4).
- Extended `app/backup.py` `verify_restore` to cover V2 source tables (agent/memory/rag/action-card/source-fact/domain-event) plus FTS-vs-projection self-consistency (WEB-6).
- Hardened `docker-compose.yml`: `CONTROLLED_WEB_ENABLED` default 0, `stop_grace_period` on api/agent.
- Fixed a production-blocking bug found by the E2E: `httpx` was dev-only but `controlled_web.py` imports it in production — moved it to main dependencies in `pyproject.toml`.
- Empty-volume Compose E2E: all three images build; api/web/agent healthy; migration reaches 0015; web tables created; `python -m app.backup` succeeds with V2+FTS counts; agent restart recovers healthy; `/internal/*` returns 404 through nginx; web admin endpoint 401 unauthenticated; `CONTROLLED_WEB_ENABLED=False` by default.

Backend 497 tests + mypy clean; agent type-check/lint/64 tests/build; frontend type-check/lint/169 tests/build.

### Status

[OK] **Completed**

## Session 5: V2.6 E2E verification, operations runbook, full v2 archive

**Date**: 2026-08-27
**Task**: 08-26-v2-6-controlled-web -> archived; 08-26-v2-agent-system -> archived
**Branch**: `main`

### Summary

Closed the final V2.6 gaps and archived the entire v2 Agent system:

- AC-W2 gap: `_fetch_bytes` accepted any content-type — added `_ensure_text_content_type` (text/* + xhtml/xml/json allowlist; missing fails closed) rejecting PDF/image/binary with `WEB_FETCH_UNSUPPORTED_TYPE`.
- AC-W7: provider-outage test asserting httpx failures surface as `WEB_PROVIDER_UNAVAILABLE`.
- Empty-volume Compose E2E with real guga Provider (glm-5.2-fast): bootstrap admin, register AgentProvider, create space, dual-layer web config, real public fetch (httpbin.org/html) through DNS/IP + content-type + HTML cleaning + citation record, one-use token CAS rejection, SSRF real rejection (private/metadata/loopback), PII real rejection (phone/secret), backup/restore with V2 tables + FTS self-consistency.
- Operations runbook in README: feature flags & kill switch, health, graceful shutdown, run lease recovery, log redaction, event retention/compression, Provider secret rotation (LLM + web), backup/restore.
- Archived v2-6 (7/7 children done) and the v2-agent-system parent (AC-P1..AC-P10 all checked).

Final gates: backend 501 tests + mypy clean; agent 64 tests; frontend 169 tests.

### Status

[OK] **Completed — all v2 tasks archived**

---

## Session: 08-28-v2-audit-remediation 整改执行

**Date**: 2026-08-28
**Task**: 08-28-v2-audit-remediation (in_progress)
**Branch**: `main`（未提交；Pi implement 禁 commit）

### 本轮完成块

- **G0/G1 工件**：`task.py validate` 当前任务 + 归档父任务 `08-26-v2-agent-system` 全通过（JSONL 14/5 条目，无 stale 引用）。
- **F1 原子建档** `create_managed_member` + 幂等台账 `member_creation_requests`（migration 0016）+ Wizard 单次提交。
- **F2/F3 VisibilityPolicy 收口**：stats/custody/operator 出口改字段投影；operator 用户列表删除家庭 PII（name/gender/birth/privacy_mode）。
- **F4 导出**：envelope 加密（secretbox `encrypt_envelope`）+ 一次性下载台账 `downloaded_at`（migration 0017）。
- **R1 ProviderGateway**：`resolve_runtime` 服务端解密 base_url+api_key 经 internal context 注入 sidecar；sidecar 以投影为唯一真源（删除 `resolveProvider(config, ...)` 的 env 旁路）。
- **R2 弱密钥防线**：`config._reject_weak_default_secrets`；生产拒启弱默认，开发需 `DEV_ALLOW_WEAK_SECRETS=1`。
- **R3 schema 合同**：新增 `test_agent_schema_contract.py`（7 条）锁定 internal wire 契约（extra=forbid + 字段/约束）。
- **R4 事件/审计**：`agent_tool_calls` 副作用去重（migration 0018，`(run_id, tool_call_id)` 幂等/409）；审计 actor 语义修正为 users.id + account/session 入 detail；`run.expired` 注册并同步 backend/sidecar/frontend 三份事件枚举；reaper 写终态事件；sidecar 移除重复 `message.user_added` 与重复终态事件（backend 唯一拥有）。
- **回归**：SSE/browser/steward/relationship/source-fact/memory-rag/controlled-web 定向 166 passed。
- **发布门禁**：
  - backend: 528 passed + mypy clean + ruff clean
  - agent: 64 passed + type-check/lint/build clean
  - frontend: 169 passed + type-check/lint/build clean
  - 全新卷 `alembic upgrade head`（18 步全迁移）干净
  - `docker compose build` 三镜像成功；`up -d` api/agent/web healthy
  - 网络边界：nginx `/internal/` → 404；直连 api:8000 `/internal/agent` → 503（默认关）
  - `app.backup` 在线备份 + integrity/计数/FTS 校验通过

### 残余（未完成，需用户确认/环境）

1. 真实 openai-compatible stub 容器走通完整模型回路（lease→context→pi→tool→settle）——repo 无 stub，属 E3 联调。
2. 375×812 与桌面人工 UI 走查 + 合成数据截图（需真实浏览器）。
3. 合成数据非空库的 backup→restore→重启回放（本轮验证了空库恢复自洽）。

### Status

[WIP] **实现与门禁全绿；E3 人工/真实联调三项残余，未提交未归档**

---

## 2026-08-29 会话：V2 Agent 架构收口（08-29 子任务）执行中暂停

**任务**：`08-29-v2-agent-architecture-release-closure`（P0，parent `08-28-v2-audit-remediation`）。
本轮用户批准 start（degraded 模式，无 session identity），从 planning 进入 in_progress。
基线 HEAD `6f93f6f`，工作树与其他进程未提交改动叠加，**本轮改动未 commit**。

### 本轮落地（全部带回归测试，backend 531 passed @ 拆分后）

- **P1 graph 隐藏边**：边过滤改两端点可见性判定（`graph.py`），隐藏端点 ID/label/creator 不再随边泄露。
- **P1 internal 网络隔离**：`/internal/agent/*` 从公开 app 拆出 `internal_app`，公开 listener `/internal/*` 一律 404；新增 `app/serve.py` 双 listener（8000 公开 / 8001 internal，不发布宿主端口）；sidecar `FG_INTERNAL_API_BASE_URL` 分离 base；12 个 internal 测试迁 `internal_client` + 公开 listener 404 回归。**E2E（宿主/nginx 不可达）未验**。
- **P1 工具并发去重**：`execute` 原子占位（空 result_json + 唯一索引 flush 冲突兜底）→ 回填；in-flight 命中 409 `AGENT_TOOL_CALL_IN_PROGRESS`；拒绝路径回滚占位。
- **P1 导出资格**：下载路径事务内先解密后消费一次性资格；损坏密文 410 且不烧资格（回归）。成熟 AEAD 未做。
- **P1 RAG session-space**：共享 scope 确认绑定来源 message 的 session 空间（回归含同空间放行）；顺修 conftest 清表顺序。
- **P1 Web fetch 用途**：`web_approved_urls.use_case`（migration 0019）+ fetch 按凭据用途取 policy（fact_check-only 空间回归）；DNS TOCTOU/redirect 未做。
- agent vitest 65 passed / type-check 绿；compose config 通过；`agent_queue.py` ruff format 修复。

### 暂停状态

- 最后一轮全量 pytest/mypy/format 复验被中断；重启后先跑完。
- 剩余 P1：成熟 AEAD、DNS TOCTOU、错误脱敏统一、ProviderGateway 唯一 egress、Guard fail-closed 合同、Steward canonical Job 生产入口、前端 store generation/abort；E3 全部未开始。
- 进度快照已写入子任务 `notes.md §7`；`implement.md` 勾选已完成项（含部分完成标注）。

### Status

[WIP] **6 项 P1 代码+回归落地未提交；全量复验中断；E3 与剩余 P1 未动**

---

## 2026-08-29 会话续：收口任务第二段（Web TOCTOU/脱敏/前端代际）

承接上午暂停点。基线仍 `6f93f6f`（未 commit），与并行进程改动叠加。

- **P1 错误脱敏**：`agent/src/redact.ts`（URL 凭据/Bearer/API key 形参替换+控制字符+300 截断），worker assistant-error 与 catch-all settle/log 接线，5 单测。
- **P1 DNS TOCTOU**：`_validate_public_url` 返回验证 IP 集；`_PinnedTCPBackend`（httpcore.SyncBackend 子类）+ `_pinned_client`（httpx 0.28.1 内部 pool 替换，注释声明版本耦合）；fetch/provider_search 钉扎连接；redirect 维持 fail-closed。回归断言域名永不进 connect。
- **并行进程整合**：secretbox AEAD（AES-256-GCM+HKDF+key_id）确认与"先解密后消费"组合正确，顺带清 lint/format。
- **P2 前端代际**：members/spaces/actionCards 三 store 迟到响应不回写（spaces 2 回归，含 clear 复位 loading 修复）。
- 全绿：backend 536+mypy+ruff；agent 70+build；frontend 171+build。
- 剩余 P1（ProviderGateway 唯一 egress、Steward 生产入口）与并行进程热文件直接冲突，留待协调后单独执行；E3 全部未动。
- 工具注：shell hook 相对路径按 cwd 解析，cwd 在子目录时全局拦截；会话内两度 shim 恢复，建议 trellis 改绝对路径。

### Status

[WIP] **P1 剩两项大重构（需协调）+E3；其余 P1/P2 代码与回归已落地未提交**

---

## 2026-08-29 会话三段：无并行进程；ProviderGateway/Guard 复核 + Steward 生产入口落地

用户确认并行进程已停止。复核原"大重构"项：ProviderGateway（唯一解密出口
resolve_runtime→internal context 单路径；sidecar 单源 projection）、Guard
直接 onPayload fail-closed（HTTP 前抛错）、AgentJob(kind="steward") 生产零写
路径——三项经 anchor 级代码复核均已收口（notes §9），无需重写。

新实现 canonical StewardJob 生产入口：`services/maintenance.py` 进程内维护循环
（agent reaper + steward reaper + queued 作业泵 ≤10/tick，毒药作业 failed 结算
不卡泵），lifespan 启停 + 双 listener 单例防重，`STEWARD_WORKER_ENABLED`/
`MAINTENANCE_INTERVAL_SECONDS` 新配置，compose 透传。5 例 tick 级回归
（端到端/过期回队/关闭 noop/reaper 接线/崩溃结算）。

全绿：backend 541 + mypy strict 119 文件 + ruff；frontend 171；agent 70；
compose config 通过。改动仍未 commit；E3 证据未开始。

### Status

[WIP] **代码层 P1/P2 全部落地或复核收口；余 E3 证据与 commit/AC 回写**

---

## 2026-08-29 会话四段：独立复核整改（Provider 代理 / 停机生命周期 / 网络与弱密钥）

独立复核推翻 §9 两处"已收口"：解密集中 ≠ egress 集中；双 listener 停机互斥；
compose 网络隔离不完整；弱密钥默认值；证据不可复现（宿主无 venv PATH）。

整改：① Provider 代理——context 只下发代理路径（不解密/无 api_key），sidecar
以 run token 打 internal 代理端点，服务端解密转发 + 流式透传 + 字节审计 +
错误脱敏（6+4 例回归，context 合同断言更新）；② serve.py 共享信号处理器
（uvicorn per-server capture 覆盖问题）+ maintenance 引用计数启停；③ compose
backend 网络 internal:true + 172.28.0.0/24 静态 IP 绑定 internal 接口 +
SECRET_KEY/AGENT_SERVICE_SECRET :? 必填 + 移除 sidecar 平行 env；④ 启动校验
（端口冲突/生产禁通配 internal host/维护间隔）。

全绿（绝对路径可复现命令见 notes §10）：backend 547+mypy 120 文件+ruff；
agent 74+build；frontend 176+build；compose config 0。

### Status

[WIP] **代码层整改完成；E3 运行证据与 commit 待用户安排**

---

## 2026-08-29 会话五段：E3 真实模型回路打通（abrdns GLM-5.2）

按用户指定换用 pi 配置 abrdns/GLM-5.2（精确大小写 id——new-api 网关大小写敏感，
小写报 no channel）。空库 20 迁移 + 双 listener + sidecar 全链路 run 4
**succeeded ~18s**：模型自主调用 get_self_context 工具，真实中文正文，
egress 审计 200×2（5.5KB+16KB）——Provider 代理唯一 egress 在真实上游下验证。
guga-copy/deepseek 重载荷间歇 503 → run 1 failed 留作 fail-closed 证据。

顺带修两缺陷：代理透传 Content-Type/Accept/UA（否则上游 400）；sidecar 注入
pi-ai 重试（AGENT_PROVIDER_STREAM_MAX_RETRIES=5，间歇 503 必需）。
证据：research/e3-model-loop-evidence.md；AC 部分项升级为有 E3 证据。
仍待：compose 栈重建复验、375px、第二卷恢复、commit。

### Status

[WIP] **真实模型回路 E2E 已取证；余 compose 栈 E3 与 commit 待用户**


## Session 3: V2 Agent 收口 trellis-check 复验与父子任务归档

**Date**: 2026-08-29
**Task**: V2 Agent 收口 trellis-check 复验与父子任务归档
**Branch**: `main`

### Summary

对 08-29-v2-agent-architecture-release-closure 执行 trellis-check：task.py validate 父子任务均通过；backend ruff/format/mypy(120 files)/pytest 547 passed 全绿；agent type-check/lint/74 tests/build 全绿；frontend 复验被中断，此前记录 176 passed（后续由 redesign 任务延续）。用户确认任务已完成，归档子任务与父任务 08-28-v2-audit-remediation（archive/2026-08/）。

### Git Commits

| Hash | Message |
|------|---------|
| `f596ead` | (see git log) |
| `add7fec` | (see git log) |
| `7d5c8e2` | (see git log) |

### Status

[OK] **Completed**


## Session 4: 前端后台角色分域与安全收尾

**Date**: 2026-08-30
**Task**: 前端后台角色分域与安全收尾
**Branch**: `feat/frontend-role-boundaries`

### Summary

完成平台运营后台 /admin 与家庭空间管理 /spaces/:spaceId/manage 分域，补齐导航、空间图隔离、邀请与 ownership transfer active membership 授权、平台运营者 visibility/RAG 隔离；前端 40/242、后端 571、Agent 80 门禁通过。主会话完成 /admin、空间管理、双主题和 375px 无溢出验证；完整 compose 逐页人工矩阵仍待后续执行。并行 v2-agent-runtime 改动未纳入本次提交。

### Git Commits

| Hash | Message |
|------|---------|
| `77babc7` | (see git log) |

### Status

[OK] **Completed**


## Session 5: 空间邀请契约修正：active member 可邀请

**Date**: 2026-08-30
**Task**: 空间邀请契约修正：active member 可邀请
**Branch**: `feat/frontend-role-boundaries`

### Summary

按用户确认的产品契约把空间邀请从 owner/space_admin 放宽到 active member（guest 仍禁止，受邀人仍需接受）；同步 canInvite、Home 邀请入口、后端命令与双侧测试，architecture 授权矩阵新增邀请行。前端 40 文件/243 测试通过，后端 571 通过（唯一失败来自并行 v2-agent-runtime 未提交文件，与本任务无关）。族谱空间开辟审批流与 Agent 归属规划记录为后续任务。

### Git Commits

| Hash | Message |
|------|---------|
| `21bed18` | (see git log) |

### Status

[OK] **Completed**


## Session 6: 完成 V2 Agent Runtime Provider 兼容修复并归档

**Date**: 2026-08-30
**Task**: 完成 V2 Agent Runtime Provider 兼容修复并归档
**Branch**: `feat/frontend-role-boundaries`

### Summary

修复 Pi/pi-ai openai-responses Provider 工具名 wire 兼容性：canonical familygraph.* 仅在出站映射为 familygraph_*，policy/events 再反解回规范名；通过 Agent 87 项测试、Agent 相关后端 114 项测试、真实 liu-dada/gpt-5.6-sol 成功正文与取消 E2E，并归档 v2-agent-runtime-full-repair。

### Git Commits

| Hash | Message |
|------|---------|
| `79e3fa0` | (see git log) |
| `fe80479` | (see git log) |

### Status

[OK] **Completed**


## Session 7: 完成空间管理员审批流程

**Date**: 2026-08-30
**Task**: 完成空间管理员审批流程
**Branch**: `feat/space-manager-approval`

### Summary

将管理者申请收窄为已有空间 active member 晋升 space_admin 的平台审批流程；保留 member 邀请和自由建空间语义，补齐并发提交/裁决安全、前后端测试与 0021→0020 迁移协调记录。前端 250 测试、后端 587 测试、mypy、Ruff 和任务校验全通过。并行 runtime 的 backend/tests/conftest.py 未暂存。

### Git Commits

| Hash | Message |
|------|---------|
| `68b45e9` | (see git log) |

### Status

[OK] **Completed**


## Session 8: 收口系统管理员与空间唯一管理员任务

**Date**: 2026-09-01
**Task**: 收口系统管理员与空间唯一管理员任务
**Branch**: `main`

### Summary

核对 Claude 会话与工作树，完成系统管理员/空间唯一管理员任务质量门禁并归档；后续 Agent Runtime assistant-only 与人物身份去重 WIP 保持未提交，另行反馈。

### Git Commits

| Hash | Message |
|------|---------|
| `4f73146` | (see git log) |

### Status

[OK] **Completed**


## Session 9: PersonalFamilyView 前端整体落地（Phase 1-7 + 双主题走查 + P0 修复）

**Date**: 2026-09-02
**Task**: PersonalFamilyView 前端整体落地（Phase 1-7 + 双主题走查 + P0 修复）
**Branch**: `main`

### Summary

完成 09-01-personal-family-view-frontend 全部七个 Phase：统一家庭壳/路由重构、家庭卡与家族树切换到 PersonalFamilyView 授权投影、只读公示页与关系说明、记忆五标签/通知/空间化统计/设置/空间管理、系统管理员边界占位；household-card/notifications/spaceStats 为 BLOCKER 合同占位（decoder+store+fixture 测试）。375px 双主题隔离环境真实走查：发现并修复 /login 无限重载 P0（壳初始导航窗口未认证请求 + 会话过期跳转循环）与公示页覆盖当前空间上下文 P1，补回归测试。四项门禁全绿（468 测试），trellis-check pass，spec 更新（空间键控 store、壳引导 gotcha、BLOCKER 合同占位模式）。

### Git Commits

| Hash | Message |
|------|---------|
| `a67010a` | (see git log) |

### Status

[OK] **Completed**


## Session 10: 修复管理员交接并发测试死锁（09-01-ownership-transfer-test-deadlock）

**Date**: 2026-09-02
**Task**: 修复管理员交接并发测试死锁（09-01-ownership-transfer-test-deadlock）
**Branch**: `main`

### Summary

accept_transfer 立即事务化（load_actor+检查+CAS+角色翻转同事务）；并发双接受测试改造为 test_person_dedupe 模式（标量 ID、独立 SessionLocal、_SYNC_TIMEOUT、异常可见、主线程重查断言）；20/20 定向重复通过，全量 pytest 626 passed/0 failed，ruff/mypy 绿；确认无永久 deselect。Spec 新增「SQLite 读事务升级与并发用例陷阱」小节（pysqlite 快照语义、BEGIN IMMEDIATE 真实价值、并发用例防挂死）；database-guidelines.md 因混有 remove-guest-role WIP 采用了部分暂存。仓库级 ruff 红点均为 notifications 等并行 WIP 未跟踪文件，不属本任务。

### Git Commits

| Hash | Message |
|------|---------|
| `4cb0d4b` | (see git log) |
| `ca83745` | (see git log) |

### Status

[OK] **Completed**


## Session 11: 完成 PersonalFamilyView 遗留投影闭环任务（通知测试+门禁收口）

**Date**: 2026-09-03
**Task**: 完成 PersonalFamilyView 遗留投影闭环任务（通知测试+门禁收口）
**Branch**: `main`

### Summary

补齐 09-02-personal-family-view-followup 缺口：aabb80f 已含 household card/space stats/notifications 服务端实现，本次新增 notifications 9 例与 space_stats 9 例合同测试（收件人隔离、masked 哨兵、已读只改 read_at、ETag/304 授权先于缓存、fail-closed 引用校验、撤权即时隐藏），修正 mypy 返回注解并 ruff 收口；pytest 全量 659 passed 无 deselect、前端 490 tests/build 通过、0027 迁移 up/down/up 验证；trellis-check 8 项全 PASS 后提交并归档。

### Git Commits

| Hash | Message |
|------|---------|
| `896d32c` | (see git log) |

### Status

[OK] **Completed**


## Session 12: 完成 09-01-system-admin-governance-routes 验收与收尾

**Date**: 2026-09-03
**Task**: 完成 09-01-system-admin-governance-routes 验收与收尾
**Branch**: `main`

### Summary

系统管理员治理路由任务收尾：审计确认全部实现（登录页主体硬校验、principal 感知 PIN/会话回跳、SystemAdminShell 登出、治理 API 边界回归测试）已由用户随 aabb80f 提交；trellis-check 验收 ACCEPT（9/9 验收项通过，后端定向 29 passed/前端 490 passed，type-check/lint/build/ruff/mypy 全绿）；沉淀前端主体感知会话约定与后端治理边界回归测试规范；归档任务。

### Git Commits

| Hash | Message |
|------|---------|
| `31cd4e7` | (see git log) |

### Status

[OK] **Completed**


## Session 13: 完成并归档空间角色与个人家族视图任务

**Date**: 2026-09-03
**Task**: 完成并归档空间角色与个人家族视图任务
**Branch**: `main`

### Summary

完成 remove-guest-role 的迁移边界、两值角色 schema 与后台类型收敛，补充 SQLite 约束/连接状态回归测试和数据库规范；后端 pytest 666 passed/3 skipped、前端 490 tests passed、mypy/lint/type-check/build/Alembic 验证通过。归档 remove-guest-role 与已完成的 personal-family-view。后端仍有与本任务无关的既有 Ruff 问题。

### Git Commits

| Hash | Message |
|------|---------|
| `5473de1` | (see git log) |

### Status

[OK] **Completed**


## Session 14: 09-04 星空玻璃重构收尾：双前端圆形FAB与后台视觉落地

**Date**: 2026-09-05
**Task**: 09-04 星空玻璃重构收尾：双前端圆形FAB与后台视觉落地
**Branch**: `main`

### Summary

完成 09-04-frontend-glass-cosmic-redesign 剩余项：HouseholdCardView 底部圆形 FAB Dock 与水晶退出按钮、FamilyTreeView 圆形工具 Dock 与新增返回家庭卡入口、RelationshipDetailPanel 微光玻璃抽屉；范围扩展至 system-admin-frontend（--ag-glass-* token、三层星空底、sticky 磨砂顶栏、卡片/登录玻璃化，零家庭依赖）。颜色红线复核：组件与 tokens.css 的 rgba/hex 全部改为 color-mix token 派生。双前端门禁全绿（家庭端 445 测试/type-check/build，后台 lint/type-check/build）。任务已归档。遗留：375px 双主题人工走查待用户复核。

### Git Commits

| Hash | Message |
|------|---------|
| `e0789ab` | (see git log) |
| `700aae5` | (see git log) |

### Status

[OK] **Completed**


## Session 15: 后台星空玻璃深度重构（09-05-admin-cosmic-glass-redesign）

**Date**: 2026-09-05
**Task**: 后台星空玻璃深度重构（09-05-admin-cosmic-glass-redesign）
**Branch**: `main`

### Summary

用户反馈后台视觉过素。经查 09-04 克制版玻璃效果已部署但肉眼不可辨（星点/星云/玻璃透明度过弱）。新建任务按设计规格将家庭端星空玻璃语言完整移植后台：星云 18%+暗角+加密星点、真玻璃卡片+入场动画、渐变品牌文字、登录页专属星空。颜色字面量收拢 :root；渐变中间色按 WCAG 复算换 #3d6cb0（全程≥4.5:1，核验落附录）。trellis-implement/trellis-check 子代理流程，lint/type-check/build 全绿，镜像重建部署 grep 验证。王德海家成员为无空间归属的建档裸用户（space_members=0），后台按空间读模型不显示属预期。另：会话中完成后台部署升级（.env 补 ADMIN_JWT_*、迁移 0028 显式处置旧 PIN 管理员、修复 compose admin 网络 internal:true 丢端口 bug——docker-compose.yml 改动未提交，属运维修复待单独处理）。

### Git Commits

| Hash | Message |
|------|---------|
| `5fb5b58` | (see git log) |

### Status

[OK] **Completed**


## Session 16: dev-seed 种子模块上线：清库重播种与空库自动播种机制

**Date**: 2026-09-05
**Task**: dev-seed 种子模块上线：清库重播种与空库自动播种机制
**Branch**: `main`

### Summary

落地 09-05-dev-seed-demo-data：app/dev_seed.py（双重门控播种 + --reset 清库 CLI）、三代结构演示家庭（王德海家 6 成员/PIN 123456/5 条 confirmed 关系事实）、compose 透传 DEV_SEED_DEMO_DATA 与 PERSONAL_FAMILY_VIEW_ENABLED。trellis-implement/trellis-check 双代理流程，谱系方向经检查代理独立推导修正（修正 PRD 箭头写法与 v1 边语义相反的口径）。部署：清库（备份 full-reset-20260905-062327.db 于数据卷）→ 重启 → bootstrap 生成新 admin + 凭据文件 → 播种 6 用户。端到端验证：admin 登录 200、王德海/123456 登录 200、家庭卡 5 成员、家族树 6 节点 5 边称谓正确。混合文件（config.py/main.py 含并行任务 WIP）经选择性暂存仅提交本任务 hunk。遗留：全量 pytest 有 1 个失败属并行任务 binding 改动；.env 的 DEV_SEED_DEMO_DATA=1 为 dev 专用，生产部署保持缺省关闭。

### Git Commits

| Hash | Message |
|------|---------|
| `b5bd4ac` | (see git log) |

### Status

[OK] **Completed**
