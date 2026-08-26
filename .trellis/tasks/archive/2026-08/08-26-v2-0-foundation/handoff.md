# V2.0 Foundation 交接摘要

## 状态：完成（2026-08-26，本会话）

分五个块实现并通过全量质检（PASS-WITH-NOTES → 两个 MINOR 已当场修复）：

| 块 | 内容 |
|---|---|
| D1 | architecture.md v2 权威合同；迁移 `0008_v2_foundation`（9 新表 + users.profile_status + accounts.status + spaces.kind + 角色/约束重构，owner FK CASCADE→RESTRICT）；VisibilityPolicy v2 四级 + 字段 mask + purpose 收紧 + minor overlay；platform_operator 与空间角色分离 |
| D2 | `app/commands/` 领域命令层（8 聚合 34 命令，短事务=授权→FSM→写入→domain_events→audit）；owner invitation 单次兑换（并发安全）；ownership transfer FSM；确档清单；数据权利 export/correct/delete/dispute |
| D3 | 前端全量：IdentitySetupView 确档向导、建档三选空间、SpaceGovernanceDialog（角色/邀请/移交）、AdminView operator 化、披露矩阵、数据权利面板、governance store |
| D4 | 收口：space_profile_refs 读取端点+前端展示；/me 携带身份状态（守卫去掉 fail-open）；逐空间披露端点（高敏类别 Literal[False] 永不可开）；修复 disclosed_categories 双向合并缺陷 |
| D5 | 质检修复：admin PATCH 迁入命令层 + break-glass note 必填；transfer 审计行断言 |

## 门禁终态

backend ruff/format/mypy strict(70 files)/pytest **183**；frontend type-check/lint/vitest **69**/build；docker compose 空库 E2E + 备份快照恢复演练通过（主会话实跑）。

## 后续任务需知的实现决策

1. 角色名用 `owner`（非 PRD 的 `space_owner`），design.md 授权的命名微调。
2. 「这是我」确认是唯一合法的 Account+Profile 联动转换（`POST /me/identity/confirm`），其余路径两状态机独立。
3. 过期类终态在返回错误前先持久化（过期是事实，不随 409 回滚）。
4. domain_events 单一 `emit()` 入口 append-only；事件类型清单见 D2 报告（profile.*、account.claimed、disclosure.*、space.*、relation.*、data_right.*、claim_dispute.* 等）。
5. 高敏感披露五类（health/address/school/contact/private_notes）schema 层面永远只能 false。
6. 建档必填关系语义由向导的 `POST /connection-requests` 承担（AD-4 合并语义），MemberCreateRequest 故意不含 relation 字段。
7. 导出处理中途崩溃会停留 processing 态（最小实现，未加清扫任务）。

## 给 V2.1 的接口

- Agent 工具应直接调用 `app.commands.*`（ActorContext 由 service token 构造，禁止请求体传 actor）。
- VisibilityPolicy.evaluate(actor, target, space_context, purpose) 是唯一投影入口；purpose='agent' 上限 lineage_summary。
- space_profile_refs 最小字段合同：{profile_id, name, added_at}。
