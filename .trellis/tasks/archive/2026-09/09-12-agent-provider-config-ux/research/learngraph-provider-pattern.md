# LearnGraph 本地实现参考

来源：`/Users/lyston/PycharmProjects/LearnGraph/frontend/src/features/settings/provider-pages.tsx`

- `1587-1589`：Provider 列表为空时直接提示点击“新增 Provider”开始配置。
- `3265-3280`：新增 Provider 使用独立配置对话框，并说明快捷项只预填厂商信息，API Key 仍需管理员填写。
- `3310-3375`：快捷接入卡片先选厂商/协议，再填写显示名称。
- `3438-3475`：Base URL 与 API Key 是独立的可填写字段，带有格式和密钥说明。
- `4167-4189`：模型列表提供“手动输入模型名称”入口，允许厂商未发现的新模型或私有模型。

FamilyGraph 后端仍要求 `provider_id + model` 的平台默认合同，因此本任务将该交互映射为：Provider 注册表负责创建服务商，平台默认保留服务商选择，同时新增可填写的模型 ID 输入。
