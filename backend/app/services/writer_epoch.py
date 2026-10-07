"""Writer epoch 与 migration health（C7）。

## 为什么需要

切换 SQLite → PostgreSQL 时，最大的风险不是「数据搬不过去」，而是**两个 writer
同时裁决**（双主）。双主会让 lease/settle/counter 出现两个真相，且无法通过对账
事后修复——因为两边都可能已经对外产生了结果。

因此需要两件基础设施：

1. **writer epoch**：一个**持久化、单调递增**的整数，标记「当前由谁写」。
   所有写路径在提交前核对 epoch；epoch 变了说明本实例已不是 writer，必须拒绝
   继续写（而不是「尽力而为」地写下去）。
2. **migration health**：把「当前处于哪个阶段」变成可查询状态（`sqlite` /
   `shadow` / `pg_control` / `pg_all`），使**回滚只需改 epoch 与阶段**，
   不需要改代码或手工修数据。

## 回滚语义（本模块的关键设计）

回滚 = 把阶段**退回上一级**并递增 epoch。因为：

- epoch 递增让**所有**旧实例立刻停止写入（它们持有的 epoch 已过期）；
- 不需要逐实例重启；
- 不产生「两个实例都以为自己是 writer」的窗口。

**禁止**：把 PostgreSQL 的新状态盲写回 SQLite。那会让 SQLite 变成「落后但仍在
被写」的第二真相源。回滚只回退**路由**，数据保留在 PostgreSQL 里。

## 阶段定义

| 阶段 | 含义 | 允许的 writer |
|---|---|---|
| `sqlite` | 迁移前 | SQLite |
| `shadow` | 只读对照（PG 只读，不写） | SQLite |
| `pg_control` | control-plane 写 PG | PG（control 面） |
| `pg_all` | 全部写 PG | PG（全部） |

阶段只能**逐级**前进或退回，不能跳级——跳级会让「哪些面已经切过」变得不可知。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.utils import timeutil

#: 允许的阶段，顺序即推进顺序。
WRITER_STAGES: tuple[str, ...] = ("sqlite", "shadow", "pg_control", "pg_all")

#: 部署级默认阶段。未配置时是 `sqlite`（迁移前），保证既有部署行为不变。
DEFAULT_STAGE = os.environ.get("FG_WRITER_STAGE", "sqlite")

#: 单例行 id。
_STATE_ID = 1


class WriterEpochMismatch(Exception):
    """本实例持有的 epoch 已过期：它已不是 writer，必须拒绝写入。

    这是一个**安全**异常，不是暂时性故障：继续写会造成双主。
    """

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"writer epoch changed: instance holds {expected}, database is at {actual}"
        )
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True)
class WriterState:
    """当前 writer 阶段与 epoch 的只读快照。"""

    stage: str
    epoch: int
    updated_at: datetime | None
    updated_by: str | None

    @property
    def postgres_is_authoritative(self) -> bool:
        """PostgreSQL 是否已成为持久真源。

        `shadow` 阶段 PG 只读对照，因此**不是**真源——把它当真源会让 shadow
        期间的写入被误判为已切换。
        """
        return self.stage in ("pg_control", "pg_all")

    @property
    def control_plane_on_postgres(self) -> bool:
        return self.stage in ("pg_control", "pg_all")

    @property
    def all_domains_on_postgres(self) -> bool:
        return self.stage == "pg_all"


def _stage_index(stage: str) -> int:
    try:
        return WRITER_STAGES.index(stage)
    except ValueError:
        raise ValueError(
            f"unknown writer stage {stage!r}; expected one of {WRITER_STAGES}"
        ) from None


def read_state(db: Session) -> WriterState:
    """读当前 writer 状态。表不存在或行为空时回落到部署默认阶段。

    回落是必要的：迁移**之前**表还不存在，而 health 端点必须能回答。
    此时按 `FG_WRITER_STAGE` 判断，使迁移前后的行为一致。
    """
    try:
        row = db.execute(
            sa.text(
                "SELECT stage, epoch, updated_at, updated_by" " FROM writer_state WHERE id = :id"
            ),
            {"id": _STATE_ID},
        ).first()
    except Exception:  # noqa: BLE001 - 表可能尚未创建（迁移前）
        db.rollback()
        return WriterState(stage=DEFAULT_STAGE, epoch=0, updated_at=None, updated_by=None)
    if row is None:
        return WriterState(stage=DEFAULT_STAGE, epoch=0, updated_at=None, updated_by=None)
    return WriterState(stage=row[0], epoch=int(row[1]), updated_at=row[2], updated_by=row[3])


def advance(
    db: Session,
    *,
    to_stage: str,
    actor: str,
    now: datetime | None = None,
) -> WriterState:
    """推进或回退到相邻阶段，并**递增 epoch**。

    只允许相邻阶段：跳级会让「哪些面已经切过」不可知，从而无法安全回滚。
    回滚同样是相邻退一级，并递增 epoch——这使所有旧实例立刻失去写权，
    不需要逐实例重启。
    """
    current = read_state(db)
    if to_stage == current.stage:
        return current
    delta = _stage_index(to_stage) - _stage_index(current.stage)
    if abs(delta) != 1:
        raise ValueError(
            f"writer stage must move one step at a time:"
            f" {current.stage} -> {to_stage} (delta={delta})"
        )
    moment = now or timeutil.utcnow()
    db.execute(
        sa.text(
            "UPDATE writer_state SET stage = :stage, epoch = epoch + 1,"
            " updated_at = :now, updated_by = :actor WHERE id = :id"
        ),
        {"stage": to_stage, "now": moment, "actor": actor, "id": _STATE_ID},
    )
    state = read_state(db)
    # 执行切换的实例**就是**当前 writer：必须同步采纳新 epoch，否则它自己的下一次
    # 写入会被守卫拒绝（把自己锁在门外）。
    global _process_epoch
    with _epoch_lock:
        _process_epoch = state.epoch
    return state


def check_epoch(db: Session, *, held: int) -> None:
    """核对本实例持有的 epoch；过期即抛 `WriterEpochMismatch`。

    写路径在提交前调用。**不**提供「尽力而为」的降级：epoch 过期意味着本实例
    已被取代，继续写就是双主。
    """
    state = read_state(db)
    if state.epoch != held:
        raise WriterEpochMismatch(expected=held, actual=state.epoch)


def _serialize_timestamp(value: object) -> str | None:
    """把时间戳序列化成字符串。

    SQLite 把 DATETIME 存成 TEXT 并**原样返回字符串**，PostgreSQL 返回 `datetime`。
    两种都要能序列化，否则 health 会在其中一个方言上直接 500（实测 AttributeError）。
    """
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def migration_health(db: Session) -> dict[str, object]:
    """migration health 快照（供 readiness 与诊断）。

    只含**治理元数据**：阶段、epoch、更新时间与 actor。不含任何业务数据、
    连接串、凭据或行内容——health 端点可能是公开的。
    """
    state = read_state(db)
    return {
        "writer_stage": state.stage,
        "writer_epoch": state.epoch,
        "postgres_authoritative": state.postgres_is_authoritative,
        "control_plane_on_postgres": state.control_plane_on_postgres,
        "all_domains_on_postgres": state.all_domains_on_postgres,
        "updated_at": _serialize_timestamp(state.updated_at),
        "updated_by": state.updated_by,
        "valid_stages": list(WRITER_STAGES),
    }


# ---------------------------------------------------------------- 写路径守卫
#
# epoch 只有在**写路径真正调用它**时才有价值。否则它只是一张表。
#
# ## 成本与开关
#
# 守卫每次写事务读一次 `writer_state`（主键单行查询）。SQLite 上亚毫秒，
# PostgreSQL 上是一次 PK 查找。迁移窗口内这个成本是值得的；迁移**完成且不再
# 计划变更阶段**后可以用 `FG_WRITER_EPOCH_GUARD=0` 关掉。
#
# 默认开启：默认关闭会让「忘了打开」变成静默的双主风险。

#: 本进程认为当前生效的 epoch。`None` = 尚未读过（首次调用时采纳数据库值）。
_process_epoch: int | None = None
_epoch_lock = threading.Lock()


def guard_enabled() -> bool:
    return os.environ.get("FG_WRITER_EPOCH_GUARD", "1") not in ("0", "false", "False")


def reset_process_epoch_for_tests() -> None:
    """测试用：清掉进程内缓存的 epoch。"""
    global _process_epoch
    with _epoch_lock:
        _process_epoch = None


def adopt_current_epoch(db: Session) -> int:
    """读取并采纳当前 epoch（进程启动或测试重置后调用）。"""
    global _process_epoch
    state = read_state(db)
    with _epoch_lock:
        _process_epoch = state.epoch
    return state.epoch


def guard(db: Session) -> None:
    """写事务的 epoch 守卫；epoch 已变则抛 `WriterEpochMismatch`。

    ## 语义

    - 首次调用（本进程尚无 epoch）**采纳**数据库当前值，而不是拒绝。理由：
      进程刚启动，它的 epoch 就是当前值；此时拒绝会让每次启动后的第一个写失败。
    - 之后每次调用比对：不同即拒绝。这是**切换让旧实例失效**的机制。

    ## 为什么不是「尽力而为」

    epoch 过期意味着本实例已被取代。继续写会产生两个真相（双主），而双主**无法**
    事后对账修复——两边都可能已对外产生结果。因此必须拒绝，且调用方不得重试。
    """
    if not guard_enabled():
        return
    global _process_epoch
    state = read_state(db)
    with _epoch_lock:
        held = _process_epoch
        if held is None:
            _process_epoch = state.epoch
            return
    if held != state.epoch:
        raise WriterEpochMismatch(expected=held, actual=state.epoch)
