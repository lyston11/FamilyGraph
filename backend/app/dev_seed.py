"""开发演示数据种子与一次性清库 CLI（09-05 dev-seed-demo-data；09-13 明皇室数据集）。

自动播种（app.main lifespan 在 admin bootstrap 之后调用，进程级单次）：
- 门控：``DEV_SEED_DEMO_DATA=1``（默认 "0" 关闭）+ 进程级单次防重入；未开启或
  本进程已执行过时只跳过并记日志，绝不变更已有数据；
- 播种语义为「固定清单 + 增量补缺（insert-only 收敛）」：不要求空库，每次启动
  在同一 ``BEGIN IMMEDIATE`` 事务内把固定清单与库内现状逐行比对，缺什么补什么；
  任何已存在行绝不 UPDATE/DELETE（红线）——常量清单里新加成员/关系后重启即自动
  补齐，无需清库重播；要彻底重置仍走 ``--reset``；
- 演示数据集「明皇室 + 九户外戚世家」：51 名可登录成员（PIN 统一 123456，公开
  dev 演示值，全部含结构化出生日期；生卒为演示用近似换算，全员历史人物、无
  未成年人）、spouse/elder 结构边及其 confirmed SourceFact 映射（全局一份，
  家庭卡与家族树均可投影渲染）、十对空间（household+lineage ×10）——
  明皇室/朱氏皇族（朱元璋）、马府/马氏家族（马皇后）、徐达家/徐氏家族（徐达）、
  常府/常氏家族（常遇春）、吕府/吕氏家族（吕本）、梅府/梅氏家族（梅思祖）、
  张家/张氏家族（张麒）、孙家/孙氏家族（孙忠）、钱家/钱氏家族（钱贵）、
  李家/李氏家族（李贞）；帝室成员以 member 身份进入各自外戚本家（朱标进常府/
  吕府、朱高炽进张家、朱瞻基进孙家、朱祁镇进钱家、朱元璋进李家、宁国公主进
  梅府），演示跨空间成员资格与「当前家族空间」切换；基础五类披露全局开放
  （高敏感保持关闭）；
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
朱世珍/陈氏 是 朱元璋、朱兴隆、朱佛女 的父母；朱元璋⇄马皇后 为配偶；朱标/朱樉/
朱棡/朱棣/朱橚/宁国公主/安庆公主 是 朱元璋 与 马皇后 的子女；朱文正 是 朱兴隆
之子；朱允炆（母吕氏）/朱允熥（母常氏）是 朱标 之子；朱高炽/朱高煦/朱高燧 是
朱棣 与 徐皇后 之子；朱瞻基/朱瞻墡 是 朱高炽 与 张皇后 之子；朱祁镇 是 朱瞻基
与 孙皇后 之子、朱祁钰 是 朱瞻基 之子；马皇后 是 马公 与 郑氏 之女；徐皇后/
徐辉祖/徐增寿 是 徐达 与 谢氏 的子女；常氏/常茂/常升 是 常遇春 与 蓝氏 的子女；
吕氏 是 吕本 之女；张皇后/张昶 是 张麒 的子女；孙皇后/孙继宗 是 孙忠 的子女；
钱皇后 是 钱贵 之女；朱佛女（朱元璋长姐）⇄李贞 为配偶，李文忠 是二人之子，
李景隆 是 李文忠 之子——六世帝系 + 九户外戚。
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
from app.services import personal_family_view
from app.services import source_facts as sf_service
from app.utils import security, timeutil

logger = logging.getLogger(__name__)

# 进程级防重入（与 services/admin_bootstrap._BOOTSTRAP_DONE 同款语义）
_SEED_DONE = False

# ---- 演示数据集常量（09-13 起为明皇室数据集）----

_SEED_PIN = "123456"  # 公开 dev 演示值（PRD 红线 4：允许出现在日志）

# 空间配对清单：(household 名, lineage 名, 空间管理员)。household=家庭卡投影、
# lineage=家族树投影；本次新建的 household 会显式配对所属 lineage
# （lineage_space_id，「当前家族空间」切换的数据基础）。
_SEED_SPACE_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("明皇室", "朱氏皇族", "朱元璋"),
    ("马府", "马氏家族", "马皇后"),
    ("徐达家", "徐氏家族", "徐达"),
    # 外戚世家：帝室成员以 member 身份进入本家 household，本家 lineage 收全族
    ("常府", "常氏家族", "常遇春"),
    ("吕府", "吕氏家族", "吕本"),
    ("梅府", "梅氏家族", "梅思祖"),
    ("张家", "张氏家族", "张麒"),
    ("孙家", "孙氏家族", "孙忠"),
    ("钱家", "钱氏家族", "钱贵"),
    ("李家", "李氏家族", "李贞"),
)

# 每空间名册 (姓名, 性别)；gender 枚举见 app.schemas.user.GenderType。同一用户
# 可进多个空间（跨空间成员资格），用户全集按名去重、按首见序建户（朱元璋为
# 1 号演示用户，smoke 附件用例依赖此约定）。「明皇室」= 帝后核心家庭卡 6 人
# （皇考+帝后+太子/燕王+皇太孙）；「朱氏皇族」= 六世宗室全集（其余旁系与姻亲
# 只进家族空间，演示双空间投影差异）；其余九对为外戚本家：household 2 人
# （本家 admin + 帝室 member），lineage 收全族。
_SEED_SPACE_MEMBERS: dict[str, tuple[tuple[str, str], ...]] = {
    "明皇室": (
        ("朱元璋", "m"),
        ("马皇后", "f"),
        ("朱标", "m"),
        ("朱棣", "m"),
        ("朱世珍", "m"),
        ("朱允炆", "m"),
    ),
    "朱氏皇族": (
        ("朱元璋", "m"),
        ("马皇后", "f"),
        ("朱标", "m"),
        ("朱棣", "m"),
        ("朱世珍", "m"),
        ("朱允炆", "m"),
        ("陈氏", "f"),
        ("朱兴隆", "m"),
        ("朱文正", "m"),
        ("朱樉", "m"),
        ("朱棡", "m"),
        ("朱橚", "m"),
        ("宁国公主", "f"),
        ("安庆公主", "f"),
        ("常氏", "f"),
        ("吕氏", "f"),
        ("徐皇后", "f"),
        ("梅殷", "m"),
        ("欧阳伦", "m"),
        ("朱允熥", "m"),
        ("朱高炽", "m"),
        ("朱高煦", "m"),
        ("朱高燧", "m"),
        ("张皇后", "f"),
        ("朱瞻基", "m"),
        ("朱瞻墡", "m"),
        ("孙皇后", "f"),
        ("朱祁镇", "m"),
        ("朱祁钰", "m"),
        ("钱皇后", "f"),
    ),
    "马府": (
        ("马皇后", "f"),
        ("朱元璋", "m"),
    ),
    "马氏家族": (
        ("马皇后", "f"),
        ("朱元璋", "m"),
        ("马公", "m"),
        ("郑氏", "f"),
    ),
    "徐达家": (
        ("徐达", "m"),
        ("朱棣", "m"),
    ),
    "徐氏家族": (
        ("徐达", "m"),
        ("朱棣", "m"),
        ("谢氏", "f"),
        ("徐辉祖", "m"),
        ("徐增寿", "m"),
        ("徐皇后", "f"),
    ),
    # 常氏家族：开平忠武王常遇春（太子元妃常氏本家），朱标以 member 跨空间
    "常府": (
        ("常遇春", "m"),
        ("朱标", "m"),
    ),
    "常氏家族": (
        ("常遇春", "m"),
        ("朱标", "m"),
        ("蓝氏", "f"),
        ("常茂", "m"),
        ("常升", "m"),
        ("常氏", "f"),
    ),
    # 吕氏家族：继妃吕氏本家（吕本），朱标以 member 跨空间
    "吕府": (
        ("吕本", "m"),
        ("朱标", "m"),
    ),
    "吕氏家族": (
        ("吕本", "m"),
        ("朱标", "m"),
        ("吕氏", "f"),
    ),
    # 梅氏家族：汝南侯梅思祖与驸马梅殷（史载为从子，父名不可考——不落父子
    # 事实，宁国公主以 member 跨空间，演示无边空间渲染路径）
    "梅府": (
        ("梅思祖", "m"),
        ("宁国公主", "f"),
    ),
    "梅氏家族": (
        ("梅思祖", "m"),
        ("宁国公主", "f"),
        ("梅殷", "m"),
    ),
    # 张氏家族：诚孝张皇后本家（张麒/张昶），朱高炽以 member 跨空间
    "张家": (
        ("张麒", "m"),
        ("朱高炽", "m"),
    ),
    "张氏家族": (
        ("张麒", "m"),
        ("朱高炽", "m"),
        ("张昶", "m"),
        ("张皇后", "f"),
    ),
    # 孙氏家族：孝恭孙皇后本家（孙忠/孙继宗，夺门之变会昌侯），朱瞻基跨空间
    "孙家": (
        ("孙忠", "m"),
        ("朱瞻基", "m"),
    ),
    "孙氏家族": (
        ("孙忠", "m"),
        ("朱瞻基", "m"),
        ("孙继宗", "m"),
        ("孙皇后", "f"),
    ),
    # 钱氏家族：孝庄钱皇后本家（钱贵），朱祁镇以 member 跨空间
    "钱家": (
        ("钱贵", "m"),
        ("朱祁镇", "m"),
    ),
    "钱氏家族": (
        ("钱贵", "m"),
        ("朱祁镇", "m"),
        ("钱皇后", "f"),
    ),
    # 李氏家族：曹国长公主朱佛女（朱元璋长姐）⇄李贞，李文忠/李景隆两代曹国公；
    # 朱元璋以 member 跨空间（兄妹关系由全局亲子事实推导渲染）
    "李家": (
        ("李贞", "m"),
        ("朱元璋", "m"),
    ),
    "李氏家族": (
        ("李贞", "m"),
        ("朱元璋", "m"),
        ("朱佛女", "f"),
        ("李文忠", "m"),
        ("李景隆", "m"),
    ),
}

# 用户全集不设 import 期派生常量：播种函数内由名册常量现算（跨空间成员并集
# 按名去重，全部可登录，不得重复建户）——避免将来只改名册、全集派生不同步的坑。
# 结构化出生日期（solar）；生卒为演示用近似换算（明代纪年各源存在差异），
# 全员历史人物、无未成年人（minor 保护 overlay 由测试夹具单独覆盖）。
_SEED_BIRTHS: dict[str, tuple[int, int, int]] = {
    "朱世珍": (1283, 9, 25),
    "陈氏": (1286, 3, 18),
    "朱兴隆": (1310, 5, 21),
    "马公": (1305, 3, 3),
    "郑氏": (1308, 9, 9),
    "朱元璋": (1328, 10, 21),
    "马皇后": (1332, 8, 19),
    "朱文正": (1336, 8, 11),
    "朱标": (1355, 10, 10),
    "常氏": (1355, 12, 3),
    "朱樉": (1356, 12, 3),
    "朱棡": (1358, 12, 18),
    "吕氏": (1358, 10, 1),
    "朱棣": (1360, 5, 2),
    "梅殷": (1360, 11, 17),
    "朱橚": (1361, 10, 8),
    "徐皇后": (1362, 3, 6),
    "欧阳伦": (1363, 6, 25),
    "宁国公主": (1364, 10, 20),
    "安庆公主": (1366, 2, 8),
    "朱允炆": (1377, 12, 5),
    "朱允熥": (1378, 11, 9),
    "朱高炽": (1378, 8, 16),
    "张皇后": (1379, 4, 12),
    "朱高煦": (1380, 12, 30),
    "朱高燧": (1383, 1, 19),
    "朱瞻基": (1398, 3, 16),
    "孙皇后": (1399, 5, 21),
    "朱瞻墡": (1406, 4, 4),
    "朱祁镇": (1427, 11, 29),
    "钱皇后": (1426, 8, 12),
    "朱祁钰": (1428, 9, 11),
    "徐达": (1332, 11, 11),
    "谢氏": (1335, 7, 4),
    "徐辉祖": (1368, 4, 17),
    "徐增寿": (1372, 5, 23),
    # 外戚世家（生卒多为演示近似）
    "常遇春": (1330, 8, 7),
    "蓝氏": (1332, 5, 5),
    "常茂": (1356, 10, 9),
    "常升": (1358, 3, 15),
    "吕本": (1325, 4, 12),
    "梅思祖": (1327, 9, 9),
    "张麒": (1341, 6, 6),
    "张昶": (1381, 2, 17),
    "孙忠": (1376, 10, 10),
    "孙继宗": (1400, 8, 18),
    "钱贵": (1405, 3, 3),
    "李贞": (1302, 11, 11),
    "朱佛女": (1326, 7, 21),
    "李文忠": (1339, 7, 18),
    "李景隆": (1367, 4, 12),
}

# v1 结构边 (from_name, to_name, dir_class)，方向语义同 Relation 模型 docstring
# （elder=to_user 是 from_user 的长辈/父辈；spouse 对称）
_SEED_EDGES: tuple[tuple[str, str, str], ...] = (
    # 祖辈与旁系：朱世珍/陈氏 二子（朱元璋、朱兴隆），朱文正是朱兴隆之子
    ("朱兴隆", "朱世珍", "elder"),
    ("朱兴隆", "陈氏", "elder"),
    ("朱元璋", "朱世珍", "elder"),
    ("朱元璋", "陈氏", "elder"),
    ("朱文正", "朱兴隆", "elder"),
    # 帝后：朱元璋⇄马皇后
    ("朱元璋", "马皇后", "spouse"),
    # 帝后子女（明皇室核心）
    ("朱标", "朱元璋", "elder"),
    ("朱标", "马皇后", "elder"),
    ("朱樉", "朱元璋", "elder"),
    ("朱樉", "马皇后", "elder"),
    ("朱棡", "朱元璋", "elder"),
    ("朱棡", "马皇后", "elder"),
    ("朱棣", "朱元璋", "elder"),
    ("朱棣", "马皇后", "elder"),
    ("朱橚", "朱元璋", "elder"),
    ("朱橚", "马皇后", "elder"),
    ("宁国公主", "朱元璋", "elder"),
    ("宁国公主", "马皇后", "elder"),
    ("安庆公主", "朱元璋", "elder"),
    ("安庆公主", "马皇后", "elder"),
    # 太子朱标两房：元妃常氏（朱允熥）、继妃吕氏（朱允炆）
    ("朱标", "常氏", "spouse"),
    ("朱标", "吕氏", "spouse"),
    ("朱允炆", "朱标", "elder"),
    ("朱允炆", "吕氏", "elder"),
    ("朱允熥", "朱标", "elder"),
    ("朱允熥", "常氏", "elder"),
    # 燕王朱棣一系：⇄徐皇后，三子
    ("朱棣", "徐皇后", "spouse"),
    ("朱高炽", "朱棣", "elder"),
    ("朱高炽", "徐皇后", "elder"),
    ("朱高煦", "朱棣", "elder"),
    ("朱高煦", "徐皇后", "elder"),
    ("朱高燧", "朱棣", "elder"),
    ("朱高燧", "徐皇后", "elder"),
    # 公主联姻
    ("梅殷", "宁国公主", "spouse"),
    ("欧阳伦", "安庆公主", "spouse"),
    # 仁宗朱高炽⇄诚孝张皇后，二子
    ("朱高炽", "张皇后", "spouse"),
    ("朱瞻基", "朱高炽", "elder"),
    ("朱瞻基", "张皇后", "elder"),
    ("朱瞻墡", "朱高炽", "elder"),
    ("朱瞻墡", "张皇后", "elder"),
    # 宣宗朱瞻基⇄孙皇后；英宗朱祁镇⇄钱皇后（代宗朱祁钰生母不落事实）
    ("朱瞻基", "孙皇后", "spouse"),
    ("朱祁镇", "朱瞻基", "elder"),
    ("朱祁镇", "孙皇后", "elder"),
    ("朱祁钰", "朱瞻基", "elder"),
    ("朱祁镇", "钱皇后", "spouse"),
    # 马氏家族：马皇后本家
    ("马皇后", "马公", "elder"),
    ("马皇后", "郑氏", "elder"),
    ("马公", "郑氏", "spouse"),
    # 徐氏家族：徐皇后本家
    ("徐达", "谢氏", "spouse"),
    ("徐皇后", "徐达", "elder"),
    ("徐皇后", "谢氏", "elder"),
    ("徐辉祖", "徐达", "elder"),
    ("徐辉祖", "谢氏", "elder"),
    ("徐增寿", "徐达", "elder"),
    ("徐增寿", "谢氏", "elder"),
    # 常氏家族：常遇春⇄蓝氏，常氏/常茂/常升 三子女
    ("常遇春", "蓝氏", "spouse"),
    ("常氏", "常遇春", "elder"),
    ("常氏", "蓝氏", "elder"),
    ("常茂", "常遇春", "elder"),
    ("常茂", "蓝氏", "elder"),
    ("常升", "常遇春", "elder"),
    ("常升", "蓝氏", "elder"),
    # 吕氏家族：吕氏 是 吕本 之女
    ("吕氏", "吕本", "elder"),
    # 张氏家族：张皇后/张昶 是 张麒 的子女
    ("张皇后", "张麒", "elder"),
    ("张昶", "张麒", "elder"),
    # 孙氏家族：孙皇后/孙继宗 是 孙忠 的子女
    ("孙皇后", "孙忠", "elder"),
    ("孙继宗", "孙忠", "elder"),
    # 钱氏家族：钱皇后 是 钱贵 之女
    ("钱皇后", "钱贵", "elder"),
    # 李氏家族：朱佛女（朱元璋长姐）⇄李贞，李文忠/李景隆 两代
    ("朱佛女", "朱世珍", "elder"),
    ("朱佛女", "陈氏", "elder"),
    ("李贞", "朱佛女", "spouse"),
    ("李文忠", "李贞", "elder"),
    ("李文忠", "朱佛女", "elder"),
    ("李景隆", "李文忠", "elder"),
)


class _SeedOutcome(NamedTuple):
    """一次清单收敛的结果计数（仅供摘要日志；不含姓名等 PII）。"""

    manifest_users: int
    added_users: int
    added_spaces: int
    added_memberships: int
    added_relations: int
    space_ids: tuple[tuple[str, int], ...]
    initialized_views: int = 0

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
            "dev seed completed (full): %s members=%d relations=%d source_facts=%d "
            "pfv_views=%d; demo PIN=%s (public dev value)",
            " ".join(f"{name}={space_id}" for name, space_id in outcome.space_ids),
            outcome.manifest_users,
            len(_SEED_EDGES),
            len(_SEED_EDGES),
            outcome.initialized_views,
            _SEED_PIN,
        )
    elif outcome.wrote_anything:
        # 增量补缺：只报计数，姓名等 PII 不进日志（logging-guidelines 既有约定）
        logger.info(
            "dev seed completed (incremental): added users=%d spaces=%d "
            "memberships=%d relations=%d pfv_views=%d",
            outcome.added_users,
            outcome.added_spaces,
            outcome.added_memberships,
            outcome.added_relations,
            outcome.initialized_views,
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
    """按空间名精确匹配既有空间（二十个种子空间名足够独特，取 id 最小者）。"""
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
    birth 用 solar 结构化日期——明皇室数据集全员历史人物，无未成年人）。"""
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
    - 空间：按 name 精确匹配；缺失才按常量创建（owner 用配对管理员同名用户
      id），既有空间不动（含 owner_id）；**本次新建**的 household 在同事务内
      显式挂入配对 lineage（lineage_space_id），既有空间即使未配对也不改
      （存量回填由迁移 0035 + 前端 owner 回退兜底）；
    - 成员行：按 (space_id, user_id) 匹配；无行才插入，role 按常量（配对管理员
      名单 → space_admin，其余 member），已有行哪怕 role 不同也不动；
    - 关系边：任一方向已有 pending/active 边即视为已覆盖（口径覆盖两条
      partial unique 索引），跳过且不为既有边补建 fact；缺失才插入 Relation
      并随新建一起落 confirmed 全局 SourceFact（同元组全局事实已存在时只补
      关系，避免 create_source_fact 409 回滚整个事务）；
    - 披露：基础五类全局开放只对**本次新建**的用户设置。

    写入顺序 User+Account → 十对空间（明皇室/朱氏皇族 + 九户外戚本家，新建
    household 即时落配对）+成员行 → Relation → confirmed SourceFact（**全局
    事实** space_id=NULL，一份喂饱所有空间投影）。
    全部走模型约束与 source_facts / disclosure 服务（含 parent 成环检测），
    无裸 SQL。
    """
    now = timeutil.utcnow()
    # 用户全集：跨空间名册按名去重（首见序建户；同姓名册性别以首见为准）
    all_members: list[tuple[str, str]] = []
    seen_names: set[str] = set()
    for roster in _SEED_SPACE_MEMBERS.values():
        for name, gender in roster:
            if name not in seen_names:
                seen_names.add(name)
                all_members.append((name, gender))

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
    spaces: dict[str, FamilySpace] = {}
    new_space_names: set[str] = set()
    for household_name, lineage_name, admin_name in _SEED_SPACE_PAIRS:
        for space_name, kind in ((household_name, "household"), (lineage_name, "lineage")):
            space = _find_seed_space(session, space_name)
            if space is None:
                # 十对空间：家庭卡（household）/ 家族树（lineage）× 明皇室 +
                # 九户外戚——多空间模型（跨空间成员资格 + 家族切换场景）
                space = FamilySpace(
                    name=space_name, kind=kind, owner_id=users[admin_name].id, created_at=now
                )
                session.add(space)
                added_spaces += 1
                new_space_names.add(space_name)
            spaces[space_name] = space
    session.flush()

    # 家族配对（仅本次新建的 household；既有行绝不 UPDATE——insert-only 红线）
    for household_name, lineage_name, _admin_name in _SEED_SPACE_PAIRS:
        household = spaces[household_name]
        if household_name in new_space_names and household.lineage_space_id is None:
            household.lineage_space_id = spaces[lineage_name].id

    added_memberships = 0
    for household_name, lineage_name, admin_name in _SEED_SPACE_PAIRS:
        for space_name in (household_name, lineage_name):
            space = spaces[space_name]
            for name, _gender in _SEED_SPACE_MEMBERS[space_name]:
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
    # PFV 视图行（09-13 缺口修复）：种子直接插库、不发注册/成员资格事件，
    # personal_family_views 行若不在此初始化，换数据集后家族树会永远停在
    # never_computed（重算作业没有行可重建，登记的作业空转成功）。幂等初始化
    # 为 queued 行，由 steward worker 按既有机制重算收敛，语义与注册流一致。
    initialized_views = 0
    for user in users.values():
        account_id = session.scalar(select(Account.id).where(Account.user_id == user.id))
        if account_id is None:
            continue
        initialized_views += personal_family_view.initialize_account_views(
            session, account_id=account_id, user_id=user.id
        )
    return _SeedOutcome(
        manifest_users=len(all_members),
        added_users=added_users,
        added_spaces=added_spaces,
        added_memberships=added_memberships,
        added_relations=added_relations,
        initialized_views=initialized_views,
        space_ids=tuple(
            (space_name, spaces[space_name].id)
            for household_name, lineage_name, _admin_name in _SEED_SPACE_PAIRS
            for space_name in (household_name, lineage_name)
        ),
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
