# 技术设计：多租户容量观测与故障验收

## 1. 负载矩阵

使用二维租户矩阵：Assistant 按 account，Steward 按 space；另加 kind、upstream、control/background。每个场景同时有正常、过载、取消、重启和恢复组。

## 2. 指标

以 request/run/attempt 为单位记录 admission wait、pool wait、header/stream、tool wait、retry、heartbeat latency、lease outcome、DB transaction age、saturation 和终态。历史数据与实时 source clock 分开，不用持久事件间隔伪造精度。

## 3. 故障注入

注入 provider/DERP、PostgreSQL、Redis、sidecar 和 worker 级故障，验证哪些资源仍可用、哪些应 fail-closed，以及恢复后是否有重复 lease/settle、孤儿 counter 或半切换索引。

## 4. 判定

每项验收同时检查性能上界、隔离性、正确终态、审计数量、授权边界和数据对账；单 run 成功只能作为 smoke，不得作为 acceptance。
