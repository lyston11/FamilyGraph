# 验收证据（2026-10-01）

## 缺陷

`default_allowlist("steward")` 把六个 steward 工具无条件放进**每个** run 的白名单，
其中 `get_viewer_target` / `get_viewer_term` 需要 `viewer_account_id` claim。
只有 terminology attempt 带该 claim（实测：44 个带 viewer 的 attempt 全是 terminology，
1315 个不带），因此 candidate / ranking run 的模型能看到、调用、然后被拒绝。

## 定位证据

生产日志（09-30 12:00 起）：

```
403 共 476 次，全部集中在这两个工具：
  284  get_viewer_target/execute → 403
  192  get_viewer_term/execute   → 403
横跨 60 个 run（228…331）
```

执行层判定是**正确**的（`STEWARD_VIEWER_TOOL_NAMES` → 403
`STEWARD_VIEWER_SCOPE_UNAVAILABLE`）；缺陷在白名单**广告了不可用的能力**。

### 排查过程中的一个错误

我最初用 `grep 'HTTP/1.1" 401'` 统计，得到「401/403 全为 0」的结论——**假阴性**。
日志 JSON 里的引号是**转义**的（`HTTP/1.1\" 401`），正确模式需要匹配转义形式。
这个错误一度让我以为 401 修复后已无认证问题；改用正确模式后才看到 476 次 403。
**教训：日志统计要用与格式一致的模式，并在已知存在的事件上先校准。**

## 修复

`default_allowlist` 增加 `viewer_scope` 参数（仅 steward 生效）；
child-run 构造点按 `attempt.viewer_account_id is not None` 传入。

- 执行层 403 判定**保留**（纵深防御）；
- 四个非 viewer 工具两种情况下都保留；assistant 白名单不受影响。

## AC 逐项

| 验收 | 结果 | 证据 |
|---|---|---|
| AC-1 | ✅ | 无 viewer → 不含两个 viewer 工具；`viewer_scope=True` → 含全部六个 |
| AC-2 | ✅ | 四个非 viewer 工具两种情况下都在；assistant 白名单与 `viewer_scope` 无关 |
| AC-3 | ✅ | `STEWARD_VIEWER_TOOL_NAMES` 的 403 判定未改 |
| AC-4 | ✅ | 部署后 **403 = 0**（修复前 476 次）；新 run 白名单 `viewer_tools=0 total=4` |
| AC-5 | ✅ | backend **1952 passed / 3 skipped**；ruff/mypy 干净 |

## 部署后实测（06:20 起）

```
403（全部）           : 0        （修复前 476 次）
get_viewer_target 403 : 0
get_viewer_term   403 : 0
工具调用成功          : 36
新 run 白名单         : viewer_tools=0 total=4（candidate/ranking）
steward run           : 335–340 全部 succeeded
expired               : 0
```

## 测试与变异验证

- `test_default_allowlist_omits_viewer_tools_without_a_viewer_claim`（新增）
- `test_assistant_allowlist_is_unaffected_by_viewer_scope`（新增）
- `test_registry_required_kind_gating`（**更新**）：该用例原本断言「六个工具总是全在」——
  那正是缺陷被写进测试，现改为固定真实规则。

变异验证：移除 `viewer_scope` 门 → 新用例失败。
（首次变异尝试因 ruff 已重排表达式而**静默未生效**，结果看似「通过」；
改为先确认变异确实应用后才取得有效结论。）

## 边界

- 未改动 candidate/ranking 是否需要 viewer 的产品决策（那是设计问题，不是本缺陷）。
- 线上未操作。
