# 策略边界研究摘要

## 结论

当前确定问题是机制缺陷，不能把 run 48 的历史原因当成已查明。sidecar 对任何消息的自然语言标记词做硬阻断，且会扫描自身生成的含 `masked` 提示；worker 多个原因落入密钥错误兜底，缺少类型/阶段诊断。后端输入/工具参数也有关键词硬拒绝，单侧修复无效。

## 选择

沿用服务端授权和数据块合同，把关键词命中降为有界 notice；确定的权限、密钥、Provider、数据契约违规继续 fail closed。来源用于定位与选择字段，不授予信任。硬阻断状态在整个 run 内保持，停止后续模型/工具调用，正常/catch 共享错误映射。诊断仅含固定枚举和安全关联信息。

## 实施影响

主要涉及 agent policy/worker/session、backend policy_guard 和前端错误文案。先交付分类与安全诊断，再交付两侧判据和阻断生命周期。使用现有协议，无新数据库或通用策略配置。前置验证真实 SDK 的终止/自动重试，不能只测 mock handler。

## 不变约束

- 权限、可见性、run/attempt fence、工具闭合 schema、Provider 和产物校验仍由 FastAPI 兜底。
- assistant 输出不可信；全角色出站密钥检查不可豁免，token cap 类型不可误伤。
- 必需数据块结构和服务端受限标记仍 fail closed；正文伪造 metadata 无效。
- 手动阅读 `.trellis/spec/backend/agent-runtime.md` 的协议、执行终态与 Provider 相关章节：该叶文件超过注入单文件上限，未默认注入，不能依赖被截断的版本。`error-handling.md` 的全边界 fail-closed 是安全约束，runtime 对现有浏览器输出缺口的说明是实现事实；二者都不能被解读为本任务已补齐输出侧 DLP。
- 不保存 prompt/thinking/密钥/匹配片段/正文指纹，不宣称补齐浏览器输出侧 DLP。
- 仅开发环境验收；线上由用户手动发布。

## 何时读完整证据

修改关键词拒绝语义、质疑结构化 masked 合同、设计新日志字段或试图归因 run 48 时，先读 `evidence/policy-boundary-investigation.md`。本摘要不是已实施/已通过测试的证明。
