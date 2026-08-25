"""测试全局准备与 m0b 公共夹具。

环境变量必须在任何 app 模块导入前注入；夹具提供迁移建库、表清空、
客户端与造数辅助（管理员后台属 m4b 非目标，造数绕过 API）。
"""

import os
import subprocess
import tempfile

os.environ.setdefault("SECRET_KEY", "test-secret-key")
# bcrypt cost 降级：套件速度（生产默认 12）
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="familygraph-tests-")

# 环境变量就绪后才能导入 config（其路径/密钥在导入时读取）
from app import config  # noqa: E402

config.ensure_ready()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    """以真实 Alembic 迁移链建表（顺带验证 0002 迁移可执行）。"""
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        result = subprocess.run([".venv/bin/alembic", *args], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


@pytest.fixture()
def db_session():
    """每测试独立会话；结束后清空全部业务表保证隔离。"""
    session = SessionLocal()
    yield session
    session.close()


# 未来表（attachments/node_positions 等）落地时同步追加
_TABLES = (
    "node_positions",
    "attachments",
    "space_members",
    "relations",
    "family_spaces",
    "audit_log",
    "auth_challenges",
    "refresh_sessions",
    "accounts",
    "users",
)


@pytest.fixture(autouse=True)
def _clean_tables(db_session):
    yield db_session
    # 先丢弃测试残留的脏状态，再按子表→父表顺序清空（满足 FK，无需关外键）
    db_session.rollback()
    for table in _TABLES:
        db_session.execute(text(f"DELETE FROM {table}"))
    db_session.commit()


@pytest.fixture()
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def create_user_with_pin(
    session,
    name: str,
    pin: str,
    *,
    is_admin: bool = False,
    pin_must_change: bool = False,
    claim_status: str | None = None,
    privacy_mode: str = "handover",
    created_by: int | None = None,
    gender: str = "unknown",
    birth: dict | None = None,
    bio: str | None = None,
):
    """直接造数：绕过 API 创建账号。claim_status 缺省与首登态联动推断。"""
    from app.models.account import Account
    from app.models.user import User
    from app.utils import security, timeutil

    if claim_status is None:
        claim_status = "claimed" if not pin_must_change else "managed"
    user = User(
        name=name,
        is_admin=is_admin,
        created_at=timeutil.utcnow(),
        gender=gender,
        privacy_mode=privacy_mode,
        created_by=created_by,
        claim_status=claim_status,
        birth=birth,
        bio=bio,
    )
    user.account = Account(
        pin_hash=security.hash_pin(pin),
        pin_must_change=pin_must_change,
        token_version=0,
        failed_attempts=0,
        locked_until=None,
    )
    session.add(user)
    session.commit()
    return user


def login(client: TestClient, name: str, pin: str):
    return client.post("/api/auth/login", json={"name": name, "pin": pin})


def auth_header(token_pair: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_pair['access_token']}"}
