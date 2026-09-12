# Design — Steward 模型调用事务隔离、预算与崩溃恢复

> 本文是待实施技术方案，不是当前代码行为；现状证据见父任务 findings。

## 核心三阶段

1. 短事务提交 core；在同事务登记 `StewardAssistBatch(job_id UNIQUE, space_id, evidence_hash, policy_version, status, next_attempt_at, lease_owner, lease_until)`。批次是 job 的辅助子阶段，不能被 sidecar lease。
2. 短事务选中一个可执行批次，建立模型调用预留行并提交；按当前政策在内存构建安全输入后释放连接，在独立受限执行线程/任务进行 HTTP。
3. 以新 Session 开短事务重新验证证据和授权，按 CAS 应用可接受产物、更新审计/批次状态。模型返回对象不能直接 ORM merge。

StewardJob `succeeded` 仅表示确定性结果完成；辅助状态由 batch 展示。maintenance 先泵 core 再调度最多一个辅助批次；network executor 有上界、不共用 Session，不连续阻塞 core tick。

## 调用状态与幂等

扩展 `StewardModelCall` 或关联 Attempt：reserved → in_flight → succeeded/failed/degraded/skipped/unknown。唯一标识 `(job_id, assist_kind, subject_key, input_hash, attempt)`；subject_key 为候选批次、收件人排序批次或 card id+revision。调用原始 payload 不落库，保存 digest、长度、预算和快照版本。

预留前检查该 job 所有发送尝试与 token 上限；响应验证失败仍保留已经花费的 usage。输入 token 使用当前模型可靠估算；未知模型则用保守上界，输出 cap=min(辅助上限,剩余预算)。usage 缺 total 时用 input+output，缺全部时按预留不释放。HTTP body 用流式字节上限读入再解析 JSON。

无法证明上游未处理的网络断开记 unknown。上游明确支持幂等键才自动复用请求标识重试，否则停止本批自动重发并报告；本地最终产物仍按 evidence/digest 去重。明确不承诺跨外部 HTTP 的 exactly-once 计费。

## 事务外后的安全约束

快照含 policy/provider/setting revision、evidence hash、受众、card revision；发包前检查一次，返回应用前再检查一次。不能因移出事务而引入 TOCTOU 漏洞。每个 auxiliary point 的异常以独立短事务保存 safe_error_code，禁止把 exception repr/完整上游 body 写日志。

## 迁移/回滚

新增 batch/attempt 迁移跟随 production-ops。旧 succeeded audit 保留；旧无 evidence 的辅助不自动重放。关闭 assist worker 和三类辅助开关后，核心继续。回滚需停止辅助执行者并确认无 lease；不得删历史计费记录来“恢复预算”。
