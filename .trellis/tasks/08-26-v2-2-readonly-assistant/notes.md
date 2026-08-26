# V2.2 注记

- “通用助手”在此阶段的通用性是对话编排，不代表拥有任意工具或联网。
- V2.3 前只能解释确定的结构路径；“舅爷爷”等完整地方称谓后置。
- RAG 能力在产品目标内，但 V2.5 前不能用原始聊天或伪 RAG 填空。
- 前端 SSE 不会自动复用 Axios interceptor，需要专门的 token/401/reconnect 设计。
- 全局入口挂 App.vue，避免绑死 FamilySpaceView；scope banner 必须始终可见。
