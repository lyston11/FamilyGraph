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
# V2.1 Agent Runtime：测试默认开启 feature flag 并配置共享密钥（生产默认关闭/必配）
os.environ.setdefault("AGENT_SERVICE_SECRET", "test-agent-service-secret")
os.environ.setdefault("AGENT_RUNTIME_ENABLED", "1")
# V2.4 Steward：测试默认开启 feature flag（生产默认关闭）
os.environ.setdefault("STEWARD_ENABLED", "1")
# V2.5 Memory/RAG/BehaviorProjection：测试默认开启（生产可独立关闭）
os.environ.setdefault("MEMORY_ENABLED", "1")
os.environ.setdefault("RAG_ENABLED", "1")
os.environ.setdefault("BEHAVIOR_PROJECTION_ENABLED", "1")
os.environ.setdefault("POLICY_GUARD_ENABLED", "1")
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


# 清表顺序：子表→父表（满足 FK，无需关外键）。v2 合同表追加于尾部。
# agent 块：runs.job_id↔jobs.run_id 循环由 ondelete 解环（删 jobs 时 runs.job_id 置空），
# 故顺序为 events→messages→jobs→runs→sessions。
_TABLES = (
    "agent_run_events",
    "agent_messages",
    "agent_jobs",
    "agent_runs",
    "agent_sessions",
    "agent_space_provider_settings",
    "agent_providers",
    # v2.6 controlled web: tokens/citations/usage must be cleared before accounts/spaces
    "web_citations",
    "web_request_usage",
    "web_approved_urls",
    "web_space_configs",
    "web_platform_configs",
    # v2.5 memory/RAG/context projections
    "context_build_items",
    "context_builds",
    "rag_chunks",
    "rag_documents",
    "memories",
    "memory_candidates",
    # v2.4 steward 块：action_cards/steward_jobs 引用 accounts/users/spaces，先于父表删
    "behavior_projections",
    "steward_jobs",
    "action_cards",
    # v2.3 事实层：source_facts 引用 raw_relation_inputs，须先删子表；
    # derived_facts 只引用 users/family_spaces，置于其前即可；
    # term_usages 引用 term_entries，须先删
    "source_facts",
    "social_relations",
    "raw_relation_inputs",
    "derived_facts",
    "term_usages",
    "term_entries",
    "node_positions",
    "attachments",
    "space_profile_refs",
    "profile_fact_reviews",
    "domain_events",
    "data_right_requests",
    "claim_disputes",
    "ownership_transfers",
    "owner_invitations",
    "disclosure_preferences",
    "platform_role_assignments",
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
    profile_status: str = "identity_confirmed",
):
    """直接造数：绕过 API 创建账号。

    v2 语义：
    - claim_status → accounts.status（managed|claimed）；缺省与首登态联动推断
    - profile_status：档案确档状态（fixture 默认 identity_confirmed，模拟既有建档）
    - is_admin=True 创建 platform_operator 角色分配（无家庭数据读取权）
    """
    from app.models.account import Account
    from app.models.user import User
    from app.models.v2_foundation import PlatformRoleAssignment
    from app.services.platform_roles import ROLE_PLATFORM_OPERATOR
    from app.utils import security, timeutil

    if claim_status is None:
        claim_status = "claimed" if not pin_must_change else "managed"
    now = timeutil.utcnow()
    user = User(
        name=name,
        created_at=now,
        gender=gender,
        privacy_mode=privacy_mode,
        created_by=created_by,
        birth=birth,
        bio=bio,
        profile_status=profile_status,
        profile_confirmed_at=(now if profile_status == "identity_confirmed" else None),
    )
    user.account = Account(
        pin_hash=security.hash_pin(pin),
        pin_must_change=pin_must_change,
        token_version=0,
        failed_attempts=0,
        locked_until=None,
        status=claim_status,
        claimed_at=(now if claim_status == "claimed" else None),
    )
    session.add(user)
    session.flush()  # 取得 account.id 供角色分配引用
    if is_admin:
        session.add(
            PlatformRoleAssignment(
                account_id=user.account.id,
                role=ROLE_PLATFORM_OPERATOR,
                created_by=None,
                created_at=now,
            )
        )
    session.commit()
    return user


def login(client: TestClient, name: str, pin: str):
    return client.post("/api/auth/login", json={"name": name, "pin": pin})


def auth_header(token_pair: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_pair['access_token']}"}


# ---- V2.1 Agent Runtime 造数辅助（绕过浏览器 API，浏览器 API 属后续 Block）----


def create_agent_fixture(session, *, name: str):
    """创建 user+account+space 三件套，返回 (user, space)。"""
    from app.models.space import FamilySpace

    user = create_user_with_pin(session, name, "123456")
    space = FamilySpace(
        name=f"{name}-space", kind="household", owner_id=user.id, created_at=user.created_at
    )
    session.add(space)
    session.commit()
    return user, space


def create_agent_session(session, *, account_id: int, space_id: int, kind: str = "assistant"):
    """直建 AgentSession（scope 固定）。"""
    from app.models.agent import AgentSession
    from app.utils import timeutil

    row = AgentSession(
        account_id=account_id, space_id=space_id, agent_kind=kind, created_at=timeutil.utcnow()
    )
    session.add(row)
    session.commit()
    return row


def create_agent_message(session, agent_session, *, role: str = "user", content=None):
    """直建会话消息（content_json 结构化投影，不存 Provider 私有 payload）。"""
    from app.models.agent import AgentMessage
    from app.utils import timeutil

    row = AgentMessage(
        session_id=agent_session.id,
        role=role,
        content_json=content or {"text": "hello"},
        created_at=timeutil.utcnow(),
    )
    session.add(row)
    session.commit()
    return row


def create_space_member(
    session,
    space_id: int,
    user_id: int,
    *,
    role: str = "member",
    status: str = "active",
):
    """直建空间成员行（浏览器 Agent API 测试满足 active 成员校验）。"""
    from app.models.space import SpaceMember
    from app.utils import timeutil

    now = timeutil.utcnow()
    row = SpaceMember(
        space_id=space_id,
        user_id=user_id,
        role=role,
        status=status,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


# ---- V2.3 SourceFact 造数辅助（Block E1；不回填生产数据，仅测试映射）----


def create_v1_relation(
    session,
    *,
    from_user_id: int,
    to_user_id: int,
    dir_class: str,
    status: str = "active",
):
    """直建 v1 结构边（elder/younger/spouse/peer），供映射工厂与对照测试使用。"""
    from app.models.relation import Relation
    from app.utils import timeutil

    now = timeutil.utcnow()
    row = Relation(
        from_user=from_user_id,
        to_user=to_user_id,
        dir_class=dir_class,
        label=None,
        created_by=from_user_id,
        status=status,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


def seed_structural_edge_to_fact(session, edge, *, space_id=None):
    """把一条 v1 active elder/spouse 边映射为 confirmed SourceFact（仅测试用）。

    方向映射（v1：to_user 是 from_user 的 dir_class）：
    - elder f→t：t 是长辈 → biological_parent subject=t object=f
    - younger f→t：f 是长辈 → biological_parent subject=f object=t
    - spouse：对称 → spouse subject=f object=t
    peer 边与非 active 状态不可映射，直接抛错。
    """
    from app.services import source_facts as sf_service

    if edge.status != "active":
        raise ValueError(f"仅映射 active 边，得到 {edge.status}")
    if edge.dir_class == "elder":
        fact_type, subject_id, object_id = "biological_parent", edge.to_user, edge.from_user
    elif edge.dir_class == "younger":
        fact_type, subject_id, object_id = "biological_parent", edge.from_user, edge.to_user
    elif edge.dir_class == "spouse":
        fact_type, subject_id, object_id = "spouse", edge.from_user, edge.to_user
    else:
        raise ValueError(f"不可映射的 dir_class: {edge.dir_class}")
    fact = sf_service.create_source_fact(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        space_id=space_id,
        provenance="connection_accept",
        state=sf_service.FACT_CONFIRMED,
    )
    session.commit()
    return fact
