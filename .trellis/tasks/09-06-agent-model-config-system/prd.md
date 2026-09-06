# Agent 模型配置体系（父子任务）

> 状态：Planning。本任务是 2026-09-06 设计拷问（grilling）收口的权威记录；两个子任务的范围、红线与依据均以此为准。实现前必须审阅各子任务 PRD 与 `design.md`。

## Goal

修复 Agent 模型配置体系的"设计未闭环"缺陷：RT-5 只交付了 `/api/admin/agent` 后端治理 API，但授予入口、owner 选择页、报错文案承诺的管理页全部缺失，导致助手在所有空间必然报 `PROVIDER_UNRESOLVED`。按原始设计（v2-agent-system PRD R8）与 2026-09-06 决策，将模型治理迁到系统管理员后台统一配置，覆盖助手（Assistant）与管家（Steward）两个 agent。

## 背景与依据（Confirmed Facts）

- `backend/app/api/agent.py:285`：发消息时 `resolve_for_space` 解析失败即 409 `PROVIDER_UNRESOLVED`，绝不静默换云。
- 旧治理端点 `/api/admin/agent/*` 挂在家庭身份域（platform_operator 鉴权），**全库零调用方**；`platform_role_assignments` 表 0 行，且无任何授予入口——纯孤岛。
- `services/steward.py:3` 的"Steward 不调用 LLM"是 V2.4 第一阶段的临时裁定，不是终态。权威设计见归档任务：
  - `08-26-v2-agent-system/prd.md`：双 Agent 系统；R8"平台运营者维护 Provider 与密钥，空间管理员只能在允许列表中选模型和功能开关；不得静默 fallback"。
  - `09-01-agent-runtime-assistant-only/notes.md`：双 Agent、两种运行边界；Steward 是事件驱动、按空间分区、长期运行的底层引擎 Agent；"未来模型只能辅助候选、排序和解释"、"未来模型 child run/context 审计另立任务"。
  - `08-26-v2-4-steward-action-card/prd.md` ST-1："空间 owner/admin 只配置词典、允许的 Provider、知识库和功能开关"。

## 已确认决策（2026-09-06 拷问收口）

| # | 决策 | 结论 |
|---|---|---|
| D1 | 治理身份域 | Agent 模型治理整体迁到系统管理员域（:8002 admin_app，ADMIN_JWT 鉴权），admin-web 新增管理页 |
| D2 | platform_operator 角色 | 废弃：代码路径下线，表数据保留不删（避免再设计一套授予机制） |
| D3 | 配置分层 | 系统管理员管 Provider 注册表（通道/密钥/允许模型目录）；空间所有者在允许目录内选模型并自行同意云执行（恢复 R8 原始设计） |
| D4 | 平台默认模型 | 管理员可指定平台默认 Provider+模型；新空间自动继承；owner 可在空间设置中改掉或明确停用（解决新空间冷启动必报错的体验问题） |
| D5 | agent 维度 | `agent_space_provider_settings` 增加 agent_kind（assistant / steward），每空间两行，owner 分别选择 |
| D6 | 旧端点 | `/api/admin/agent/*` 直接删除（连同 require_platform_operator 依赖链）；审计与 secretbox 密钥加密机制原样平移到新端点 |
| D7 | 报错文案 | 两句式：通道未配置 →"助手模型尚未由平台管理员配置，请联系平台管理员"；通道已有但空间未选 → 引导"请到 空间管理 → 模型设置 选择" |
| D8 | 管家接模型 | 推翻 V2.4 临时裁定；按 09-01 约定"另立任务"= 子任务 B。模型只做候选/排序/解释辅助，确定性内核与写入红线保留 |
| D9 | 红线 | LLM 产物经 owner 确认后才可写入（如关系修正建议 owner 点确认才落 SourceFact）；自主写入不考虑 |
| D10 | 立项切分 | 子任务 A（治理迁移+双端 UI）先行；子任务 B（Steward 模型辅助）复用 A 的配置底座，先 design 后实施 |

## Requirements

- R1. 治理迁移与双端 UI → 子任务 `09-06-agent-provider-admin-migration`。
- R2. Steward 模型辅助层 → 子任务 `09-06-steward-model-assist`。
- R3. 两个子任务共同遵守：无静默 fallback、敏感内容强制本地 Provider 时本地不可用即明确拒绝（R8/AC-P8 原有合同不变）。
- R4. 云执行 = 家庭成员数据出域；owner 的云同意与逐空间披露语义（disclosure_preferences 模式）不得被管理员单方面覆盖。

## Acceptance Criteria

- [ ] AC-P1：子任务 A 交付后，系统管理员可在 admin-web 完成 Provider 注册/更新/启用停用与平台默认设置；空间 owner 可在家庭前端"空间管理 → 模型设置"分别为 assistant / steward 选择模型并管理云同意。
- [ ] AC-P2：旧 `/api/admin/agent/*` 与 require_platform_operator 链路删除；新端点全部落在 admin_app（ADMIN_JWT），操作写审计，密钥只写不读（响应仅 has_secret 布尔）。
- [ ] AC-P3：新空间在管理员设了平台默认时开箱可用（不出现 PROVIDER_UNRESOLVED）；未设默认时报错文案按 D7 两句式引导。
- [ ] AC-P4：子任务 B 交付后，Steward 的模型调用仅用于候选/排序/解释，产物经 owner 确认才落库；确定性 dirty 重算/冲突检测/资格矩阵行为与红线（绝不写 SourceFact、不发申请、不合并空间、不保存自由形式长期记忆）全部保留并有测试。
- [ ] AC-P5：空库迁移链可复现；AGENT_RUNTIME_ENABLED 关闭时全部模型治理端点 503。

## Out Of Scope

- MatchBroker 跨空间匹配、全平台陌生人推荐（沿袭父 PRD）。
- LLM 自主写入任何正式事实/成员资格/可见权（D9）。
- 管家成为第二个会话式聊天 agent（它是后台引擎 Agent，09-01 边界不变）。

## Notes

- 当前活动任务为 `09-05-familygraph-visual-redesign`（in_progress）；本组任务以 planning 状态排队，待视觉重设计收口后按 A → B 顺序启动。
