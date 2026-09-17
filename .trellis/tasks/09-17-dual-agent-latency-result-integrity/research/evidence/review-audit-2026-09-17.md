# 2026-09-17 双 Agent 复核证据

基线：main@d8d3668。来源：本次会话实际源码/只读服务核查，以及保留在本机`/tmp`的合成审计反例和日志。本文件固化结果以免临时文件丢失；不是又一次运行记录。无生产写库、部署、真实模型调用。

## 五个红测

命令（历史实际运行）：

```bash
cd backend
PYTHONPATH=tests:. .venv/bin/pytest -q -s -p conftest /tmp/test_fg_latency_audit.py
```

结果：`5 failed, 21 warnings in 2.52s`。该文件是审计探针，不在正式tests中，失败证明旧声明不成立；后续C/D须改为符合新合同的正式回归，不能简单断言created_at本身应变成执行时刻。

| 测试 | 构造 | 实际输出/失败 |
| --- | --- | --- |
| test_total_deadline_interrupts_delayed_body | loopback ThreadingHTTPServer，headers延迟0.30s，body再延迟0.30s；调用_post_json预算0.40s | elapsed=0.638s，ReadTimeout；`elapsed < .50`失败 |
| test_retry_count_single_failure_then_success | 合成run，failed502@3s、succeeded200@10s | failed_attempts=1，runs_with_provider_retry=0 |
| test_retry_window_retains_exhausted_failures | failed502@3/6/10s，无成功结束 | failed_attempts=3，provider_retry.n=0，runs_with_provider_retry=0；预期失败间隔下界7000ms未保留 |
| test_runs_without_events_remain_in_denominator | 合成run存在，但无事件 | runs=0、runs_without_start=0 |
| test_batched_tool_events_preserve_measured_duration | start事件后sleep0.12s，end事件一并append | actual=0.125s，persisted_delta=0.001301s |

计数探针只证明当前算法的形状盲区；没有turn/request关联的历史数据，无法无歧义区分真实retry与下一轮正常请求。D必须解决关联或标unknown，不把这些简化fixture当历史准确重试的证据。

## 正式回归与源码发现

本轮审核已有后端定向79 passed（41.76s）、sidecar定向42 passed。它们未覆盖上述边界；不是修复后的结果。原全量1648/1649等记录属于旧提交时点，不在此复跑或改写。

- `backend/app/services/steward_assist.py`：_post_json chunk后才检查monotonic；execute_batch timeout未扣结算预留。
- `agent/src/worker.ts`、`events.ts`、`backend/app/services/agent_events.py`：context/session在SDK agent_start之前；事件批量发送后created_at落在insert时刻。
- `backend/app/api/admin_agent_latency.py`：以事件分组统计run、从失败段聚合重试，尾部失败/单失败存在漏计；纯工具轮与压缩不能由正文事件完整恢复。
- `backend/app/services/provider_proxy.py`：所有上游>=400对sidecar泛化502；部分HTTPError分支无egress审计。
- 项目SDK session默认retry启用、maxRetries=3；另有pi-ai请求层配置。20s maxRetryDelayMs不应当作所有退避的通用上限。尚未验证每类错误如何叠加，E/F负责。

## 只读远端快照

```text
HEAD d8d3668 2026-09-17T18:03:37+08:00
familygraph-api.service active
ExecMainStartTimestamp Thu 2026-09-17 04:52:21 UTC
WorkingDirectory /home/ubuntu/projects/FamilyGraph/backend
familygraph-agent.service active
ExecMainStartTimestamp Wed 2026-09-16 05:14:53 UTC
WorkingDirectory /home/ubuntu/projects/FamilyGraph/agent
```

线上OpenAPI未包含assistant_phases。进程启动早于修复且行为仍旧，足以否定“代码同步即运行生效”；执行G时需重新核对，不能拿此旧快照断言届时状态。

三次SSH时钟采样偏差区间交集约[-0.25s,+0.34s]，未发现几十秒级偏差，但不能证明毫秒级跨机计时准确。真实代理chunk与浏览器渲染仍未测。

## 后续复现要求

C使用真实socket覆盖headers/body阻塞，并同时检查客户端资源结束；D走真实append与源duration，分清持久时间不改语义；E真实SDK配本地上游验证请求数与错误类别；F覆盖真实backend/SSE/browser。全部显式隔离DATA_DIR，保留安全时长/计数，不保存真实正文或密钥。临时探针若已不存在，可按上表重建正式回归，不依赖`/tmp`作为长期验收入口。
