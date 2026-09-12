# Agent Note: Steward 事件窗口复用统一空间作用域解析

Status: implemented

## Problem

`_consume_window` 通过 `space_id IS NULL` 宽读所有全局事件。当前这些事件只影响
`events_consumed` 统计和窗口水位，但与调度入口已经采用的空间影响合同不一致，
也为未来使用 `touched_users` 时引入跨空间误处理风险。

## Decision

事件窗口读取沿用 `domain_events.resolve_event_space_ids` 作为唯一空间作用域合同：
显式空间事件只进入对应空间；无空间事件仅在解析结果包含当前空间时进入；memory/RAG
事件不属于 Steward 窗口。保留事件 ID 上界推进语义，避免历史无关事件反复扫描。

## Alternatives considered

- **在 SQL 中复制人物 membership/ref/bridge 条件**：可减少 Python 过滤，但会复制权威解析逻辑并在事件类型扩展时漂移，放弃。
- **保持当前宽读**：暂不影响业务写入，但统计和消费合同继续不一致，未来复用 touched_users 时风险扩大，放弃。
- **复用 `resolve_event_space_ids` 并在窗口内逐事件过滤**：不增加新的授权规则，直接复用已验证合同，采用。

## Consequences

窗口现在只返回当前空间受影响事件；无关全局事件不计入 `events_consumed`，
memory/RAG 事件也不进入 Steward 窗口。事件 ID 上界仍按作业推进，避免无关历史
事件在后续窗口反复扫描。逐事件解析会增加窗口读取的查询次数；窗口有界且解析
逻辑可继续优化为批量查询，先保证合同一致性。
