# 重做 Agent 模型服务商配置入口

## Goal

参考 LearnGraph 配置卡片，将后台模型治理页改为可直接开始填写服务商连接信息和模型，并改善空状态与默认模型配置流程。

## Requirements

- 在没有已注册 Provider 时，平台默认模型区必须提供清晰的“添加服务商”入口，用户无需理解“注册表”概念即可开始配置。
- Provider 表单按模型服务商配置心智呈现：名称、兼容接口、Base URL、API Key（只写）和模型 ID/白名单均可填写，并解释模型 ID 的格式。
- 平台默认配置保留已注册 Provider 的选择约束，同时模型字段支持直接填写模型 ID；已注册模型以 datalist/提示辅助，不把用户锁死在下拉选项中。
- 保存默认模型时沿用现有后端 provider_id + model 合同；不改变密钥脱敏、权限、审计或空间 owner 配置语义。
- 参考 LearnGraph 的分步配置卡片信息层级，减少空列表、禁用下拉造成的“无法配置”错觉。

## Acceptance Criteria

- [x] 空 Provider 状态中存在可见的添加服务商操作，并能打开完整 Provider 表单。
- [x] Provider 表单中服务商与模型字段可直接填写，提交载荷与现有 API 合同一致。
- [x] 平台默认的模型输入可填写不在已有 allowlist 选项中的文本，提交仍发送正确 provider_id/model。
- [x] 现有 Provider 注册、编辑、平台默认保存和错误展示测试继续通过；新增交互有组件测试覆盖。
- [x] system-admin-frontend lint、type-check、相关 Vitest 通过。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
