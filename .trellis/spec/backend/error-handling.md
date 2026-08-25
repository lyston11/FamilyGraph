# 错误处理规范（初始规范 v0）

- 统一错误响应结构：`{"error": {"code": "MACHINE_CODE", "message": "用户可读文案", "detail": {...可选}}}`。
- 全局 exception handler 分类：HTTPException(业务) / ValidationError(422 保持 FastAPI 默认外壳但映射 code) / 未捕获异常(500 + 日志，不向客户端泄露堆栈)。
- 业务错误码常量表集中在 `app/errors.py`，如 AUTH_INVALID_CREDENTIALS、PIN_CHANGE_REQUIRED、RELATION_CYCLE_FORBIDDEN、VISIBILITY_MASKED。
- 认证失败永远返回同一文案"名字或 PIN 码错误"，不区分账号不存在/PIN 错误（防枚举）。
- 可见性遮罩不是错误：被遮罩字段返回 MASKED 标记结构而非抛错（前端渲染锁样式）。
- FSM 非法转换返回 409 + 当前状态信息；环检测拒绝返回 422 RELATION_CYCLE_FORBIDDEN。
- 服务端日志记录完整上下文，客户端响应永不包含内部细节。
