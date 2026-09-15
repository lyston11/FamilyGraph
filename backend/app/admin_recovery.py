"""受限运维恢复命令（09-04 SF-F5）：管理员忘记密码时的唯一恢复入口。

用法（容器内执行）::

    python -m app.admin_recovery [--username admin]

行为：使用部署配置的初始密码（未配置则随机），原子写入
``DATA_DIR/bootstrap/admin-recovery``（0600）；同一事务内递增
``password_version``（全部旧 access/refresh 即刻失效）、撤销全部 refresh
session、写安全审计 ``admin_password_recovery``、回置 ``password_must_change``。
密码明文只出现在 0600 文件，不进日志/标准输出/数据库。

该命令是受限运维操作：不删除账号、不改用户名、不产生任何家庭数据访问权。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models.system_admin import SystemAdmin, SystemAdminAccount
from app.services import admin_auth, admin_bootstrap, audit
from app.utils import security, timeutil

RECOVERY_FILENAME = "admin-recovery"


def recovery_file_path() -> Path:
    """恢复凭据文件路径（0600，一次一覆盖）。"""
    return config.BOOTSTRAP_DIR / RECOVERY_FILENAME


def run_recovery(session: Session, *, username: str) -> Path:
    """执行恢复流程；返回写入的文件路径。密码明文不经过任何日志。"""
    admin = session.query(SystemAdmin).filter(SystemAdmin.username == username.strip()).first()
    if admin is None:
        raise LookupError(f"未找到用户名为 {username!r} 的系统管理员")
    account = session.scalar(
        select(SystemAdminAccount).where(SystemAdminAccount.system_admin_id == admin.id)
    )
    if account is None:
        raise LookupError(f"系统管理员 {username!r} 缺少凭据账号，数据库状态异常")
    password = admin_bootstrap.initial_password()
    now = timeutil.utcnow()
    account.password_hash = security.hash_password(password)
    account.password_version += 1
    account.password_must_change = True
    account.updated_at = now
    admin.updated_at = now
    admin_auth.revoke_all_sessions(session, admin.id, None, "admin_recovery_sessions_revoked")
    audit.write_audit(
        session,
        action="admin_password_recovery",
        target_id=admin.id,
        detail={"principal_type": "system_admin"},
    )
    path = recovery_file_path()
    # 先落 0600 文件再提交：提交失败时残留文件会被下次恢复覆盖
    admin_bootstrap.write_private_file(
        path, admin_bootstrap.credentials_file_content(admin.username, password)
    )
    session.commit()
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.admin_recovery",
        description="生成一次性管理员恢复密码（只落 0600 文件）并使全部会话失效",
    )
    parser.add_argument("--username", default="admin", help="目标管理员用户名（默认 admin）")
    args = parser.parse_args(argv)

    config.ensure_ready()
    config.ensure_data_dirs()
    from app.db import SessionLocal

    try:
        with SessionLocal() as session:
            path = run_recovery(session, username=args.username)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    # 只输出路径：恢复密码永不进入标准输出/日志
    print(f"recovery credentials written to {path} (0600); password is NOT printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
