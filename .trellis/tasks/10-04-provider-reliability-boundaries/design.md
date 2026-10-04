# 技术设计：Provider 长流可靠性与上游故障隔离

## 1. 分阶段预算

Provider admission 分为：授权/配置解析、DNS/TCP/TLS 建连、header、stream、consumer backpressure。建连名额在 stream 开始前释放；长流另有 `account|space + kind + upstream + global` stream capacity 和 wall-clock deadline。

## 2. Circuit breaker

circuit key 为 `provider_profile + agent_kind + tenant scope`，默认按 upstream/kind 隔离，必要时再按 tenant 分桶。状态只影响新请求；已有流按 cancel/deadline 收敛。打开 circuit 不写业务终态，不绕过 gateway，不影响 control-plane。

## 3. 连接生命周期

测试 HTTP client 复用、DNS/TLS、连接池、流中取消、per-chunk gate、backpressure 和审计。任何 client 复用不得跨 chunk 持有请求级 DB Session，也不得让一个 provider 流阻塞其他 upstream。

## 4. 语义

保持 `sent`、每次真实 egress 一条审计、unknown/保守计费、cancel/lease-lost 和永久上游 4xx 不重试。连接建立后中断不能被误标成未发送。
