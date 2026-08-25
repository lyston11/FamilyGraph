# 日志规范（初始规范 v0）

- 结构化日志（python logging + JSON formatter），字段：ts, level, logger, msg, user_id(若有), request_id。
- 中间件注入 request_id（uuid4），贯穿单次请求全部日志行。
- **脱敏红线**：PIN（任何形式）、JWT、pin_hash、challenge_token、refresh token 永不入日志；姓名/生卒等 PII 只允许出现在 audit_log 表，不进应用日志。
- audit_log（数据库表，非文件）：login_failed(≥3 次)、pin_change/reset、admin 全部操作、档案删除、关系断连。仅 admin API 可读。
- 级别约定：ERROR=未预期异常/DATA_LOSS 风险；WARNING=限流触发/FSM 非法尝试/孤儿文件清扫；INFO=登录成功/建档/空间变更；DEBUG 默认关闭。
- 上传图片删除失败记 WARNING 并进入清扫清单，不阻塞主流程。
