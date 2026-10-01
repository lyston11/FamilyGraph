# Steward viewer 工具被无条件授予，无 viewer 的 run 调用必 403

## 问题

`agent_tools.default_allowlist("steward")` 把六个 steward 工具**无条件**放进每个
run 的白名单，其中包括需要 `viewer_account_id` claim 的
`familygraph.steward.get_viewer_target` 与 `familygraph.steward.get_viewer_term`。

但只有 **terminology** attempt 带 viewer claim（实测：44 个带 viewer 的 attempt 全是
terminology；1315 个不带）。因此 **candidate / ranking** run 的模型能看到并调用这两个
工具，然后必然被拒绝。

## 证据

生产日志实测（09-30 12:00 起）：

```
403 共 476 次，全部集中在这两个工具：
  284  POST /runs/{id}/tools/familygraph.steward.get_viewer_target/execute  → 403
  192  POST /runs/{id}/tools/familygraph.steward.get_viewer_term/execute    → 403
横跨 60 个 run（228…331），涉及 assist_kind: candidate 6 / ranking 6（样本）
```

执行层是**正确**的：`agent_tools` 里已有

```python
if spec.name in steward_tools.STEWARD_VIEWER_TOOL_NAMES and execution.viewer_account_id is None:
    raise ToolProtocolError(403, "STEWARD_VIEWER_SCOPE_UNAVAILABLE", ...)
```

缺陷在**白名单**：它向模型广告了一个该 run 无法使用的能力。代价不只是 403 计数——
模型会花轮次去发现这个拒绝，而这些轮次本可用于实际任务。

## 需求

### R1 不得广告不可用的能力

steward run 的 `tool_allowlist` 必须与该 attempt 的实际能力一致：
带 viewer claim 的 attempt 拿到两个 viewer 工具；不带的**不得**拿到。

### R2 授权判定不变

只改**白名单内容**，不改执行层判定。`STEWARD_VIEWER_TOOL_NAMES` 的 403 必须保留——
它是纵深防御：即使白名单出错，越权调用仍被拒绝。

### R3 不误伤其他工具

四个非 viewer 工具（`get_space_snapshot`、`list_space_nodes`、`get_evidence`、
`get_relationship_path`）在两种情况下都必须保留。assistant 白名单不受影响。

## 验收

| ID | 可观察结果 |
|---|---|
| AC-1 | 无 viewer claim 的 steward allowlist 不含两个 viewer 工具；有 viewer claim 时含全部六个 |
| AC-2 | 四个非 viewer 工具在两种情况下都在；assistant 白名单不受 `viewer_scope` 影响 |
| AC-3 | 执行层 403 判定保留（纵深防御未被删除） |
| AC-4 | 部署后 `get_viewer_target`/`get_viewer_term` 的 403 降为 0（candidate/ranking run 不再调用） |
| AC-5 | backend 全量 pytest / ruff / mypy 通过 |

## 不在范围

- 改执行层的 viewer 判定或 `STEWARD_VIEWER_TOOL_NAMES` 语义。
- 给 candidate/ranking 增加 viewer claim（那是产品决策，不是缺陷修复）。
- 线上操作。
