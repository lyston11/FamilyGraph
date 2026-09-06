"""开发演示数据种子与一次性清库 CLI（09-05 dev-seed-demo-data）。

自动播种（app.main lifespan 在 admin bootstrap 之后调用，进程级单次）：
- 门控：``DEV_SEED_DEMO_DATA=1``（默认 "0" 关闭）+ 进程级单次防重入；未开启或
  本进程已执行过时只跳过并记日志，绝不变更已有数据；
- 播种语义为「固定清单 + 增量补缺（insert-only 收敛）」：不要求空库，每次启动
  在同一 ``BEGIN IMMEDIATE`` 事务内把固定清单与库内现状逐行比对，缺什么补什么；
  任何已存在行绝不 UPDATE/DELETE（红线）——常量清单里新加成员/关系后重启即自动
  补齐，无需清库重播；要彻底重置仍走 ``--reset``；
- 演示数据集「王德海家 + 王氏家族 + 李国强家」：12 名可登录成员（PIN 统一
  123456，公开 dev 演示值，含结构化出生日期——王小虎/王朵朵为未成年人，演示
  未成年保护 overlay）、spouse/elder 结构边及其 confirmed SourceFact 映射
  （全局一份，家庭卡与家族树均可投影渲染）、三个空间——household「王德海家」
  （6 人）、lineage「王氏家族」（11 人：核心 6 人 + 旁系亲属，演示双空间投影
  差异）、household「李国强家」（2 人：李国强 admin + 王德海 member，演示第二
  空间管理员与跨空间成员资格）；基础五类披露全局开放（高敏感保持关闭）；
  全部经 SQLAlchemy 模型与既有 security / source_facts / disclosure 设施写入，
  不使用裸 SQL，不创建/修改 system_admins（admin bootstrap 的专属职责，09-04
  合同）。

一次性清库（运维）：``python -m app.dev_seed --reset``
- 先备份当前 db 到 ``/data/backups/pre-reset-<时间戳>.db``，再删除
  db/-wal/-shm，stdout 打印「docker compose restart api」指引；
- 绝不在活进程内原地重建 SQLite（服务进程会持有已删除 inode 继续写旧文件），
  重启后由启动链完成 迁移 → admin bootstrap → （清单增量收敛）播种。

形态契约：种子造数模型形态与 tests/conftest.py 的 create_user_with_pin /
seed_space_with_owner / create_v1_relation / seed_structural_edge_to_fact 保持
同步（conftest 是测试件，生产模块不 import，故此处内聚同构实现；两侧改动须
人工同步）。演示家庭谱系按 v1 边方向语义（to_user 是 from_user 的 dir_class）：
王远山/赵桂兰 是 王德海 与 王秀兰 的父母；王德海⇄周秀英 为配偶；王建军/王小雨
是 王德海 与 周秀英 的子女；王小虎/王朵朵 是 王建军 与 刘婷婷 的子女；
王秀兰⇄张伟 为配偶——四代结构。
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app import config
from app.commands.context import command_transaction
from app.models.account import Account
from app.models.relation import NON_TERMINAL_STATUSES, Relation
from app.models.relationship_facts import SourceFact
from app.models.space import FamilySpace, SpaceMember
from app.models.user import BASIC_DISCLOSURE_KEYS, User
from app.services import disclosure as disclosure_service
from app.services import source_facts as sf_service
from app.utils import security, timeutil

logger = logging.getLogger(__name__)

# 进程级防重入（与 services/admin_bootstrap._BOOTSTRAP_DONE 同款语义）
_SEED_DONE = False

# ---- 演示数据集常量（PRD 2.2 + 09-05 family-profile-nav-disclosure R3/R6 + 多空间扩展）----

_SEED_SPACE_NAME = "王德海家"
_SEED_LINEAGE_SPACE_NAME = "王氏家族"
_SEED_SECOND_SPACE_NAME = "李国强家"
_SEED_PIN = "123456"  # 公开 dev 演示值（PRD 红线 4：允许出现在日志）
_SEED_OWNER_NAME = "王德海"
# household「王德海家」核心成员；(姓名, 性别)；gender 枚举见 app.schemas.user.GenderType
_SEED_HOUSEHOLD_MEMBERS: tuple[tuple[str, str], ...] = (
    ("王德海", "m"),
    ("周秀英", "f"),
    ("王建军", "m"),
    ("王小雨", "f"),
    ("王远山", "m"),
    ("王小虎", "m"),
)
# lineage「王氏家族」在核心成员之外追加的旁系亲属（只进家族空间，演示双空间投影差异）
_SEED_LINEAGE_EXTRA_MEMBERS: tuple[tuple[str, str], ...] = (
    ("赵桂兰", "f"),
    ("王秀兰", "f"),
    ("张伟", "m"),
    ("刘婷婷", "f"),
    ("王朵朵", "f"),
)
# 第二位家庭空间管理员：自有 household 空间并纳入王德海（演示跨空间成员资格；
# 王德海已在 _SEED_HOUSEHOLD_MEMBERS 中建户，用户全集只补李国强本人）
_SEED_SECOND_ADMIN_NAME = "李国强"
_SEED_SECOND_SPACE_MEMBERS: tuple[tuple[str, str], ...] = (
    (_SEED_SECOND_ADMIN_NAME, "m"),
    (_SEED_OWNER_NAME, "m"),
)
# 用户全集不设 import 期派生常量：播种函数内由三个名册常量现算（三空间成员
# 并集，全部可登录，不得重复建户）——避免将来只改名册、全集派生不同步的坑。
# 结构化出生日期（solar）；王小虎 2018 / 王朵朵 2021 年生 = 未成年人（演示 minor 保护 overlay）
_SEED_BIRTHS: dict[str, tuple[int, int, int]] = {
    "王远山": (1940, 5, 12),
    "赵桂兰": (1942, 8, 21),
    "张伟": (1961, 3, 17),
    "王秀兰": (1962, 10, 6),
    "李国强": (1963, 2, 11),
    "王德海": (1965, 3, 8),
    "周秀英": (1967, 7, 19),
    "王建军": (1990, 11, 2),
    "刘婷婷": (1992, 6, 30),
    "王小雨": (1993, 4, 25),
    "王小虎": (2018, 9, 14),
    "王朵朵": (2021, 5, 9),
}
# v1 结构边 (from_name, to_name, dir_class)，方向语义同 Relation 模型 docstring
_SEED_EDGES: tuple[tuple[str, str, str], ...] = (
    # 核心三代：王德海家
    ("王德海", "周秀英", "spouse"),
    ("王德海", "王远山", "elder"),
    ("王建军", "王德海", "elder"),
    ("王小雨", "王德海", "elder"),
    ("王小虎", "王建军", "elder"),
    # 祖辈 + 旁系：王氏家族
    ("王远山", "赵桂兰", "spouse"),
    ("王德海", "赵桂兰", "elder"),
    ("王秀兰", "王远山", "elder"),
    ("王秀兰", "赵桂兰", "elder"),
    ("王秀兰", "张伟", "spouse"),
    ("王建军", "刘婷婷", "spouse"),
    ("王小虎", "刘婷婷", "elder"),
    ("王朵朵", "王建军", "elder"),
    ("王朵朵", "刘婷婷", "elder"),
)


class _SeedOutcome(NamedTuple):
    """一次清单收敛的结果计数（仅供摘要日志；不含姓名等 PII）。"""

    manifest_users: int
    added_users: int
    added_spaces: int
    added_memberships: int
    added_relations: int
    household_id: int
    lineage_id: int
    second_space_id: int

    @property
    def wrote_anything(self) -> bool:
        return bool(
            self.added_users or self.added_spaces or self.added_memberships or self.added_relations
        )

    @property
    def is_full_seed(self) -> bool:
        """清单内用户全部新建 = 空库快路径的全量播种（摘要日志沿用原形态）。"""
        return self.manifest_users > 0 and self.added_users == self.manifest_users


def maybe_seed_demo_data(session: Session) -> bool:
    """启动 preflight 种子入口（lifespan 在 admin bootstrap 之后调用）。

    门控：``DEV_SEED_DEMO_DATA=1``（默认 "0" 关闭）+ 进程级单次（``_SEED_DONE``）；
    未开启或本进程已执行过时零写入跳过。开启时**不要求空库**——在同一
    ``BEGIN IMMEDIATE`` 事务内对固定清单做 insert-only 收敛比对，缺什么补什么；
    任何已存在行绝不 UPDATE/DELETE（红线），清单收敛比对与多表写入同事务，
    消除「检查 → 插入」竞态窗口（同 admin bootstrap 的事务锁模式）。

    返回 True 表示本次发生了插入（含空库全量播种快路径）；清单已完全收敛、
    零写入时返回 False。两种情况下 ``_SEED_DONE`` 都置 True（本进程不再重入）。
    """
    global _SEED_DONE
    if config.DEV_SEED_DEMO_DATA != "1":
        logger.debug("dev seed skipped: DEV_SEED_DEMO_DATA != 1")
        return False
    if _SEED_DONE:
        logger.debug("dev seed skipped: already seeded in this process")
        return False
    with command_transaction(session, immediate=True):
        outcome = _seed_demo_family(session)
    _SEED_DONE = True
    if outcome.is_full_seed:
        # 全量播种（空库快路径）：沿用原摘要形态（计数 / space_id / 公开演示 PIN）；
        # 姓名等 PII 不进应用日志（logging-guidelines），演示集固定可按 space_id 查库核对
        logger.info(
            "dev seed completed (full): household_id=%d lineage_id=%d second_space_id=%d "
            "members=%d relations=%d source_facts=%d; demo PIN=%s (public dev value)",
            outcome.household_id,
            outcome.lineage_id,
            outcome.second_space_id,
            outcome.manifest_users,
            len(_SEED_EDGES),
            len(_SEED_EDGES),
            _SEED_PIN,
        )
    elif outcome.wrote_anything:
        # 增量补缺：只报计数，姓名等 PII 不进日志（logging-guidelines 既有约定）
        logger.info(
            "dev seed completed (incremental): added users=%d spaces=%d "
            "memberships=%d relations=%d",
            outcome.added_users,
            outcome.added_spaces,
            outcome.added_memberships,
            outcome.added_relations,
        )
    else:
        logger.info("dev seed converged: manifest already satisfied; no writes")
        return False
    return True


def _find_seed_user(session: Session, name: str) -> User | None:
    """按姓名精确匹配既有用户（只匹配未删除行，取 id 最小者）。

    家庭端姓名本就不唯一（同名建档另有绑定确认流），清单收敛取最旧行复用；
    既有用户原样复用其 id，绝不改动其 birth/gender/PIN/profile_status/披露偏好
    （insert-only 红线优先于清单口径的强一致）。
    """
    return session.scalar(
        select(User).where(User.name == name, User.deleted_at.is_(None)).order_by(User.id).limit(1)
    )


def _find_seed_space(session: Session, name: str) -> FamilySpace | None:
    """按空间名精确匹配既有空间（三个种子空间名足够独特，取 id 最小者）。"""
    return session.scalar(
        select(FamilySpace).where(FamilySpace.name == name).order_by(FamilySpace.id).limit(1)
    )


def _pair_relation_exists(session: Session, from_user_id: int, to_user_id: int) -> bool:
    """两人之间（任一方向）是否已有 pending/active 关系边。

    匹配口径必须覆盖 relations 的两条 partial unique 索引
    （uq_relations_pair_fwd / uq_relations_pair_rev，WHERE status IN
    ('pending','active')）：任一方向已有非终态边时再插入必然 IntegrityError，
    且 insert-only 红线禁止改写既有边的 dir_class——此时该清单边视为已被
    覆盖，跳过且不为既有边补建 fact（fact 只随关系新建一起落）。
    """
    stmt = (
        select(Relation.id)
        .where(
            Relation.status.in_(NON_TERMINAL_STATUSES),
            or_(
                and_(Relation.from_user == from_user_id, Relation.to_user == to_user_id),
                and_(Relation.from_user == to_user_id, Relation.to_user == from_user_id),
            ),
        )
        .limit(1)
    )
    return session.scalar(stmt) is not None


def _global_fact_exists(
    session: Session, *, fact_type: str, subject_user_id: int, object_user_id: int
) -> bool:
    """同元组全局事实（space_id=NULL）是否已有非 revoked 行。

    SourceFact 的 partial unique 覆盖 (subject, object, fact_type,
    COALESCE(space_id, -1)) 的全部非 revoked 行（不限 confirmed）——存量 dev 库
    可能残留业务流写入的事实；不预检时 create_source_fact 会 409 并回滚整个
    播种事务。此情形只补关系、不再落 fact（insert-only，无删改）。
    """
    stmt = (
        select(SourceFact.id)
        .where(
            SourceFact.fact_type == fact_type,
            SourceFact.subject_user_id == subject_user_id,
            SourceFact.object_user_id == object_user_id,
            SourceFact.space_id.is_(None),
            SourceFact.state != sf_service.FACT_REVOKED,
        )
        .limit(1)
    )
    return session.scalar(stmt) is not None


def _seed_demo_user(session: Session, *, name: str, gender: str, now: datetime) -> User:
    """单个演示成员；形态契约与 tests/conftest.py create_user_with_pin 保持同步
    （claimed + identity_confirmed + pin_must_change=False，家庭端 PIN 统一；
    birth 用 solar 结构化日期——王小虎/王朵朵为未成年人，演示 minor 保护 overlay）。"""
    y, m, d = _SEED_BIRTHS[name]
    user = User(
        name=name,
        created_at=now,
        gender=gender,
        privacy_mode="handover",
        created_by=None,
        birth={"cal_type": "solar", "date": f"{y:04d}-{m:02d}-{d:02d}", "is_leap_month": False},
        bio=None,
        profile_status="identity_confirmed",
        profile_confirmed_at=now,
    )
    user.account = Account(
        pin_hash=security.hash_pin(_SEED_PIN),
        pin_must_change=False,
        token_version=0,
        failed_attempts=0,
        locked_until=None,
        status="claimed",
        claimed_at=now,
    )
    session.add(user)
    return user


def _seed_space_member(
    session: Session, *, space_id: int, user_id: int, added_by: int, role: str, now: datetime
) -> None:
    """成员行；形态契约与 tests/conftest.py seed_space_with_owner 保持同步
    （role=space_admin|member + status=active，受 CHECK/partial unique 约束兜底）。"""
    session.add(
        SpaceMember(
            space_id=space_id,
            user_id=user_id,
            added_by=added_by,
            role=role,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )


def _seed_relation(
    session: Session, *, from_user_id: int, to_user_id: int, dir_class: str, now: datetime
) -> Relation:
    """v1 结构边；形态契约与 tests/conftest.py create_v1_relation 保持同步。"""
    row = Relation(
        from_user=from_user_id,
        to_user=to_user_id,
        dir_class=dir_class,
        label=None,
        created_by=from_user_id,
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    return row


def _map_structural_edge(edge: Relation) -> tuple[str, int, int]:
    """v1 active 结构边 → SourceFact (fact_type, subject_id, object_id) 方向映射。

    形态契约与 tests/conftest.py seed_structural_edge_to_fact 保持同步：
    elder f→t：t 是长辈 → biological_parent(t, f)；younger 反向；spouse 对称。
    """
    if edge.dir_class == "elder":
        return "biological_parent", edge.to_user, edge.from_user
    if edge.dir_class == "younger":
        return "biological_parent", edge.from_user, edge.to_user
    if edge.dir_class == "spouse":
        return "spouse", edge.from_user, edge.to_user
    raise ValueError(f"不可映射的 dir_class: {edge.dir_class}")


def _seed_demo_family(session: Session) -> _SeedOutcome:
    """在当前事务内对固定清单做 insert-only 收敛，返回计数与种子空间 id
    （调用方负责事务/提交）。

    逐类收敛规则（**只增不改不删**——任何已存在行绝不 UPDATE/DELETE）：
    - 用户：按姓名精确匹配（未删行，取 id 最小者）；缺失才建户（含 Account、
      结构化 birth），既有用户原样复用 id，其 birth/gender/PIN/profile_status/
      披露偏好一律不动；
    - 空间：按 name 精确匹配；缺失才按常量创建（owner 用同名用户 id），
      既有空间不动（含 owner_id）；
    - 成员行：按 (space_id, user_id) 匹配；无行才插入，role 按常量（admin 名单
      →space_admin，其余 member），已有行哪怕 role 不同也不动；
    - 关系边：任一方向已有 pending/active 边即视为已覆盖（口径覆盖两条
      partial unique 索引），跳过且不为既有边补建 fact；缺失才插入 Relation
      并随新建一起落 confirmed 全局 SourceFact（同元组全局事实已存在时只补
      关系，避免 create_source_fact 409 回滚整个事务）；
    - 披露：基础五类全局开放只对**本次新建**的用户设置。

    写入顺序 User+Account → 三空间（household 王德海家 / lineage 王氏家族 /
    household 李国强家）+成员行 → Relation → confirmed SourceFact（**全局事实**
    space_id=NULL，一份喂饱所有空间投影）。全部走模型约束与 source_facts /
    disclosure 服务（含 parent 成环检测），无裸 SQL。
    """
    now = timeutil.utcnow()
    # 用户全集由三个名册常量现算（household 核心 + lineage 旁系 + 第二管理员；
    # 王德海已在 _SEED_HOUSEHOLD_MEMBERS 中，不重复）
    all_members: tuple[tuple[str, str], ...] = (
        _SEED_HOUSEHOLD_MEMBERS + _SEED_LINEAGE_EXTRA_MEMBERS + ((_SEED_SECOND_ADMIN_NAME, "m"),)
    )

    added_users = 0
    users: dict[str, User] = {}
    new_users: list[User] = []
    for name, gender in all_members:
        user = _find_seed_user(session, name)
        if user is None:
            user = _seed_demo_user(session, name=name, gender=gender, now=now)
            added_users += 1
            new_users.append(user)
        users[name] = user
    session.flush()  # 取得 users.id 供空间/成员/关系引用

    added_spaces = 0
    space_specs: tuple[tuple[str, str, int], ...] = (
        (_SEED_SPACE_NAME, "household", users[_SEED_OWNER_NAME].id),
        (_SEED_LINEAGE_SPACE_NAME, "lineage", users[_SEED_OWNER_NAME].id),
        (_SEED_SECOND_SPACE_NAME, "household", users[_SEED_SECOND_ADMIN_NAME].id),
    )
    spaces: dict[str, FamilySpace] = {}
    for name, kind, owner_id in space_specs:
        space = _find_seed_space(session, name)
        if space is None:
            # 三个空间：家庭卡（household）/ 家族树（lineage）/ 第二管理员
            # household 空间——多空间模型（PRD R3 + 跨空间成员资格场景）
            space = FamilySpace(name=name, kind=kind, owner_id=owner_id, created_at=now)
            session.add(space)
            added_spaces += 1
        spaces[name] = space
    session.flush()
    space_rosters: tuple[tuple[FamilySpace, tuple[tuple[str, str], ...], str], ...] = (
        (spaces[_SEED_SPACE_NAME], _SEED_HOUSEHOLD_MEMBERS, _SEED_OWNER_NAME),
        (
            spaces[_SEED_LINEAGE_SPACE_NAME],
            _SEED_HOUSEHOLD_MEMBERS + _SEED_LINEAGE_EXTRA_MEMBERS,
            _SEED_OWNER_NAME,
        ),
        (spaces[_SEED_SECOND_SPACE_NAME], _SEED_SECOND_SPACE_MEMBERS, _SEED_SECOND_ADMIN_NAME),
    )
    added_memberships = 0
    for space, roster, admin_name in space_rosters:
        for name, _gender in roster:
            user_id = users[name].id
            exists = session.scalar(
                select(SpaceMember.id)
                .where(SpaceMember.space_id == space.id, SpaceMember.user_id == user_id)
                .limit(1)
            )
            if exists is not None:
                continue  # 既有成员行不动（哪怕 role 不同）
            _seed_space_member(
                session,
                space_id=space.id,
                user_id=user_id,
                added_by=users[admin_name].id,
                role="space_admin" if name == admin_name else "member",
                now=now,
            )
            added_memberships += 1
    session.flush()

    # 结构边 + confirmed SourceFact：**全局事实（space_id=NULL）**。
    # 成环检测（source_facts._ancestors_within）不按空间隔离，同一亲子事实落两份
    # 会被判环；且 load_graph 对全局事实全空间可见——一份事实即可同时喂饱
    # 家庭卡（household 投影）与家族树（lineage 投影），语义上也更贴近
    # 「血缘关系是全局事实，空间只是可见范围」的领域模型。
    added_relations = 0
    for from_name, to_name, dir_class in _SEED_EDGES:
        from_user_id = users[from_name].id
        to_user_id = users[to_name].id
        if _pair_relation_exists(session, from_user_id, to_user_id):
            continue
        edge = _seed_relation(
            session,
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            dir_class=dir_class,
            now=now,
        )
        added_relations += 1
        fact_type, subject_id, object_id = _map_structural_edge(edge)
        if not _global_fact_exists(
            session, fact_type=fact_type, subject_user_id=subject_id, object_user_id=object_id
        ):
            sf_service.create_source_fact(
                session,
                fact_type=fact_type,
                subject_user_id=subject_id,
                object_user_id=object_id,
                space_id=None,
                provenance="connection_accept",
                state=sf_service.FACT_CONFIRMED,
            )

    # 基础五类披露全局开放（R6 成员互见）：只对本次新建用户设置，既有用户的
    # 披露偏好一律不动；高敏感类别不写（默认关闭，Q4=b 仅为"可开"）
    for user in new_users:
        disclosure_service.set_basic_disclosure(
            session, user, {key: True for key in BASIC_DISCLOSURE_KEYS}
        )
    return _SeedOutcome(
        manifest_users=len(all_members),
        added_users=added_users,
        added_spaces=added_spaces,
        added_memberships=added_memberships,
        added_relations=added_relations,
        household_id=spaces[_SEED_SPACE_NAME].id,
        lineage_id=spaces[_SEED_LINEAGE_SPACE_NAME].id,
        second_space_id=spaces[_SEED_SECOND_SPACE_NAME].id,
    )


def reset_database() -> Path | None:
    """一次性清库：备份当前 db → 删除 db/-wal/-shm（``--reset`` CLI 路径）。

    备份用 SQLite online backup API 产一致性快照（WAL 模式下直接 cp 主库文件
    会丢 -wal 中已提交事务，database-guidelines 禁止运行期裸 cp）。只在活进程外
    执行：本函数绝不重建 schema，重启后由启动链完成迁移 → bootstrap → 播种。
    返回备份文件路径；当前库文件不存在（全新数据卷）时返回 None。
    """
    db_path = config.DB_PATH
    backup_path: Path | None = None
    if db_path.exists():
        config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_path = config.BACKUPS_DIR / f"pre-reset-{stamp}.db"
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(FileNotFoundError):
            Path(str(db_path) + suffix).unlink()
    return backup_path


def main() -> None:
    """CLI 入口：``python -m app.dev_seed [--reset]``（退出码 0=成功）。"""
    parser = argparse.ArgumentParser(description="FamilyGraph dev 演示数据种子 / 一次性清库工具")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="备份当前 db 后删除 db/-wal/-shm（重启 api 后自动迁移+bootstrap+播种）",
    )
    args = parser.parse_args()
    if not args.reset:
        parser.print_help()
        return
    backup_path = reset_database()
    if backup_path is None:
        print("backup : (no existing database file; nothing to back up)")
    else:
        print(f"backup : {backup_path}")
    print(f"removed: {config.DB_PATH} 及其 -wal/-shm")
    print("next   : docker compose restart api   # 迁移 → admin bootstrap → dev seed")


if __name__ == "__main__":
    main()
