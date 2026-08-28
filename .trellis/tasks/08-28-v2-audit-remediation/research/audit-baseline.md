# V2 审计基线与证据索引

> 审计日期：2026-08-28  
> 代码基线：`dc46e34 fix(v2-facts): close SourceFact production write gap; clarify is_admin semantics`  
> 记录性质：只读审计证据。本文不把 handoff 中的“已完成”陈述当作事实，所有完成判断都必须由当前代码、测试输出或可重跑命令证明。

## 1. 审计范围

覆盖以下归档任务及其工件：

- `.trellis/tasks/archive/2026-08/08-26-v2-agent-system`
- `.trellis/tasks/archive/2026-08/08-26-v2-0-foundation`
- `.trellis/tasks/archive/2026-08/08-26-v2-1-agent-runtime`
- `.trellis/tasks/archive/2026-08/08-26-v2-2-readonly-assistant`
- `.trellis/tasks/archive/2026-08/08-26-v2-3-relationship-intelligence`
- `.trellis/tasks/archive/2026-08/08-26-v2-4-steward-action-card`
- `.trellis/tasks/archive/2026-08/08-26-v2-5-memory-rag-policy`
- `.trellis/tasks/archive/2026-08/08-26-v2-6-controlled-web`

检查内容包括：任务拓扑、`task.json` 状态、PRD AC 勾选、design/implement/handoff/notes 一致性、JSONL 路径、关键后端/Agent/前端边界、质量门禁和 E2E 证据。

## 2. 严重问题清单

### G0 — 归档上下文可复现性

七个子任务的 `implement.jsonl` / `check.jsonl` 仍引用归档前路径：

```text
.trellis/tasks/08-26-v2-agent-system/research/...
```

研究文件实际位于：

```text
.trellis/tasks/archive/2026-08/08-26-v2-agent-system/research/...
```

在审计记录中，父任务 `task.py validate` 通过而七个子任务分别失败 1、3、1、2、1、1、3 个引用。整改必须同时验证：活动目录、归档目录、历史自引用和跨平台路径均可解析；不能只依赖人工记忆。

### G1 — 完成状态与规划文档互相矛盾

父任务 `task.json` 标为 `completed`，但父 `handoff.md` 仍写 `planning`、未启动、未修改产品代码；父 `prd.md` 也保留规划阶段语义。七个子任务的 `task.json` 均为 `completed`，但子 PRD 的全部 AC 仍未勾选。Foundation 的 `implement.md` 仍是全未勾选清单。

整改目标不是“把所有框打勾”，而是建立状态证据规则：每一条 AC 必须链接到测试/命令/产物；没有证据就保持 `partial` 或 `blocked`，不能写 `completed`。

### F1 — 建档与首条关系不是原子事务

- 后端 `backend/app/api/users.py:121-155` 的建档请求不接收关系。
- 前端 `frontend/src/components/member/MemberCreateWizard.vue:180-202` 先创建档案，再独立提交关系；关系失败只显示 warning，档案保留。

这会留下无关系的 provisional 档案，违反“名字和关系必填”以及组合事务合同。整改必须有单一领域命令、幂等键、失败回滚和重复提交测试。

### F2 — 统计出口绕过字段级可见性

`backend/app/services/visibility.py:351-388` 只产生可见用户 ID；`backend/app/api/misc.py:42-69` 随后直接读取 `gender`、`birth` 并返回精确生日。统计、搜索、RAG、导出和 Agent 都必须消费同一 `VisibilityDecision` 的字段投影，而不是只复用 ID 集合。

### F3 — provisional 的 custody 读取优先级不明确且存在越权路径

`backend/app/services/visibility.py:223-227` 仅凭 `created_by` 就返回 `household_detail`，可能绕过 provisional/minor overlay。整改采用收紧解释：代管人可有独立的 `custody_management` 投影以完成管理，但不能得到凭据、精确高风险字段或让普通 profile/agent/statistics 投影越过最小节点规则。

### F4 — 导出文件明文

`backend/app/commands/data_rights.py:225-229` 直接写普通 JSON，而 Foundation design 要求加密、短期下载。整改需使用服务端密钥封装/加密文件、短期一次性下载授权、过期清理和静态/动态测试。

### R1 — Provider 配置没有接入真实执行链

`backend/app/services/agent_provider.py:43-103` 只解析配置并返回 `secret_ref`；`backend/app/api/internal_agent.py:345-350` 将引用传给 sidecar；`agent/src/session.ts:108-120` 实际从环境变量读取 `baseUrl/apiKey`，未消费 `secret_ref`。因此管理端 Provider 配置不能驱动真实调用，AC-RT6 不成立。

### R2 — Internal API 的网络面未隔离

`backend/app/main.py:148-150` 挂载 internal router；`docker-compose.yml:15-16` 发布 API 端口。当前依赖 service secret 而不是网络隔离，默认开发 secret 也不应被视为生产防线。整改必须让宿主无法访问 internal listener/path，并保留 service/run token 双重校验。

### R3 — 工具 schema 不是统一 fail-closed

`backend/app/services/agent_tools.py:314-357` 未完整校验 `minLength`、数值范围等约束；sidecar TypeBox 和后端注册表可漂移。整改需建立一个权威合同、生成/快照同步和畸形输入负向测试。

### R4 — 事件生命周期和审计存在一致性缺口

- 入队和 sidecar 都可能写 `message.user_added`（`backend/app/services/agent_queue.py:150-162`、`agent/src/worker.ts:173-191`）。
- reaper 收敛 `expired/cancelled` 时没有持久化公开终态事件或广播（`backend/app/services/agent_queue.py:470-515`）。
- 工具审计使用 `account_id` 作为 `actor_id`（`backend/app/services/agent_tools.py:427-432`），需与 audit 语义确认并修正。
- sidecar 将 provider HTTP body 拼入错误，缺少统一 secret redaction（`agent/src/client.ts:231-237`、`agent/src/worker.ts:242-249`）。

### A1 — Assistant 与前端证据缺口

V2.2 notes 明确 375px 真机走查未完成，仅有 `<768px` 自动化断点。必须补人工/浏览器可复核记录、焦点和 reduced-motion 检查，并验证空间切换、登出、401 和 SSE 重连后的缓存清理。

### M1/W1 — Memory/RAG、Steward、Controlled Web 的全链路证据不足

代码面存在对应实现，但 handoff 多为摘要，没有把命令、退出码、测试数量、当前 commit、SSE 历史恢复和带引用联网 E2E 绑定在一起。整改要把“实现存在”和“独立可重跑”分开记录，并重新执行跨空间、删除/tombstone、Steward 单空间、SSRF/PII/引用和备份恢复矩阵。

## 3. 正面基线（不可回退）

- v2 七个阶段均有实质代码提交，不能以整改任务重写或删除真源。
- 账户/档案/事实三状态机、空间/关系 FSM、ActionCard、RAG/Guard、受控 Web 的总体边界仍有效。
- 当前没有部署、真实成员或业务数据；不需要生产迁移、双写、回填或旧客户端兼容窗口。
- 用户原始关系输入必须保留；Agent 不能直接写 SourceFact、发送申请、合并空间或扩大公开范围。
- Assistant 绑定 `account_id + session_id + space_id`；Steward 绑定 `space_id + job_id`；跨空间和跨 Agent 记忆不可共享。

## 4. 证据等级

| 等级 | 含义 | 可用于关闭 AC？ |
|---|---|---|
| E0 | 设计/声明，没有实际输出 | 否 |
| E1 | 代码或单元测试存在，但未绑定命令和当前 commit | 只能说明实现线索 |
| E2 | 当前 commit 可重跑的命令、退出码、数量和关键断言 | 可以关闭局部 AC |
| E3 | 空库 Compose/跨进程 E2E、恢复演练和安全对抗均有产物 | 才可关闭发布级 AC |

本整改任务要求所有 P0/P1 和最终发布 AC 达到 E2；跨层、备份、隐私和网络边界要求 E3。
