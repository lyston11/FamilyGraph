# B 设计：逐调用保全、混合批次恢复与总截止

## 1. 当前实现与复用点

主边界：`backend/app/services/steward_assist.py`，沿用 `_immediate_tx`、`_fence_check`、attempt 审计/产物校验、`_apply_batch` 和恢复器。`StewardModelCall` 已有 status、latency_ms、usage、billed_tokens、output_json，可先复用；`StewardAssistBatch` 已有 owner/attempt/deadline。禁止另建重复 attempt 数据模型。

源码 `62a2b15`：

- `execute_batch` 的 results 累积与循环后统一审计造成窗口。
- `_apply_batch` 在任何业务 fence 之前因同批 unknown/failed 提前返回。
- `recover_stuck_batches` 的 has_unknown 优先分支会使已持久化成果无法进入恢复应用。
- 当前测试明确保护“失租旧 worker 不能补审计”以及“审计持久化后只能 recovery 应用”；两者必须保留。

## 2. 首选执行节奏

```text
获得 batch 执行权 / 预留预算
  对每个 attempt：
    短事务重验执行权、策略、剩余时间 → 记录 in_flight → 提交
    无 DB 写事务地请求上游（固定总体截止）
    校验/安全错误分类
    短事务再次重验 owner + attempt + 实际 deadline
      → 保存此笔安全终态、usage、latency、合法产物 → 提交
  已无待发送请求 → 进入可收尾状态
  短事务：重验共同与目标栅栏 → 应用独立成功成果 + 聚合终态
```

执行时优先移动复用现有审计段，不为一个调用点创建通用工作流框架。网络异常路径也须走逐笔安全收敛，不能只保存正常返回。拿锁后重新校验时间；存储失败/失租不得吞掉后继续发下一笔。

总体请求截止早于 batch lease 到期，为保存与收尾留明确预算。但不能声称规避所有崩溃窗口：进程可能在收到上游结果后、提交前退出，这仍是 unknown；本次消除的是让已知结果人为等待后续 HTTP 的扩大窗口。

## 3. 混合结果与原子收尾

优先不增加 batch 状态枚举。所有发送结束/恢复收敛后，批次可以进入 applying；应用事务从 succeeded/degraded 中选择经既有校验且仍适用的产物，unknown/failed 不参与业务写回。

- 独立产物的写回与本批收尾状态应在同一事务中提交，防止“业务已应用但未记终态”反复执行。既有 candidate digest/CAS/幂等辅助保护保持。
- 所有请求合格且栅栏有效，沿用 applied；存在 transport failed/unknown，即使部分成果应用也仍记录 failed 和相应安全原因。不得把批次 failed 等同所有成果均未应用；后台观测以 attempt 和业务来源绑定解释。
- 共同栅栏失效则 superseded，不因部分成功绕过；目标级独立性以现有栅栏粒度为下限，不为提升成功率拆掉 group 校验。
- 空 `{items: []}` 是合法已检查结果，必须沿现有 `_mark_terminology_checked` 路径收敛；不能用 `items` 非空作为“是否完成过调用”的唯一判据。
- 独立性不成立（例如完整排列的一个子集、同一 group 的授权变化）则整个对应产物拒绝，保留审计和原因。

若实现核对发现现有状态/来源字段无法准确表达“部分成果已应用且批次失败”，先列消费者和最小字段变更，再更新设计；禁止不审视 admin metrics/recovery 就静默改变状态含义。

## 4. 恢复矩阵

| 输入 | 恢复动作 | 可应用与网络行为 |
|---|---|---|
| reserved，确定没发送 | 释放/跳过既有预留，沿原有限调度规则 | 不记上游费用；不能当已发送 unknown |
| in_flight，无持久结果 | unknown + 保守计费 | 不重发，无产物可应用 |
| succeeded + 非空安全 output_json | 保留终态与 usage，取得 recovery owner/attempt 后重验 | 应用合法产物，不发 HTTP |
| succeeded + 合法空 items | 保存已检查语义 | 不伪造改善，不重复请求 |
| failed/degraded，无合法产物 | 保留错误/预算证据 | 不应用不合法内容 |
| succeeded 与 unknown/failed 混合 | 先收敛未知，再为已知成果恢复应用 | 不重放 unknown；批次仍显失败事实 |
| 共同 fence 失效 | superseded，保留已保存审计 | 禁止应用 |
| 老执行者晚回 | 拒绝旧 owner/attempt/deadline | 不补写审计/投影，不抢回 lease |
| 已终态且无残留活跃 attempt | 无动作 | 重复恢复幂等 |

恢复器查询、应用器早退和终态聚合必须配套修改，不能只补一处。failed/superseded 旧批次残留 in_flight 的现行收敛行为需兼容，不批量复活历史批次来应用未经过新合同的成果。

## 5. 时限与取消设计

当前 `httpx.Client(timeout=...)` 是阶段/IO 等待限制，不足以证明总墙钟上界。规划选择“可取消的总截止 + 原有阶段 timeout”，不先写定异步化整个 worker。

实施前先用本地慢 chunk server 复现：每块小于 read timeout，整体超过总预算。核对已安装 HTTPX/Python 能力后选择最小可关闭请求的实现；现有有界 executor/独立 Session 保持，async HTTP 也不能把数据库 Session 带到另一线程共享。

必须给出三种边界的明确数值/公式并测试：单笔总体预算、最迟结束时点、保存/收尾预留。值先由锁等待与短事务基线选取，不随意新增配置项或只把租约调大。时间不足则停止发送后续项；绝对截止由 monotonic duration 约束，数据库 lease 仍使用权威 UTC 且重复检查。

禁止 `Future.result(timeout=...)` 后不取消底层网络；禁止每次收到字节就重置总截止；禁止无限延长 lease 让慢请求永占资源。上游可能继续处理已取消请求，故取消不能证明未计费，也不自动允许重发。

## 6. 兼容、风险与回退

优先零 schema 变更；若需要最小应用证据字段，旧行 nullable、读侧兼容、隔离迁移在前，序号以实施时 head 为准。无论有无迁移都跑四 kind 与 admin status/metrics 的状态语义测试。

逐调用短事务增多，需量化锁占用，不借机替换数据库。候选/排序/解释/称谓不全部共享目标独立性，要沿实际写回调用确认。历史 unknown 保持原样；本次性能和可靠性方案不自动解冻既有调用。

回退逐调用和恢复语义应作为配套变更；保留已存合法产物与审计，不清库、不降级为未发送。发布前模拟新产物由旧 reader 读取的兼容性，不能回滚后让旧恢复器重复发请求。

## 7. 需在编码前收敛的技术点

1. HTTPX 总截止的最小可取消实现及资源关闭证明。
2. existing `_apply_batch`/recovery/admin metrics 对 failed 的所有消费者。
3. 四 kind 的独立性、空结果检查与来源审计是否足够，不足时最小字段方案。
4. 保存预算如何覆盖实际 DB 锁等待，同时不破坏旧 worker 拒写。

以上属于后续执行前的实现选择，不是本轮已验证的解决方案。
