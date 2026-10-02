# 验收证据（2026-10-02）

## 根因

`policy_guard` 的 PII 电话号码正则在长输入上**超线性回溯**，而它跑在**事件循环**上，
每次 provider 请求都执行。payload 越过阈值后检查耗时超过 10s connect 预算，
请求在连接阶段超时并被记为 `transport_timeout`。

```
10 KB ->  522ms      34 KB ->  6.0s
18 KB ->  1.7s       66 KB -> 23.4s        4x 输入 -> 15.7x 时间
真实 steward payload（17.8 KB）: before_provider_request 1.03s
```

py-spy 现场（故障进程采样 200 次）反复显示 MainThread 卡在
`contains_pii → classify → before_provider_request → stream_provider_response`，
**从未到达网络层**；`connect` 相关栈 0 处。

详见 `root-cause.md`。

## 修复

用**一次线性扫描**替代正则，保留「≥10 位数字」判据：

```
66 KB: 23,381ms -> 1.9ms        256 KB: 8.3ms（线性）
before_provider_request（真实 payload）: 1.03s -> 2.7ms
```

**附带修复**：`logger.exception()` 的 traceback 从未被输出（`JsonFormatter` 把
`exc_info` 当内部属性跳过）——这是本故障排查的最大障碍，也解释了为什么 09:02 的
异常只知道「哪个路由抛了」。

## AC 逐项

| 验收 | 结果 | 证据 |
|---|---|---|
| AC-1 | ✅ | 超线性正则 + 事件循环路径 + 10s connect 预算三者因果闭合；py-spy 从未到达网络层 |
| AC-2 | ✅ | 部署后 **10/10 egress succeeded**；run 439–443 全部 `succeeded` |
| AC-3 | ✅ | traceback 现在输出；字段白名单回归保证无敏感数据 |
| AC-4 | ✅ | 复杂度回归（4x 输入不得 ~10x 时间）；变异验证：还原旧正则 → 失败 |
| AC-5 | ✅ | backend **1958 passed / 3 skipped**；ruff/mypy 干净 |
| AC-6 | ✅ | egress/Provider 合同回归通过（全量含相关用例） |

## 部署后实测

```
最近 30 分钟 egress: total=10 succeeded=10 failed=0
run 439 succeeded  06:28:49 -> 06:30:16
run 440 succeeded  06:28:57 -> 06:30:58
run 441 succeeded  06:58:07 -> 06:58:43
run 442 succeeded  06:58:50 -> 06:59:01
run 443 succeeded  06:58:50 -> 07:04:00   ← 5 分钟长 run 也成功
```

修复前该窗口为 `ok=0 fail=51`（持续 18.8 小时零成功）。

## 变异验证

| 变异 | 结果 |
|---|---|
| 还原旧超线性正则 | 复杂度断言失败（4x 输入 → 15.8x 时间） |
| 移除电话检测 | 检测能力断言失败（漏检 `13800138000`） |

## 排查中的两次错误（已纠正）

1. **误认 SSL context 为根因**：py-spy 抓到 `ssl.create_default_context` 帧，
   但实测仅 26.6ms——httpx 对 `http://` 也建 SSL context，属正常开销。
2. **首次简化正则丢了判据**：写成 `\+?\d(?:[\d -]{8,}\d)`，丢掉「≥10 位数字」，
   使 `1970-01-01` 被误判为电话并脱敏，破坏了 lineage 生日投影——
   被既有测试 `test_six_tools_contract_via_internal_endpoint` 捕获。

## 边界

- 未在故障进程上直接注入探针验证「修复后同一进程恢复」；证据是部署后新进程
  恢复正常（100% 成功）。
- 阈值的确切触发 payload 未回溯定位（历史 payload 未留存）；但规模曲线与
  10s 预算的关系已量化。
- 线上未操作。
