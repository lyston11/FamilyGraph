# Gate 2：事务入口计数的完整对账（65 个）

## 为什么必须对账

同一件事在本次任务里被数出三个不同数字，且**每一个都是错的**：

| 口径 | 计数 | 漏掉了什么 |
|---|---|---|
| 只数 `BEGIN IMMEDIATE` 字面量 | 18 | 全部经 helper 的调用点 |
| `immediate=True` + `_immediate_tx` | 43 | `write_transaction` 包装层 |
| AST 数调用表达式（未排除 helper 定义） | 46 | 同上，且把 helper 定义当成入口 |

**决定性口径**（`scripts/migration-proof/build_tx_entries.py`，显式排除 helper 定义）：

```
command_transaction(..., immediate=True)   20
_immediate_tx(db) 直接调用                  22
with write_transaction(bind)               23
──────────────────────────────────────────────
合计                                        65
```

## 为什么前两版会漏

`app/services/steward_pipeline.py::write_transaction` 是一个**包装层**：

```python
@contextmanager
def write_transaction(bind):
    with writer_turn(bind):
        with Session(bind=bind) as session:
            with _immediate_tx(session):
                yield session
```

它有 **23 个调用点**（`steward_delivery`、`steward_gc`、`steward_demand`、
`steward_pipeline`、`steward_overlay`）。只 grep `_immediate_tx` 或 `immediate=True`
时，这 23 个入口**完全不可见**——而它们承载的正是 Steward 的写回路径。

这不是记账问题：漏掉它们意味着 Gate 2 的锁序分析会**跳过 Steward 写回的全部入口**，
而那正是 `counter → run → attempt` 冲突最可能出现的地方。

## 结论

- **任何「N 处事务」的说法都必须注明口径**；数字本身不是证据，ID 清单才是。
- `tx-entries.json` 是唯一权威清单（65 条，含 form/path/line/function）。
- 分类必须覆盖全部 65 条；未分类即失败（脚本强制）。
