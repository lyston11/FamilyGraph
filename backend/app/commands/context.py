"""应用命令层共享上下文与事务边界（AC-F7，spec/architecture.md §0.6）。

命令合同：
- 每条命令在同一短事务内完成 授权 → FSM 校验 → 写入 → domain_events → audit；
- actor 只能由认证上下文构造（HTTP 依赖或未来 Agent Runtime），命令永不接受
  来自请求体/模型层的 actor override；
- 命令不依赖 FastAPI Request 对象，未来 Agent domain tool 直接复用；
- 外部网络 I/O 不进入事务；物理文件清理等副作用由命令返回值交调用方在
  事务提交后执行。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.errors import AUTH_INVALID_CREDENTIALS, UNIFIED_CREDENTIAL_MESSAGE, raise_api_error
from app.models.account import Account
from app.models.user import User


@dataclass(frozen=True)
class ActorContext:
    """认证主体投影：命令授权的唯一依据。"""

    user_id: int
    account_id: int
    account_status: str
    ip: str | None = None

    @classmethod
    def from_identity(cls, user: User, account: Account, *, ip: str | None = None) -> ActorContext:
        """由 HTTP 认证依赖的 (user, account) 元组构造；Agent Runtime 可自行构造等价物。"""
        return cls(
            user_id=user.id,
            account_id=account.id,
            account_status=account.status,
            ip=ip,
        )


def load_actor(session: Session, ctx: ActorContext) -> User:
    """按上下文加载当前主体；上下文指向已删除档案时按认证失败处理。"""
    user = session.get(User, ctx.user_id)
    if user is None:
        raise_api_error(401, AUTH_INVALID_CREDENTIALS, UNIFIED_CREDENTIAL_MESSAGE)
    return user


@contextmanager
def command_transaction(session: Session, *, commit: bool = True) -> Iterator[Session]:
    """单条命令 = 一个短事务：成功提交，任何异常整体回滚后原样抛出。

    ``commit=False`` 供一个领域命令组合进更大的应用命令事务；外层仍须
    使用本上下文管理器完成最终 commit。路由不再自行 commit；错误路径不留
    脏会话（database-guidelines 写事务红线）。
    """
    try:
        yield session
        if commit:
            session.commit()
    except Exception:
        session.rollback()
        raise
