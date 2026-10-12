# C2：counter 引入后的锁序验证

## 结论

引入 counter 后重跑静态调用图：**`entries_violating_frozen_order = 0`**，
且 `counter_implemented = true`。这正是 `10-05` 留下的回归基线要求。

关键入口的实际锁序（按源码行序，不是按函数边界）：

| 入口 | 行锁序列 | 判定 |
|---|---|---|
| `_settle` | `counter → run_row` | 正确（归还早于 fence） |
| `request_cancel` | `counter → run_row` | 正确 |
| `lease_attempt` | `counter` | 正确 |
| `enqueue_run` / `submit_user_message` | `counter` | 正确 |
| `enqueue_steward_job` / `settle_steward_job` | `counter` | 正确 |
| `recover_stuck_attempts` | `counter` | 正确 |

`_settle` 从 `run_row → counter`（错误）变为 `counter → run_row`（正确），
证明「把归还移到 fence 之前」的修复在静态图上可见。

## 工具本身修了三个缺陷（都导致假结论）

这类「分析工具给出错误结论」比被测代码的缺陷更危险——它会让人改错地方。

### 1. 先收集自身锁、再下钻被调函数 → 顺序颠倒

初版把函数自身的锁全部先收集，再按调用顺序下钻。当**被调函数的锁出现在自身锁之前**
时顺序就反了：`_settle` 在 475 行调 `release_account_run`（counter），478 行调
`fence_execution`（run 行），却报成 `run_row → counter`，把**正确**顺序误报为反向。

修法：把自身锁与被调点放进同一张按行号排序的表，按序处理。

### 2. 按函数名解析被调方 → 凭空造边

`execute`、`_snapshot`、`load_input` 等名字在多个模块都存在。只按名字匹配会造出
跨模块的假调用边——实测把 `lease_attempt` 经一连串同名函数连到
`fence_assistant_execution`，于是报出 **8 个假的反向锁序**。

修法：只承认两类解析——**同一文件内定义**、或**本文件显式 import** 进来的名字；
其余视为外部调用，**不跟随**。图因此是「不完整但可靠」，而不是「完整但可能错」。

### 3. 模块别名未解析 → 漏掉真实的 counter 锁

`from app.services import capacity` 之后 `capacity.release_account_run(...)` 是**属性调用**。
初版只取 `.attr`（`release_account_run`），但 `LOCK_SOURCES` 里没有这个名字，
于是 `_settle` 的 counter 锁**完全没被记录**。

修法：识别 `from app.X import Y` 中 Y 是子模块的情况，编码为 `模块路径::函数名`；
锁点判定用裸函数名。

## 方法学结论

**静态分析工具必须先自证**：一个报假阳性的锁序检查会让人去「修」正确的代码。
本工具的三个缺陷都是靠**与已知事实对照**发现的——`_settle` 的源码顺序是明确的
（475 行 counter、478 行 fence），图上却相反，因此是图错了。

## 证据等级

**L0**（AST 静态调用图）+ 与 `10-05` 已实测的 L3 死锁探针一致。
反证：把 `release_account_run` 从 `_settle` 的 fence **之后**调用，图会重新报出
`run_row → counter`——因此该检查有判别力。
