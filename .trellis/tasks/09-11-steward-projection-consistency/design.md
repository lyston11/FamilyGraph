# Design — Steward 个人视图、称谓与失效传播修复

> 本文是待实施技术方案，不是当前代码行为；现状证据见父任务 findings。

## 数据流与职责

新增共享 event-impact resolver，`domain_events.emit` 用它同事务标记 PFV/相关建议失效、合并 Steward 水位。匹配实际事件名而不是依赖旧 spec 的 space_member 前缀。全局人物事件按该人物的 active membership/ref/桥接受权范围找空间；私人 memory/rag 事件保持不触发空间工作。

注册直接创建 claimed 账号是初始状态，不伪造一次 managed→claimed；以合法空间访问事件触发 ensure_view。为 active 带 Account 的目标建立 unique viewer_account_id/root/space 行，初始 queued/never_computed；只引用 provisional 的未登录人物不伪造 Account。

## 计算与读取

后台按输入版本调用已有 resolver 和 terms.resolve_term_or_structural；source_fact_ids、路径证据、词典版本、授权版本进入 input_hash。真正策略版本取统一 config/POLICY_VERSION，purpose 另存或仅在查询传入。

删除旧子行/重建/置 current 必须在同一事务或 savepoint 中；一份视图失败不会污染 Session 导致整个空间 rollback。并发计算采用 expected input revision CAS，旧计算不能覆盖新 stale。

GET 首先验证访问与输入版本；stale/failed 返回状态和安全空内容，触发工作由显式服务命令短事务登记，GET 不在序列化时改 ORM display_json。若保留已有首次读物化，必须显式提交且测试跨会话持久化；优先迁到后台初始化。

## 兼容性与回滚

保持 /personal-family-view、/family-recommendations 的响应主体和错误 envelope；新增状态原因枚举需同步 decoder。旧 policy_version='graph' 与没有词典版本的 view 标 stale 并排队，不假装有效。功能回滚仅关闭新计算；读取授权复核不能回滚为宽松旧路径。
