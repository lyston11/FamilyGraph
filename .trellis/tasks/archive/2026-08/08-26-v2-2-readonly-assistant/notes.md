# V2.2 注记

- “通用助手”在此阶段的通用性是对话编排，不代表拥有任意工具或联网。
- V2.3 前只能解释确定的结构路径；“舅爷爷”等完整地方称谓后置。
- RAG 能力在产品目标内，但 V2.5 前不能用原始聊天或伪 RAG 填空。
- 前端 SSE 不会自动复用 Axios interceptor，需要专门的 token/401/reconnect 设计。
- 全局入口挂 App.vue，避免绑死 FamilySpaceView；scope banner 必须始终可见。

## trellis-check 结论与移交（2026-08-26）

PASS。发现项 #1/#2（错误码文案映射）已当场修复；移交 V2.3：

- session.ts buildRunSession 对所有 agent_kind 无条件使用 ASSISTANT_SYSTEM_PROMPT；steward 领域化时须按 projection.agent_kind 分派提示词。
- AC-AS1 的 375px 移动端真机走查未做（与 v1 遗留项一致），自动化已覆盖 <768px 断点分支。
