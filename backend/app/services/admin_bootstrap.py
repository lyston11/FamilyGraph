"""管理员部署 bootstrap 与凭据文件交付（09-04 SF-F3）。

启动 preflight（lifespan 调用，进程级单次）：
1. fail-closed：检测到旧版 PIN 凭据结构（pin_hash 列）即拒绝服务——本任务
   不做 PIN 迁移，也不静默转换（design §4）；
2. 事务锁（BEGIN IMMEDIATE）内检查无任何 system_admin 行时创建唯一
   ``username=admin`` 账号：CSPRNG 强随机密码、bcrypt 哈希入库、明文原子写入
   ``DATA_DIR/bootstrap/admin-credentials``（0600）；
3. 密码明文只出现在该文件，不进日志/审计/数据库/响应；
4. 已存在管理员的部署重启不生成第二账号（受控启动状态记录）。

首次成功改密事务提交后由 services/admin_auth.finalize_credential_file 删除
文件；删除失败写安全告警且回置 password_must_change（不标记初始化完成）。
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from pathlib import Path

from sqlalchemy import func
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app import config
from app.commands.context import command_transaction
from app.models.system_admin import SystemAdmin, SystemAdminAccount
from app.services import audit
from app.utils import security, timeutil

logger = logging.getLogger(__name__)

CREDENTIALS_FILENAME = "admin-credentials"
DEFAULT_ADMIN_USERNAME = "admin"

_BOOTSTRAP_DONE = False


def credentials_file_path() -> Path:
    """bootstrap 凭据文件唯一路径（0600）。"""
    return config.BOOTSTRAP_DIR / CREDENTIALS_FILENAME


def credentials_file_exists() -> bool:
    return credentials_file_path().exists()


def _assert_no_legacy_pin_schema(session: Session) -> None:
    """旧 PIN 结构仍在库中时拒绝服务（fail-closed，不静默转换）。"""
    inspector = sa_inspect(session.connection())
    if "system_admin_accounts" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("system_admin_accounts")}
    if "pin_hash" in columns or "pin_must_change" in columns or "token_version" in columns:
        raise RuntimeError(
            "检测到旧版系统管理员 PIN 凭据结构（pin_hash/token_version）："
            "请先完成 Alembic 迁移 0028_admin_password_credentials 并显式处置旧账号；"
            "本服务不做 PIN→密码静默转换，拒绝启动"
        )


def write_private_file(path: Path, content: str) -> None:
    """原子写入 0600 私密文件（同目录临时文件 + os.replace）。

    mkstemp 自带 0600；写入、fsync 后原子改名，避免中途可读或半写文件。
    失败时清理临时文件后原样抛出 OSError。
    """
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def credentials_file_content(username: str, password: str) -> str:
    """凭据文件内容：仅用户名与一次性密码，外加首登必须改密提示。"""
    return (
        "FamilyGraph 管理员初始凭据（一次性交付；首次登录后必须立即修改密码，"
        "届时本文件会被自动删除）\n"
        f"username: {username}\n"
        f"password: {password}\n"
    )


def write_credentials_file(username: str, password: str) -> Path:
    path = credentials_file_path()
    write_private_file(path, credentials_file_content(username, password))
    return path


def delete_credentials_file() -> None:
    """删除 bootstrap 凭据文件；失败向上抛 OSError（由调用方写安全告警）。"""
    path = credentials_file_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return


def create_admin_account(
    session: Session, *, username: str, password: str
) -> tuple[SystemAdmin, SystemAdminAccount]:
    """创建管理员主体与凭据（bootstrap 与运维恢复共用同一密码设施）。"""
    now = timeutil.utcnow()
    admin = SystemAdmin(username=username, status="active", created_at=now, updated_at=now)
    admin.account = SystemAdminAccount(
        password_hash=security.hash_password(password),
        password_must_change=True,
        password_version=0,
        failed_attempts=0,
        locked_until=None,
        status="managed",
        claimed_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(admin)
    session.flush()
    return admin, admin.account


def _assert_migrated_schema(session: Session) -> None:
    """schema 未就绪（未跑 Alembic）时以可诊断错误拒绝启动（fail-closed）。"""
    inspector = sa_inspect(session.connection())
    if "system_admins" not in inspector.get_table_names():
        raise RuntimeError(
            "数据库缺少 system_admins 表：请先执行 alembic upgrade head 再启动服务"
            "（拒绝在未迁移数据库上自动 bootstrap）"
        )


def run_startup_preflight(session: Session) -> None:
    """启动 preflight 入口（lifespan 调用；进程级单次，多 listener 共享）。"""
    global _BOOTSTRAP_DONE
    if _BOOTSTRAP_DONE:
        return
    _assert_migrated_schema(session)
    _assert_no_legacy_pin_schema(session)
    _bootstrap_admin_if_needed(session)
    _BOOTSTRAP_DONE = True


def _bootstrap_admin_if_needed(session: Session) -> None:
    """事务锁内检查并创建唯一 admin 账号；凭据只落 0600 文件。"""
    with command_transaction(session, immediate=True):
        admin_count = session.query(func.count(SystemAdmin.id)).scalar()
        if admin_count:
            # 受控启动状态记录：已有管理员（无论 active/disabled）绝不生成第二账号
            logger.info(
                "admin bootstrap skipped: %d system admin account(s) already present",
                admin_count,
            )
            return
        password = security.generate_strong_password()
        admin, _account = create_admin_account(
            session, username=DEFAULT_ADMIN_USERNAME, password=password
        )
        # 先落文件再提交：提交失败时残留文件会在下次启动被覆盖，不会泄露可用凭据
        write_credentials_file(DEFAULT_ADMIN_USERNAME, password)
        audit.write_audit(
            session,
            action="admin_bootstrap_created",
            target_id=admin.id,
            detail={"principal_type": "system_admin"},
        )
    logger.info("admin bootstrap completed: credentials file delivered (0600)")
