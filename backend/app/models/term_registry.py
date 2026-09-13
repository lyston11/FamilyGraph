"""TermRegistry 四级称谓表（迁移 0012_term_registry，V2.3 Block E3，KI-4）。

两张表（跨块合同，E4 前端与 Assistant 工具按此消费）：
- TermEntry：四级称谓词条。level 优先级 personal > space > locale > system；
  personal 归属唯一账号（跨空间生效），space 归属空间（可多条 active 别名，
  但同 (space_id, concept_code, term) 唯一），locale/system 为内置包种子。
- TermUsage：称谓使用证据（KI-4 两人晋升的输入）。同账号对同一词条在同一
  空间只计一次使用者（UNIQUE 三元组）；删除即失去晋升资格。

用户原始输入原文存 raw_relation_inputs（models/relationship_facts.py），
本模块任何写入不触碰原文与 SourceFact。
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ---- 枚举常量（服务层与迁移共用；CHECK 约束兜底）----
TERM_LEVELS = ("system", "locale", "space", "personal")
TERM_STATUSES = ("active", "superseded")
USAGE_SOURCE_EVENTS = ("personal_correction", "assistant_query", "manual_select")
# 首批系统内置 locale 包；zh-CN 覆盖 E2 黄金用例 code 集，wu 为方言示例条目
BUILTIN_LOCALES = ("zh-CN", "wu")

# ---- 内置种子数据（迁移与 services.terms.seed_builtin_packs 共用）----
# system：标准称谓兑底，任何空间/语言未命中时最后回退；
# zh-CN：覆盖 E2 黄金用例 code 集 + 常用亲属（含姻亲确定性两跳）；
# wu：方言示例包，首版仅一条演示可扩展性。
BUILTIN_SYSTEM_TERMS = [
    ("SELF", "自己"),
    ("U", "尊亲长"),
    ("Um", "父亲"),
    ("Uf", "母亲"),
    ("D", "子女"),
    ("Dm", "儿子"),
    ("Df", "女儿"),
    ("B", "兄弟姐妹"),
    ("Bm", "兄弟"),
    ("Bf", "姐妹"),
    ("S", "配偶"),
    ("Sm", "丈夫"),
    ("Sf", "妻子"),
    ("P", "伴侣"),
    ("Pm", "伴侣"),
    ("Pf", "伴侣"),
    # scripts/gen_term_pack.py 枚举生成（09-13-steward-term-autofix）：
    # locale 未覆盖结构的书面泛化兜底
    ("U-U", "祖父母"),
    ("U-U-U", "曾祖父母"),
    ("U-D", "兄弟姐妹"),
    ("D-D", "孙子女"),
    ("D-D-D", "曾孙子女"),
    ("S-U", "配偶的父母"),
    ("S-D", "继子女"),
    ("B-S", "兄弟姐妹的配偶"),
    ("B-D", "侄甥"),
]
BUILTIN_ZH_CN_TERMS = [
    ("Um", "爸爸"),
    ("Uf", "妈妈"),
    ("Um-Um", "爷爷"),
    ("Um-Uf", "奶奶"),
    ("Uf-Um", "外公"),
    ("Uf-Uf", "外婆"),
    ("Uf-Uf", "姥姥"),  # 09-13 terminology：同一概念的可核验同义词
    ("Um-Uf-Bm", "舅爷爷"),  # E2 黄金用例：奶奶的兄弟
    ("Uf-Bm", "舅舅"),
    ("Uf-Bf", "姨妈"),
    ("Um-Bm", "叔伯"),  # 父亲兄弟不分伯叔时通用
    ("Um-Bf", "姑姑"),
    ("Uam", "养父"),
    ("Uaf", "养母"),
    ("Usm", "继父"),
    ("Usf", "继母"),
    ("Ug", "监护人"),
    ("Ugm", "监护人"),
    ("Ugf", "监护人"),
    ("Dm", "儿子"),
    ("Df", "女儿"),
    ("D", "子女"),
    ("Bm", "兄弟"),
    ("Bf", "姐妹"),
    ("B", "兄弟姐妹"),
    ("Sm", "丈夫"),
    ("Sf", "妻子"),
    ("S", "配偶"),
    ("Pm", "伴侣"),
    ("Pf", "伴侣"),
    ("P", "伴侣"),
    # 姻亲（经 spouse 延伸的确定性两跳）
    ("Sm-Um", "公公"),
    ("Sm-Uf", "婆婆"),
    ("Sf-Um", "岳父"),
    ("Sf-Uf", "岳母"),
    ("Dm-Sf", "儿媳"),
    ("Df-Sm", "女婿"),
    # ---- 以下由 scripts/gen_term_pack.py 枚举生成（09-13-steward-term-autofix）----
    # 长幼相关码入包为泛化词；解析时长幼消歧（services/terms.py）按出生数据
    # 升级为 哥哥/弟弟/姐姐/妹妹/嫂子/弟媳/姐夫/妹夫。
    ("Dam", "养子"),
    ("Daf", "养女"),
    ("Dsm", "继子"),
    ("Dsf", "继女"),
    ("Um-Dm", "兄弟"),  # 父亲的儿子；长幼消歧类
    ("Um-Df", "姐妹"),  # 父亲的女儿；长幼消歧类
    ("Uf-Dm", "兄弟"),  # 母亲的儿子；长幼消歧类
    ("Uf-Df", "姐妹"),  # 母亲的女儿；长幼消歧类
    ("Dm-Dm", "孙子"),
    ("Dm-Df", "孙女"),
    ("Df-Dm", "外孙"),
    ("Df-Df", "外孙女"),
    ("Um-Sf", "继母"),  # 父亲的妻子（非生母路径）
    ("Um-Sm", "继父"),  # 父亲的丈夫（非生父路径）
    ("Uf-Sm", "继父"),  # 母亲的丈夫（非生父路径）
    ("Uf-Sf", "继母"),  # 母亲的妻子（非生母路径）
    ("Sm-Dm", "继子"),
    ("Sm-Df", "继女"),
    ("Sf-Dm", "继子"),
    ("Sf-Df", "继女"),
    ("Bm-Sf", "兄弟的妻子"),  # 嫂子/弟媳；消歧类
    ("Bf-Sm", "姐妹的丈夫"),  # 姐夫/妹夫；消歧类
    ("Bm-Sm", "兄弟的丈夫"),
    ("Bf-Sf", "姐妹的妻子"),
    ("Bm-Dm", "侄子"),
    ("Bm-Df", "侄女"),
    ("Bf-Dm", "外甥"),
    ("Bf-Df", "外甥女"),
    ("Sm-Bm", "妻子的兄弟"),
    ("Sm-Bf", "妻子的姐妹"),
    ("Sf-Bm", "丈夫的兄弟"),
    ("Sf-Bf", "丈夫的姐妹"),
    ("Um-Um-Um", "曾祖父"),
    ("Um-Um-Uf", "曾祖母"),
    ("Um-Uf-Um", "外曾祖父"),
    ("Um-Uf-Uf", "外曾祖母"),
    ("Uf-Um-Um", "外曾祖父"),
    ("Uf-Um-Uf", "外曾祖母"),
    ("Uf-Uf-Um", "外曾祖父"),
    ("Uf-Uf-Uf", "外曾祖母"),
    ("Dm-Dm-Dm", "曾孙"),
    ("Dm-Dm-Df", "曾孙女"),
    ("Dm-Df-Dm", "外曾孙"),
    ("Dm-Df-Df", "外曾孙女"),
    ("Df-Dm-Dm", "外曾孙"),
    ("Df-Dm-Df", "外曾孙女"),
    ("Df-Df-Dm", "外曾孙"),
    ("Df-Df-Df", "外曾孙女"),
    ("Um-Dm-Sf", "兄弟的妻子"),  # 经父母链同胞的配偶；消歧类
    ("Um-Df-Sm", "姐妹的丈夫"),  # 消歧类（姐夫/妹夫）
    ("Uf-Dm-Sf", "兄弟的妻子"),  # 消歧类
    ("Uf-Df-Sm", "姐妹的丈夫"),  # 消歧类
    ("Um-Bf-Sm", "姑父"),
    ("Uf-Bm-Sf", "舅妈"),
    ("Uf-Bf-Sm", "姨父"),
    ("Um-Bm-Dm", "堂兄弟"),
    ("Um-Bm-Df", "堂姐妹"),
    ("Um-Bf-Dm", "表兄弟"),
    ("Um-Bf-Df", "表姐妹"),
    ("Uf-Bm-Dm", "表兄弟"),
    ("Uf-Bm-Df", "表姐妹"),
    ("Uf-Bf-Dm", "表兄弟"),
    ("Uf-Bf-Df", "表姐妹"),
    ("Um-Um-Um-Um", "高祖父"),
    ("Um-Um-Um-Uf", "高祖母"),
    ("Uf-Um-Um-Um", "外高祖父"),
    ("Uf-Um-Um-Uf", "外高祖母"),
    ("Dm-Dm-Dm-Dm", "玄孙"),
    ("Dm-Dm-Dm-Df", "玄孙女"),
    ("Df-Dm-Dm-Dm", "外玄孙"),
    ("Df-Dm-Dm-Df", "外玄孙女"),
    # 性别未知的中性泛化
    ("U-U", "祖父母"),
    ("U-U-U", "曾祖父母"),
    ("D-D", "孙子女"),
    ("D-D-D", "曾孙子女"),
]
BUILTIN_WU_TERMS = [
    ("Um", "阿爷"),
]
# 归一化种子清单：(level, locale, concept_code, term)
BUILTIN_TERM_SEEDS = (
    [("system", None, code, term) for code, term in BUILTIN_SYSTEM_TERMS]
    + [("locale", "zh-CN", code, term) for code, term in BUILTIN_ZH_CN_TERMS]
    + [("locale", "wu", code, term) for code, term in BUILTIN_WU_TERMS]
)

_TERM_MAX_LENGTH = 64


def _check_in(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({', '.join(repr(v) for v in values)})", name=name)


class TermEntry(Base):
    """四级称谓词条：active 生效，superseded 保留 revision 链（历史可追溯）。

    - personal：owner_account_id 必填；(owner_account_id, concept_code) 至多一条
      active 行（partial unique index），旧值 superseded 保留。
    - space：space_id 必填；同一 (space_id, concept_code) 允许多条 active 别名，
      同 term 唯一（partial unique index）。晋升由两人 usage 规则自动产生，
      无管理员发布路径；不复制到 locale/system。
    - locale / system：locale 列承载包名（system 层为 NULL），迁移种子写入。
    """

    __tablename__ = "term_entries"
    __table_args__ = (
        _check_in("level", TERM_LEVELS, "ck_te_level"),
        _check_in("status", TERM_STATUSES, "ck_te_status"),
        # personal：每账号每概念至多一条 active 词条（superseded 历史不受限）
        Index(
            "uq_term_entries_personal_active",
            "owner_account_id",
            "concept_code",
            unique=True,
            sqlite_where=sa.text("level = 'personal' AND status = 'active'"),
        ),
        # space：同空间同概念同词至多一条 active 别名
        Index(
            "uq_term_entries_space_active",
            "space_id",
            "concept_code",
            "term",
            unique=True,
            sqlite_where=sa.text("level = 'space' AND status = 'active'"),
        ),
        Index("ix_term_entries_space_level", "space_id", "level"),
        Index("ix_term_entries_concept", "concept_code"),
        Index("ix_term_entries_owner", "owner_account_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # E2 resolver 的 concept_code 编码合同（services/relationship_resolver.py）
    concept_code: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    space_id: Mapped[int | None] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=True
    )
    owner_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
    locale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    term: Mapped[str] = mapped_column(String(_TERM_MAX_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TermEntry {self.id} {self.level}/{self.locale} {self.concept_code}"
            f"={self.term!r} {self.status} r{self.revision}>"
        )


class TermUsage(Base):
    """使用证据：某账号在某空间选择了某词条叫法（幂等去重后插入）。

    同一账号重复选择不计第二位使用者（UNIQUE 三元组，KI-4）；两位不同
    identity_confirmed 账号的有效 usage 使对应 space 候选词自动晋升
    （services/terms.py recompute_space_promotion）。
    """

    __tablename__ = "term_usages"
    __table_args__ = (
        _check_in("source_event", USAGE_SOURCE_EVENTS, "ck_tu_source_event"),
        Index(
            "uq_term_usages_entry_account_space",
            "term_entry_id",
            "account_id",
            "space_id",
            unique=True,
        ),
        Index("ix_term_usages_account", "account_id"),
        Index("ix_term_usages_space", "space_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    term_entry_id: Mapped[int] = mapped_column(
        ForeignKey("term_entries.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    source_event: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TermUsage {self.id} entry={self.term_entry_id} account={self.account_id}"
            f" space={self.space_id} {self.source_event}>"
        )
