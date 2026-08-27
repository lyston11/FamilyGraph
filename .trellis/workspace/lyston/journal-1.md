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
