# 实施计划：Provider 长流可靠性与上游故障隔离

## Phase A：现场基线

- [ ] 从 egress 审计量化 connect/header/stream/backpressure、重试、连接持有和长流并发。
- [ ] 关联 upstream/provider/kind/tenant，但不输出凭据、prompt 或正文。

## Phase B：流级资源

- [ ] 建立 stream-level account/space/kind/upstream/global capacity 和总 wall-clock deadline。
- [ ] 验证建连名额在流开始前释放、长流不占 control、取消/失租立即停止。

## Phase C：故障隔离

- [ ] 实现按 upstream/profile/kind/tenant 的 circuit 状态和有界退避。
- [ ] 注入 DERP 不可达、DNS/TLS、header timeout、stream reset、backpressure、cancel。
- [ ] 验证不同 provider/kind/tenant 不互相熔断，egress/sent/settle 合同不变。

## Gate

- [ ] 长流多租户 p95/p99 和资源上界有真实证据；不得用单次成功替代尾延迟验收。
