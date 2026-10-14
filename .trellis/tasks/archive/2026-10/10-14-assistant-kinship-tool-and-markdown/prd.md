# 助手称谓工具被关闭时仍被广告 + 助手回复 markdown 原样显示

## 背景（2026-10-14 现场证据）

用户在家庭助手提问「马皇后是我的什么人」，前端工具摘要出现两条
`familygraph.get_term_alternatives 失败`，助手正文则把
`**依据（已确认事实 + 派生路径）：**` 原样显示。

后端访问日志（dev backend，run 1844，12:42:30–12:42:36 UTC）：
`POST /internal/agent/runs/1844/tools/familygraph.get_term_alternatives/execute` → **503**
共 12 行（模型 2 次调用 × sidecar 约 6 次重试）。

该路由只有两个 503 来源：执行准入（会打 `admission rejected` 警告）与亲属称谓 flag 门禁
（不打日志）。该时段 run 1844 无任何准入拒绝行（12:35–12:37 的 22 条 `cluster_full`
全部属于 steward run 1840），且运行中进程 `/proc/21439/environ` 与
`/home/ubuntu/.config/familygraph/familygraph.env` 都没有
`RELATIONSHIP_INTELLIGENCE_ENABLED`（`config.py` 默认 False），因此只能是
`agent_tools.py:852` 的 `KINSHIP_FLAG_DISABLED`。

## 问题

1. **契约缺陷（主因，代码层）**：`agent_tools.default_allowlist()` 已按「A run must not
   advertise a capability it cannot use」排除未启用的 Web / 记忆 / steward viewer 工具，
   却**没有**排除 flag 关闭时的三个 `_KINSHIP_INTAKE_TOOLS`
   （`resolve_free_text_relation` / `get_term_alternatives` / `record_term_usage`）；
   同时 `agent/src/prompt.ts` 的静态系统提示无条件命令模型调用它们。
   后果：能力关闭时仍被广告并被指示调用 → 每次调用必然 503（且 503 被 sidecar 视为可重试，
   白烧约 6 次重试与一轮模型调用）→ 模型只能回答「服务暂不可用/资料不足」。
2. **显示缺陷（独立）**：`frontend/src/components/agent/MessageList.vue` 用
   `{{ item.text }}` + `white-space: pre-wrap` 纯文本渲染助手正文，前端无任何 markdown
   依赖、全仓无 `v-html`，因此 `**粗体**`、列表、标题全部按字面量显示。
3. **部署状态（dev）**：dev 后端未开启 `RELATIONSHIP_INTELLIGENCE_ENABLED`，
   称谓工具与其浏览器面端点在 dev 上整体不可用。

## 目标

- 后端不再向模型广告当前无法使用的称谓工具（任何 flag 状态的部署都成立）。
- sidecar 系统提示不再无条件命令调用未广告的工具。
- 助手正文按 markdown 语义渲染（粗体/斜体/行内代码/列表/标题/段落），
  **不引入 `v-html`**、不新增运行时依赖。
- dev 环境开启称谓能力并端到端验证工具真实可用。

## 非目标

- 不改动 kinship 端点、TermRegistry、SourceFact 的任何授权或数据语义。
- 不做 markdown 全量：**链接、图片、脚注、任务列表、引用块、嵌套强调、单元格对齐、
  原始 HTML 直通**都不支持。**表格已纳入**（首轮真实验收里模型自己产出了
  `| 来源层级 | 可用叫法 |`，不渲染就退化成一行竖线，见「验收证据」）。
- 不引入 markdown 依赖、不使用 `v-html`（全仓既有红线）。
- 不处理本次现场顺带发现的两个已修/已收敛故障（steward run 的 `cluster_full` 与
  已由 `856b17f` 修复的 `agent/dist` 过期），不顺手改容量配置。
- 不改动生产（prod compose）的 flag 状态。

## 验收标准

- **AC-1**：`RELATIONSHIP_INTELLIGENCE_ENABLED=False` 时，`default_allowlist("assistant")`
  返回的清单不含三个 `_KINSHIP_INTAKE_TOOLS`；为 True 时含（回归用真实 db 作用域）。
- **AC-2**：该排除与既有 Web/记忆排除同形（同一 `_unusable` 机制），不新增分支写法；
  其余工具清单逐项不变（防止误伤）。
- **AC-3**：sidecar 构造的 assistant 系统提示在 allowlist 含
  `familygraph.get_term_alternatives` 时保留称谓段落，不含时移除该段落；
  其余段落逐字不变（快照/逐字断言）。
- **AC-4**：助手正文 `**依据**\n\n- 关系路径…` 渲染出 `<strong>` 与列表元素，
  文本内容与原文一致（去掉标记符号）；纯文本（含 `<script>` 字面量）不被当作 HTML 执行；
  渲染结果中不存在 `v-html`/`innerHTML` 使用。
- **AC-5**：`frontend` 门禁全绿（lint / type-check / test / build），
  `backend` 受影响测试 + ruff + mypy 通过，`agent` vitest 通过。
- **AC-6**：dev 环境（`/home/ubuntu/.config/familygraph/familygraph.env`）开启
  `RELATIONSHIP_INTELLIGENCE_ENABLED=1`，重启 `familygraph-api` 后，
  真实发起一次称谓提问，`get_term_alternatives` 返回 `ok`（工具摘要显示成功）
  且助手正文引用了真实叫法清单；证据（日志行/DB 事件）留档。

## 验收证据（2026-10-14，dev 环境真实链路）

- **flag 生效（进程级）**：重启前 `GET /api/kinship/resolve` → 503 `KINSHIP_FLAG_DISABLED`；
  重启后 → 401 `AUTH_UNAUTHORIZED`（门禁已开，只差鉴权）；`/proc/<pid>/environ` 含
  `RELATIONSHIP_INTELLIGENCE_ENABLED=1`；`/api/health` 200。
- **工具真实可用（后端访问日志）**：
  `POST /internal/agent/runs/1849/tools/familygraph.get_term_alternatives/execute → 200`
  （单次调用，无重试；对照修复前 run 1844 的同名端点 503 × 12）。
- **前端真实链路（浏览器）**：dev 体验账号（朱元璋 / 明皇室）提问
  「马皇后是我的什么人？这个空间里还有哪些叫法？」——工具摘要 7 项全部「成功」
  （`get_self_context` / `resolve_free_text_relation` / `search_space` /
  `get_relationship_path` / `get_profile_summary` / `get_term_alternatives` /
  `explain_structural_path`），正文给出 `Sf` 概念码、配偶路径与叫法来源表格。
  截图：`evidence-assistant-markdown-and-tools.png`。
- **渲染（工作区前端 5175，运行本次改动）**：同一答案的 DOM 断言——
  `[data-test="markdown-text"]` 2 个、`.md-table` 1 个（单元格文本
  `来源层级/可用叫法/个人偏好/未设置/当前空间推荐/无/地区语言包/妻子/系统标准/妻子`）、
  正文不再含字面竖线。

## 交付物

- 代码：`backend/app/services/agent_tools.py`、`agent/src/prompt.ts`（及其调用点）、
  `frontend/src/components/agent/`（新增渲染组件 + 接线）、对应测试。
- 部署：dev env 一行 + 重启 + 验证记录。
- 规范回写：`agent-runtime.md` 补「不得广告不可用能力」的称谓工具条目；
  `component-guidelines.md`/`quality-guidelines.md` 补 markdown 渲染约定。
