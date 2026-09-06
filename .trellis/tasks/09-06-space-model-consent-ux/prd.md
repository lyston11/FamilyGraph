# 空间模型设置 UX：云同意开关即时生效与报错可达性

## Goal

前端让「同意云端执行」等开关状态永远与后端落库状态一致（开关即保存），
并在聊天报错现场给空间管理员可行动的跳转路径。

## 背景（事故复盘）

用户在 空间管理 → 模型设置 打开「同意云端执行」开关后，家庭助手聊天仍报
「该模型需要云端执行同意，请到 空间管理 → 模型设置 开启」。

根因：`SpaceModelSettingsPanel.vue` 的开关只改本地表单状态，必须再点
「保存选择」/「同意并启用平台默认」才 PUT 落库；面板又在无落库行时预填
平台默认 provider/model，观感上"已配置"。DB（容器 /data）中
`agent_space_provider_settings` 为 0 行 → 后端按「继承平台默认 +
cloud_allowed=False」fail-closed 拒绝（`agent_provider.py:169-199, 324-341`），
属正确的治理语义；问题纯在前端"开关说谎"。

## 已定决策（grilling 两轮，全部采推荐）

| # | 决策 | 结论 |
|---|------|------|
| Q1 | 范围 | 轻量前端修复；PRD-only 轻量任务 |
| Q2 | 云同意开关语义 | 开关即保存（详见 R1） |
| Q3 | 聊天报错横幅 | 加跳转入口，仅 `canManageCurrentSpace` 为真时显示 |
| Q4 | 平台默认预填 | 保留预填，配合 Q2 成为两步快路径 |
| Q5 | steward assist 三开关 | 同样即时生效 + 守卫（详见 R2） |
| Q6 | provider/model 下拉 | 保留显式「保存选择」+ 未保存徽标（详见 R3） |
| Q7 | 跳转落点 | 管理页加 `?section=` query 同步，跳转直达 `?section=models` |
| Q8 | 错误文案 | 不区分「继承默认未同意 / 自选未同意」，保持单条 |

## Requirements

- **R1 云同意开关即时保存**：开关仅在所选 Provider 为云类型时渲染（现状）。
  翻动时若表单 provider+model 齐全 → 立即 PUT `cloud_allowed`（非乐观 UI：
  等待结果，成功后 toast + 状态行刷新为「自选：…已/未同意云端执行」；失败则
  开关回弹并 toast 错误）。表单未选全则开关回弹并提示「先选择模型」。
  刷新页面后开关状态必须与落库行一致（不再出现"翻开即失"）。
- **R2 steward assist 开关即时保存 + 守卫**：
  - 已有落库行 → 翻动立即 PUT 更新该行（同 R1 的非乐观与回弹语义）；
  - 无行但表单 provider+model 已选全 → 整行保存（连带开关状态）；
  - 两者皆不满足 → 三个开关禁用 + 提示「先配置管家模型」。
- **R3 下拉保持显式保存 + 未保存徽标**：provider/model 改动仍走「保存选择」；
  表单与落库行不一致时显示「未保存更改」徽标（位置：状态行旁或保存按钮上，
  实现自定），保存/恢复平台默认/停用后消失。无落库行时以表单非空（预填）
  不算"未保存"。
- **R4 报错横幅跳转**：`ErrorNotice` 的错误状态形状扩展一个结构化动作字段
  （实现自定，如 `action: { kind: 'open-model-settings' }`）。当错误为
  PROVIDER_UNRESOLVED + reason=cloud_not_allowed 时：`canManageCurrentSpace`
  为真 → 显示「去模型设置」入口，跳转
  `/spaces/{id}/manage?section=models`；非管理员不显示（纯文案，现状）。
- **R5 管理页 section 深链**：`SpaceManagementView` 挂载时读
  `route.query.section`（合法 key 才生效），切换 tab 时 `router.replace`
  写回，URL 可分享。
- **R6 平台默认预填保留**（现状不动）；「同意并启用平台默认」按钮保留。
- **R7 测试**：跟随仓库既有前端 spec 惯例，覆盖：开关即时保存成功/失败回弹、
  assist 守卫禁用态、未保存徽标出现与消失、ErrorNotice 跳转的权限两态、
  section 深链解析。

## 范围

- `frontend/src/components/member/SpaceModelSettingsPanel.vue`
- `frontend/src/components/agent/ErrorNotice.vue`（+ `stores/agent.ts` 错误状态形状）
- `frontend/src/views/SpaceManagementView.vue`（仅 section 深链同步）
- 对应 `__tests__` spec 文件

## 非目标

- 后端治理语义不动：云同意必须显式落库、fail-closed、可审计（保持现状）。
- 不做「继承默认/自选」两态信息架构重构。
- 不动系统管理员前端（5174）。

## 已确认的事实

- 管理页 sections key：`overview/members/invites/bridge/models/settings`；
  `activeSection` 现为纯本地状态。
- 角色判定：`spacesStore.canManageCurrentSpace`（AppShell 已用于「空间管理」入口）。
- 后端 PUT 语义（`api/space_model_settings.py`）：`enabled=true` 必须
  provider_id+model 成对；assist_* 仅 steward 维度、行级属性。
- 错误文案链：`stores/agent.ts:584` 已把 `detail` 传入 friendlyAgentError；
  `ErrorNotice.vue` 只收 `{code, message}`，需扩展错误状态形状（R4）。

## Acceptance Criteria

- [ ] AC1 翻开云同意开关且选择齐全 → 立即落库；状态行显示
      「自选：…已同意云端执行」；刷新页面后开关保持；失败时开关回弹并提示。
- [ ] AC2 本事故场景（预填默认 + 翻开关）无需寻找任何额外按钮即可打通聊天。
- [ ] AC3 steward assist 开关在无行且未选全时禁用并有提示；有行时翻动即时生效。
- [ ] AC4 下拉改动后出现「未保存更改」徽标，保存后消失；无落库行的预填不触发徽标。
- [ ] AC5 管理员聊天报错横幅出现「去模型设置」，点击直达模型设置 tab；
      非管理员看不到该入口。
- [ ] AC6 `/spaces/{id}/manage?section=models` 直接落在模型设置 tab；
      非法 section 值回退概览。
- [ ] AC7 R7 所列 spec 全部通过（vitest）。

## Notes

- Lightweight task：PRD-only。
- 实现层细节（ErrorNotice action 字段形状、开关 loading/回弹交互）由实现者
  在上述约束内自定，不再升级为用户决策。
