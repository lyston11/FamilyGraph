"""开发演示数据种子与一次性清库 CLI（09-05 dev-seed-demo-data）。

自动播种（app.main lifespan 在 admin bootstrap 之后调用，进程级单次）：
- 双重门控：``DEV_SEED_DEMO_DATA=1``（默认 "0" 关闭）且 ``users`` 表为空；
  任一不满足只跳过并记日志，绝不变更已有数据（PRD 红线 1）；
- 演示数据集「王德海家」：6 名可登录成员（PIN 统一 123456，公开 dev 演示值，含
  结构化出生日期——王小虎为未成年人，演示未成年保护 overlay）、spouse/elder
  结构边及其 confirmed SourceFact 映射（household + lineage 双空间各一份，
  家族卡与家族树均可投影渲染）、基础五类披露全局开放（高敏感保持关闭）；
  全部经 SQLAlchemy 模型与既有 security / source_facts / disclosure 设施写入，
  不使用裸 SQL，不创建/修改 system_admins（admin bootstrap 的专属职责，09-04 合同）。

一次性清库（运维）：``python -m app.dev_seed --reset``
- 先备份当前 db 到 ``/data/backups/pre-reset-<时间戳>.db``，再删除
  db/-wal/-shm，stdout 打印「docker compose restart api」指引；
- 绝不在活进程内原地重建 SQLite（服务进程会持有已删除 inode 继续写旧文件），
  重启后由启动链完成 迁移 → admin bootstrap → （空库门控）播种。

形态契约：种子造数模型形态与 tests/conftest.py 的 create_user_with_pin /
seed_space_with_owner / create_v1_relation / seed_structural_edge_to_fact 保持
同步（conftest 是测试件，生产模块不 import，故此处内聚同构实现；两侧改动须
人工同步）。演示家庭谱系按 v1 边方向语义（to_user 是 from_user 的 dir_class）：
王远山 是 王德海 的父亲；王德海⇄周秀英 为配偶；王建军/王小雨 是 王德海 与
周秀英 的子女；王小虎 是 王建军 的子女——三代结构。
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.commands.context import command_transaction
from app.models.account import Account
from app.models.relation import Relation
from app.models.space import FamilySpace, SpaceMember
from app.models.user import BASIC_DISCLOSURE_KEYS, User
from app.services import disclosure as disclosure_service
from app.services import source_facts as sf_service
from app.utils import security, timeutil

logger = logging.getLogger(__name__)

# 进程级防重入（与 services/admin_bootstrap._BOOTSTRAP_DONE 同款语义）
_SEED_DONE = False

# ---- 演示数据集常量（PRD 2.2 + 09-05 family-profile-nav-disclosure R3/R6）----

_SEED_SPACE_NAME = "王德海家"
_SEED_LINEAGE_SPACE_NAME = "王氏家族"
_SEED_PIN = "123456"  # 公开 dev 演示值（PRD 红线 4：允许出现在日志）
_SEED_OWNER_NAME = "王德海"
# (姓名, 性别)；gender 枚举见 app.schemas.user.GenderType（m/f/unknown）
_SEED_MEMBERS: tuple[tuple[str, str], ...] = (
    ("王德海", "m"),
    ("周秀英", "f"),
    ("王建军", "m"),
    ("王小雨", "f"),
    ("王远山", "m"),
    ("王小虎", "m"),
)
# 结构化出生日期（solar）；王小虎 2018 年生 = 未成年人（演示 minor 保护 overlay）
_SEED_BIRTHS: dict[str, tuple[int, int, int]] = {
    "王远山": (1940, 5, 12),
    "王德海": (1965, 3, 8),
    "周秀英": (1967, 7, 19),
    "王建军": (1990, 11, 2),
    "王小雨": (1993, 4, 25),
    "王小虎": (2018, 9, 14),
}
# v1 结构边 (from_name, to_name, dir_class)，方向语义同 Relation 模型 docstring
_SEED_EDGES: tuple[tuple[str, str, str], ...] = (
    ("王德海", "周秀英", "spouse"),
    ("王德海", "王远山", "elder"),
    ("王建军", "王德海", "elder"),
    ("王小雨", "王德海", "elder"),
    ("王小虎", "王建军", "elder"),
)


def maybe_seed_demo_data(session: Session) -> bool:
    """启动 preflight 种子入口（lifespan 在 admin bootstrap 之后调用）。

    返回 True 表示本次执行了播种；env 未开启、库非空或进程内已播种时只跳过
    （记日志），零写入。空库判定与多表写入放在同一 ``BEGIN IMMEDIATE`` 事务内，
    消除「检查 → 插入」竞态窗口（同 admin bootstrap 的事务锁模式）。
    """
    global _SEED_DONE
    if config.DEV_SEED_DEMO_DATA != "1":
        logger.debug("dev seed skipped: DEV_SEED_DEMO_DATA != 1")
        return False
    if _SEED_DONE:
        logger.debug("dev seed skipped: already seeded in this process")
        return False
    with command_transaction(session, immediate=True):
        user_count = session.execute(select(func.count()).select_from(User)).scalar_one()
        if user_count:
            logger.info(
                "dev seed skipped: users table not empty (%d row(s)); no data touched",
                user_count,
            )
            return False
        household_id, lineage_id = _seed_demo_family(session)
    _SEED_DONE = True
    # 摘要日志：仅计数 / space_id / 公开演示 PIN；姓名等 PII 不进应用日志
    # （logging-guidelines；演示集本身固定，可按 space_id 查库核对）
    logger.info(
        "dev seed completed: household_id=%d lineage_id=%d members=%d relations=%d "
        "source_facts=%d(×2 spaces); demo PIN=%s (public dev value)",
        household_id,
        lineage_id,
        len(_SEED_MEMBERS),
        len(_SEED_EDGES),
        len(_SEED_EDGES),
        _SEED_PIN,
    )
    return True


def _seed_demo_user(session: Session, *, name: str, gender: str, now: datetime) -> User:
    """单个演示成员；形态契约与 tests/conftest.py create_user_with_pin 保持同步
    （claimed + identity_confirmed + pin_must_change=False，家庭端 PIN 统一；
    birth 用 solar 结构化日期——王小虎为未成年人，演示 minor 保护 overlay）。"""
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
    session.flush()
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


def _seed_demo_family(session: Session) -> tuple[int, int]:
    """在当前事务内写入演示家庭，返回 (household_id, lineage_id)（调用方负责事务/提交）。

    写入顺序 User+Account → 双空间（household 王德海家 / lineage 王氏家族）+成员行 →
    Relation → confirmed SourceFact（每个空间各投影一份，家庭卡与家族树均可渲染）→
    基础五类披露全局开放（成员互见；高敏感保持关闭，R6）。全部走模型约束与
    source_facts / disclosure 服务（含 parent 成环检测），无裸 SQL。
    """
    now = timeutil.utcnow()
    users = {
        name: _seed_demo_user(session, name=name, gender=gender, now=now)
        for name, gender in _SEED_MEMBERS
    }
    session.flush()  # 取得 users.id 供空间/成员/关系引用

    owner = users[_SEED_OWNER_NAME]

    # household 空间（家庭卡）+ lineage 空间（家族树）：双空间模型（PRD R3）
    household = FamilySpace(
        name=_SEED_SPACE_NAME, kind="household", owner_id=owner.id, created_at=now
    )
    lineage = FamilySpace(
        name=_SEED_LINEAGE_SPACE_NAME, kind="lineage", owner_id=owner.id, created_at=now
    )
    session.add_all([household, lineage])
    session.flush()
    for space in (household, lineage):
        for name, _gender in _SEED_MEMBERS:
            _seed_space_member(
                session,
                space_id=space.id,
                user_id=users[name].id,
                added_by=owner.id,
                role="space_admin" if name == _SEED_OWNER_NAME else "member",
                now=now,
            )
    session.flush()

    # 结构边 + confirmed SourceFact：**全局事实（space_id=NULL）**。
    # 成环检测（source_facts._ancestors_within）不按空间隔离，同一亲子事实落两份
    # 会被判环；且 load_graph 对全局事实全空间可见——一份事实即可同时喂饱
    # 家庭卡（household 投影）与家族树（lineage 投影），语义上也更贴近
    # 「血缘关系是全局事实，空间只是可见范围」的领域模型。
    for from_name, to_name, dir_class in _SEED_EDGES:
        edge = _seed_relation(
            session,
            from_user_id=users[from_name].id,
            to_user_id=users[to_name].id,
            dir_class=dir_class,
            now=now,
        )
        fact_type, subject_id, object_id = _map_structural_edge(edge)
        sf_service.create_source_fact(
            session,
            fact_type=fact_type,
            subject_user_id=subject_id,
            object_user_id=object_id,
            space_id=None,
            provenance="connection_accept",
            state=sf_service.FACT_CONFIRMED,
        )

    # 基础五类披露全局开放（R6 成员互见）：经 disclosure 服务正常路径写入；
    # 高敏感类别不写（默认关闭，Q4=b 仅为"可开"）
    for user in users.values():
        disclosure_service.set_basic_disclosure(
            session, user, {key: True for key in BASIC_DISCLOSURE_KEYS}
        )
    return household.id, lineage.id


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
