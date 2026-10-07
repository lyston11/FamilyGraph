"""迁移拒绝用例的相对偏移计算（共享 helper）。

## 为什么必须计算而不是写死

四个迁移拒绝用例都用「深层相对降级」触发 Alembic 的 `Ambiguous walk`：
偏移必须深到**越过 0044/0048 合并分叉**，否则走位成功、DDL 照常执行，
「拒绝先于任何 DDL」的性质就测不到。

写死字面量（历史上是 `-2`/`-4`/`-7`）的问题是：**每新增一个迁移，同一个字面量就落到
不同 revision 上**。本次新增 0056/0057 后，`-4` 从「越过分叉」变成「停在分叉上方」，
于是四个用例全部失败——而它们守护的性质并未失效，是偏移不再指向分叉。

因此偏移从脚本目录**计算**：head → 第一个 merge fork 的步数 + 1。
新增迁移会自动把偏移推深，用例不再需要跟着改。
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).parents[1]


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))


def first_fork_steps() -> int:
    """从当前 head 向下走到第一个 merge fork 的步数（fork 本身位于该步）。"""
    script = _script()
    rev = script.get_revision(script.get_current_head())
    steps = 0
    while rev is not None:
        if isinstance(rev.down_revision, tuple):
            return steps
        if rev.down_revision is None:
            break
        rev = script.get_revision(rev.down_revision)
        steps += 1
    raise AssertionError("当前 head 的下行链上没有 merge fork")


def deep_downgrade_offset() -> str:
    """越过第一个 merge fork 的相对偏移（字符串形式，供 alembic CLI 使用）。

    比 `first_fork_steps()` 多一步：正好落在 fork **之后**，使 Alembic 在走位阶段
    即报 `Ambiguous walk`，从而在任何 DDL 之前失败。
    """
    return f"-{first_fork_steps() + 1}"


def steps_to(revision: str) -> int:
    """从当前 head 向下走到 `revision` 的步数。"""
    script = _script()
    rev = script.get_revision(script.get_current_head())
    steps = 0
    while rev is not None:
        if rev.revision == revision:
            return steps
        down = rev.down_revision
        if isinstance(down, tuple) or down is None:
            break
        rev = script.get_revision(down)
        steps += 1
    raise AssertionError(f"{revision} 不在当前 head 的下行链上")


def offset_to(revision: str) -> str:
    """正好落到 `revision` 的相对偏移。

    用于「祖先拒绝先于 DDL」用例：目标必须是某个**不是 head 直接父级**的 revision，
    这样 `destination != down_revision`，被拒绝迁移的 preflight 才会履行祖先拒绝
    （若目标等于直接父级，那只是正常降级一步，不会触发祖先拒绝）。

    必须计算而非写死：字面量会在新增迁移后落到不同 revision 上——本次新增
    0056/0057 就让四处 `-2`/`-4`/`-7` 全部失准。
    """
    return f"-{steps_to(revision)}"
