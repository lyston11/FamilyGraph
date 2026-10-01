# 日志规范（初始规范 v0）

- 结构化日志（python logging + JSON formatter），字段：ts, level, logger, msg, user_id(若有), request_id。
- 中间件注入 request_id（uuid4），贯穿单次请求全部日志行。
- **脱敏红线**：PIN（任何形式）、JWT、pin_hash、challenge_token、refresh token 永不入日志；姓名/生卒等 PII 只允许出现在 audit_log 表，不进应用日志。
- **管理员凭据红线（09-04 沉淀）**：admin 初始密码/恢复密码（含 0600 凭据文件内容）、admin password_hash、`ADMIN_JWT_*` 配置值、admin access/refresh token 永不入应用日志、审计正文或 HTTP 响应；凭据文件删除失败只记无明文的安全告警（`admin_credential_file_delete_failed`）。bootstrap/恢复路径有专测 grep 日志断言（`test_no_pin_or_token_leaks_in_logs`）。
- audit_log（数据库表，非文件）：login_failed(≥3 次)、pin_change/reset、admin 全部操作、档案删除、关系断连。仅 admin API 可读。
- 级别约定：ERROR=未预期异常/DATA_LOSS 风险；WARNING=限流触发/FSM 非法尝试/孤儿文件清扫；INFO=登录成功/建档/空间变更；DEBUG 默认关闭。
- 上传图片删除失败记 WARNING 并进入清扫清单，不阻塞主流程。

## `extra` 字段必须被输出（2026-10-01）

`JsonFormatter` 原先只输出固定的六个键（ts/level/logger/msg/user_id/request_id），
**静默丢弃所有 `extra=`**。后果不是「少了个字段」而是**诊断失效**：
`event_loop_lag` 触发 4 次却看不到 `lag_ms`，`maintenance tick` 的 `counters` 一直为空——
日志显示「事件发生了」，却不显示任何数值。

- 调用方通过 `extra=` 附带的字段**必须**出现在 JSON 行里；
- 嵌套结构（如 `counters`）原样保留；
- `LogRecord` 内部属性（`levelno`/`pathname`/`lineno`/`args`/`exc_info`）不得外泄；
- `extra` 优先于基础投影（调用方显式给出的字段比默认更具体）。

回归：`tests/test_logctx_extra.py`。变异验证：移除 extra 输出循环 → 用例失败。

**新增诊断/计数器日志时**：字段名用安全标识，值用数值或计数；
不得输出 SQL 文本、参数、正文、token、凭据（本文件顶部红线不变）。
