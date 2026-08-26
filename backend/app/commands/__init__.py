"""应用命令层（AC-F7）：HTTP 与未来 Agent domain tool 共用的事务边界。

每条命令一个短事务：授权（actor 来自 ActorContext）→ FSM 校验 → 写入 →
domain_events → audit；外部网络/物理文件 I/O 不进事务。按聚合分模块。
"""

from app.commands.context import ActorContext, command_transaction, load_actor

__all__ = ["ActorContext", "command_transaction", "load_actor"]
