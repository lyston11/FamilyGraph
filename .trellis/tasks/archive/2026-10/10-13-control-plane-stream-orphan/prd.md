# 修复在途流被误报为孤儿占用导致 /api/ready 恒 503

## Goal

修复 `capacity_bootstrap.reconcile` 把**在途 SSE 流**的容量计数行误判为「孤儿占用」
的缺陷。该误判让 `/api/ready` 在**每一次有流在途时**返回 503。

## Background

在开发环境实测控制面尾延迟时发现 `/api/ready` 持续返回 503：

```json
{"status":"ok","writer_stage":"pg_all","capacity_ready":false,
 "capacity_error":"pg_all 阶段容量计数行未就绪：孤儿占用 global:0:provider_stream
   计数行 active=1; 孤儿占用 space:2:provider_stream 计数行 active=1。..."}
```

根因：`_active_counts` 只为 `assistant_run`(account)、`steward_job`(space)、
`steward_assist`(space) 建 bucket，**从不**为 `provider_stream` 建。
而 `reconcile` 的孤儿判据是「计数行 active>0 且 expected==0」。

危害方向与直觉相反：**不是「泄漏被漏报」，而是「正常占用被误报为泄漏」**。
SSE 流可持续几十秒，因此这段时间里整个实例的就绪探针都是失败的——
编排器/负载均衡会把一个完全健康的实例摘除。

## Requirements

- R1 `provider_stream` 不参与孤儿对账（该维度无数据库可查的真实来源）。
- R2 仍受两道保护：`assert_ready` 的存在性检查、运行时行锁 + `CHECK (active <= capacity)`。
- R3 泄漏仍可被发现：`bootstrap` 会把无真实活跃的行归零。
- R4 其他维度（`steward_job` / `steward_assist` / `assistant_run`）的孤儿检测**不得**放宽。

## Acceptance Criteria

- [ ] 有在途流时 `reconcile` 不报 provider_stream 孤儿。
- [ ] 有在途流时 `assert_ready(stage="pg_all")` 通过（端到端）。
- [ ] 其他维度的孤儿检测仍失败（`test_reconcile_detects_orphaned_occupancy` 保持通过）。
- [ ] 反证：移除该例外后新测试失败。
- [ ] 全量测试全绿。
