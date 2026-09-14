# Steward 行为投影重建

## 1. 适用范围

修改 `steward.rebuild_behavior_projections`、增加可重放事件或扩展行为投影键时读取本合同。该重建器只拥有能由自身事件处理器恢复的键族；全局写入白名单不代表重建所有权。

## 2. 接口

`rebuild_behavior_projections(session, *, space_id: int, account_id: int | None = None, now: datetime | None = None) -> int`。

- `account_id` 指定时只重建该空间中的该账号；缺省时重建整个空间的所属键。
- 返回成功处理的事件数量，不是最终投影行数；四个有效事件可以重建三行。
- `BEHAVIOR_PROJECTION_ENABLED=False` 时直接返回 `0`，不清理任何持久数据。

## 3. 必需行为

- 所属键族仅为 `card_cooldown:`、`correction_preference:`、`term_usage:`，分别对应 `card.dismissed`、`term.personal_updated`、`term.usage_recorded`。
- 两个删除分支都必须同时限定空间、可选账号和所属前缀；没有可重放事件时也只能删除这些键。
- 前缀比较必须是大小写敏感的字面量比较。SQLite 的 `LIKE` 会折叠大小写，且 `_` 是通配符；转义通配符本身不足以建立所有权边界。
- `kinship_recommendation_dismissed:`、未知键以及近似拼写的行，均须保留 ID、值、更新时间。
- 清理和重放使用调用方事务，不提交或回滚调用方其他工作。保持原事件过滤、合法 payload 校验与计数语义。
- 重复重放同一事件集，应得到相同键和值；允许本次重建的 `updated_at` 不同。

## 4. 条件与结果

| 条件 | 必需结果 |
| --- | --- |
| 功能关闭 | 返回 `0`，所有行不变 |
| 所属旧行没有对应事件 | 清理该作用域内的所属旧行 |
| 非所属键没有对应事件 | 完整保留 |
| 其他空间或账户级重建中的其他账号 | 完整保留 |
| 无效事件、缺失 actor 或不支持的 payload | 不产生投影，不计入成功重放数 |
| 同事件集再次重建 | 所属语义结果相同，非所属行仍原样保留 |

## 5. 正反例

- 正例：真实推荐服务写入忽略冷却后，重建 card/term 投影仍不会让该推荐重新出现。
- 基线：功能关闭时，已存在的冷却和偏好全部保留。
- 禁止：删除空间内全部 `BehaviorProjection` 再只重放 card/term 事件；把全局写入白名单直接当作可删除集合；把 `CARD_COOLDOWN:` 或 `cardXcooldown:` 当作 `card_cooldown:`。
- 本合同不要求把重建器接入普通 Steward 作业或 maintenance，也不扩大行为收集范围。

## 6. 必需验证

`backend/tests/test_steward_behavior_rebuild.py` 必须覆盖：功能开关 × 账户/空间四组合；真实推荐生产、忽略和读取；无事件、大小写与下划线反例；其他账号/空间保全；无效事件及重复重放计数。

在 `backend/` 执行：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_behavior_rebuild.py tests/test_family_recommendations.py
.venv/bin/ruff check app/services/steward.py tests/test_steward_behavior_rebuild.py
.venv/bin/ruff format --check app/services/steward.py tests/test_steward_behavior_rebuild.py
.venv/bin/python -m mypy app
```

## 7. 错误与正确的删除边界

```sql
-- 错误：范围过大，重建器无法恢复推荐服务的冷却。
DELETE FROM behavior_projections WHERE space_id = :space_id;

-- 正确：对每个所属前缀使用字面量比较，并保留空间/账号条件。
DELETE FROM behavior_projections
WHERE space_id = :space_id
  AND (:account_id IS NULL OR account_id = :account_id)
  AND substr(projection_key, 1, length(:prefix)) = :prefix;
```

关联合同：[Steward 与 ActionCard](steward-action-card.md)、[数据库事务](database-guidelines.md)。
