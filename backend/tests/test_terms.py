"""TermRegistry 四级称谓、使用证据与空间晋升测试（V2.3 Block E3；KI-4/KI-5）。

覆盖（对齐任务验收 AC-KI5/AC-KI6 与分派合同）：
- 四级优先级矩阵：每层单独命中与叠加覆盖、无命中回退 None（结构描述由
  组合层兜底）、内置种子包（system/zh-CN/wu）存在性；
- 跨空间不同称谓：同人两空间不同 space 词条，personal 全空间生效；
- personal 纠正：立即反映在 resolve 输出、term.personal_updated 领域事件、
  不产生 SourceFact 变更、raw_relation_inputs 原文不受任何 term 写入影响、
  其他账号/空间不受污染；
- 晋升规则：两个不同确档账号 usage 自动 active；第二个 provisional 不晋升；
  撤销一条 usage / 成员退出后降级 superseded；同账号重复只计一次；
  管理员角色无关（无审批路径断言）；
- API 合同：flag off 全端点 503；PUT/GET my 校验；resolve 主路径/称谓/
  来源级别/替代路径/事实状态摘要；usages 幂等与晋升触发。
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import (
    auth_header,
    create_agent_fixture,
    create_space_member,
    create_user_with_pin,
    login,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models.relationship_facts import RawRelationInput, SourceFact
from app.models.term_registry import TermEntry, TermUsage
from app.models.v2_foundation import DomainEvent
from app.services import source_facts as sf
from app.services import terms
from app.utils.timeutil import utcnow

# ---- 造数辅助 ----


@pytest.fixture(autouse=True)
def _seed_builtin_packs(db_session: Session) -> None:
    """清表夹具会连带清掉迁移种子；本文件所有测试先幂等重灌内置包。"""
    terms.seed_builtin_packs(db_session)
    db_session.commit()  # 立即释放写锁，避免与 TestClient 请求连接互斥


def _person(session: Session, space_id: int | None, name: str, gender: str, **kwargs: Any):
    """建人并加入空间（active 成员，保证可见与晋升资格）。"""
    user = create_user_with_pin(session, name, "123456", gender=gender, **kwargs)
    if space_id is not None:
        create_space_member(session, space_id, user.id)
    return user


def _confirm_parent(
    session: Session,
    subject_id: int,
    object_id: int,
    *,
    fact_type: str = "biological_parent",
    space_id: int | None = None,
) -> SourceFact:
    fact = sf.create_source_fact(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, fact, "confirm")
    session.commit()  # 立即释放写锁（API 测试中 TestClient 用另一连接登录/请求）
    return fact


def _space_entry(
    session: Session,
    *,
    space_id: int,
    concept_code: str,
    term: str,
    status: str = "active",
) -> TermEntry:
    entry = TermEntry(
        concept_code=concept_code,
        level="space",
        space_id=space_id,
        owner_account_id=None,
        locale=None,
        term=term,
        status=status,
        revision=1,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(entry)
    session.commit()
    return entry


def _term_events(session: Session, *, event_type: str | None = None) -> list[DomainEvent]:
    session.flush()
    stmt = select(DomainEvent).where(DomainEvent.aggregate_type == terms.AGGREGATE_TYPE)
    if event_type is not None:
        stmt = stmt.where(DomainEvent.type == event_type)
    return list(session.scalars(stmt.order_by(DomainEvent.id)).all())


# ---- 内置种子包 ----


def test_builtin_seed_packs_present(db_session: Session) -> None:
    """迁移种子：system 兜底集 + zh-CN 覆盖黄金用例 + 一个方言示例条目。"""
    system = {
        e.concept_code: e.term
        for e in db_session.scalars(select(TermEntry).where(TermEntry.level == "system"))
    }
    zh = {
        e.concept_code: e.term
        for e in db_session.scalars(
            select(TermEntry).where(TermEntry.level == "locale", TermEntry.locale == "zh-CN")
        )
    }
    # E2 黄金用例 code 集（relationship_resolver docstring）
    golden = [
        "Um",
        "Uf",
        "Um-Um",
        "Uf-Um",
        "Um-Uf",
        "Um-Uf-Bm",
        "Uf-Bm",
        "Uam",
        "Usf",
        "Dm",
        "Sm",
        "Sf",
        "Pm",
        "Bm",
    ]
    assert all(code in system for code in ("SELF", "U", "D"))
    assert all(code in zh for code in golden)
    assert zh["Um"] == "爸爸" and zh["Um-Uf-Bm"] == "舅爷爷"
    assert len(zh) > len(system)  # locale 包为主力，system 仅兜底
    wu = list(
        db_session.scalars(
            select(TermEntry).where(TermEntry.level == "locale", TermEntry.locale == "wu")
        )
    )
    assert len(wu) >= 1  # 方言示例条目（可扩展注册表）


# ---- 四级优先级矩阵 ----


def test_priority_system_only(db_session: Session) -> None:
    user, space = create_agent_fixture(db_session, name="系1")
    resolved = terms.resolve_term(
        db_session, account_id=user.account.id, space_id=space.id, concept_code="SELF"
    )
    assert (resolved.term, resolved.source_level) == ("自己", "system")


def test_priority_locale_over_system(db_session: Session) -> None:
    user, space = create_agent_fixture(db_session, name="系2")
    resolved = terms.resolve_term(
        db_session, account_id=user.account.id, space_id=space.id, concept_code="Um"
    )
    assert (resolved.term, resolved.source_level) == ("爸爸", "locale")


def test_priority_space_over_locale_and_personal_over_all(db_session: Session) -> None:
    user, space = create_agent_fixture(db_session, name="系3")
    _space_entry(db_session, space_id=space.id, concept_code="Um", term="爹地")
    resolved = terms.resolve_term(
        db_session, account_id=user.account.id, space_id=space.id, concept_code="Um"
    )
    assert (resolved.term, resolved.source_level) == ("爹地", "space")

    entry = terms.set_personal_term(
        db_session,
        account_id=user.account.id,
        space_id=space.id,
        concept_code="Um",
        term="老爸",
    )
    resolved = terms.resolve_term(
        db_session, account_id=user.account.id, space_id=space.id, concept_code="Um"
    )
    assert (resolved.term, resolved.source_level, resolved.entry_id) == (
        "老爸",
        "personal",
        entry.id,
    )


def test_priority_no_hit_returns_none_for_structural_fallback(db_session: Session) -> None:
    """四级全未命中 → source_level=None；组合层用结构描述兜底。"""
    user, space = create_agent_fixture(db_session, name="系4")
    resolved = terms.resolve_term(
        db_session, account_id=user.account.id, space_id=space.id, concept_code="Um-Um-Um-Um"
    )
    assert resolved.term is None and resolved.source_level is None and resolved.entry_id is None


def test_cross_space_different_terms_same_person(db_session: Session) -> None:
    """AC-KI5：同一人物进入不同空间显示不同称谓（space 层隔离）。"""
    viewer_a, space_a = create_agent_fixture(db_session, name="跨A")
    _, space_b = create_agent_fixture(db_session, name="跨B")
    target = _person(db_session, None, "张父", "m")
    create_space_member(db_session, space_a.id, target.id)
    create_space_member(db_session, space_b.id, target.id)
    _space_entry(db_session, space_id=space_a.id, concept_code="Um", term="爹地")

    in_a = terms.resolve_term(
        db_session, account_id=viewer_a.account.id, space_id=space_a.id, concept_code="Um"
    )
    in_b = terms.resolve_term(
        db_session, account_id=viewer_a.account.id, space_id=space_b.id, concept_code="Um"
    )
    assert in_a.term == "爹地" and in_a.source_level == "space"
    assert in_b.term == "爸爸" and in_b.source_level == "locale"

    # personal 层跟随账号跨两个空间生效（最高优先级），但不污染他人视图
    other_viewer = _person(db_session, space_a.id, "旁人", "f")
    terms.set_personal_term(
        db_session,
        account_id=viewer_a.account.id,
        space_id=space_a.id,
        concept_code="Um",
        term="老爸",
    )
    for sid in (space_a.id, space_b.id):
        mine = terms.resolve_term(
            db_session, account_id=viewer_a.account.id, space_id=sid, concept_code="Um"
        )
        theirs = terms.resolve_term(
            db_session, account_id=other_viewer.account.id, space_id=sid, concept_code="Um"
        )
        assert (mine.term, mine.source_level) == ("老爸", "personal")
        assert theirs.term == ("爹地" if sid == space_a.id else "爸爸")


# ---- personal 纠正（AC-KI6 与 AC-KI3 原文保护）----


def test_personal_correction_event_revision_chain_and_no_source_fact(db_session: Session) -> None:
    user, space = create_agent_fixture(db_session, name="纠1")
    account_id = user.account.id

    entry1 = terms.set_personal_term(
        db_session, account_id=account_id, space_id=space.id, concept_code="Uf", term="老妈"
    )
    events = [e for e in _term_events(db_session) if e.type == terms.EVENT_PERSONAL_UPDATED]
    assert len(events) == 1
    assert events[0].payload == {
        "account_id": account_id,
        "space_id": space.id,
        "concept_code": "Uf",
        "entry_id": entry1.id,
    }

    entry2 = terms.set_personal_term(
        db_session, account_id=account_id, space_id=space.id, concept_code="Uf", term="娘亲"
    )
    rows = list(
        db_session.scalars(
            select(TermEntry).where(
                TermEntry.level == "personal", TermEntry.owner_account_id == account_id
            )
        )
    )
    active = [r for r in rows if r.status == "active"]
    superseded = [r for r in rows if r.status == "superseded"]
    assert len(active) == 1 and active[0].id == entry2.id and active[0].term == "娘亲"
    assert [r.term for r in superseded] == ["老妈"]  # 旧值保留 revision 链
    assert active[0].revision > entry1.revision  # 链单调递增

    # 改回历史用词：复用既有行，不新增
    entry3 = terms.set_personal_term(
        db_session, account_id=account_id, space_id=space.id, concept_code="Uf", term="老妈"
    )
    rows_now = list(
        db_session.scalars(
            select(TermEntry).where(
                TermEntry.level == "personal", TermEntry.owner_account_id == account_id
            )
        )
    )
    assert len(rows_now) == 2 and entry3.id == entry1.id and entry3.status == "active"

    # 幂等：同文本重复调用不发事件
    before = len([e for e in _term_events(db_session) if e.type == terms.EVENT_PERSONAL_UPDATED])
    terms.set_personal_term(
        db_session, account_id=account_id, space_id=space.id, concept_code="Uf", term="老妈"
    )
    after = len([e for e in _term_events(db_session) if e.type == terms.EVENT_PERSONAL_UPDATED])
    assert before == after

    # AC-KI4/AC-KI3：纠正不产生 SourceFact 变更
    assert list(db_session.scalars(select(SourceFact)).all()) == []


def test_personal_correction_immediate_in_resolve_view(db_session: Session) -> None:
    """纠正立即影响当前视图（展示侧实时解析，无需失效缓存）。"""
    viewer, space = create_agent_fixture(db_session, name="纠2")
    create_space_member(db_session, space.id, viewer.id)  # owner 也需 active 成员资格
    father = _person(db_session, space.id, "纠父", "m")
    _confirm_parent(db_session, father.id, viewer.id, space_id=space.id)

    first = terms.compose_resolution_view(
        db_session,
        viewer_user_id=viewer.id,
        target_user_id=father.id,
        space_id=space.id,
        account_id=viewer.account.id,
    )
    assert first["term"] == "爸爸" and first["term_source_level"] == "locale"

    terms.set_personal_term(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um",
        term="老爸",
    )
    second = terms.compose_resolution_view(
        db_session,
        viewer_user_id=viewer.id,
        target_user_id=father.id,
        space_id=space.id,
        account_id=viewer.account.id,
    )
    assert second["term"] == "老爸" and second["term_source_level"] == "personal"


def test_raw_input_untouched_by_any_term_write(db_session: Session) -> None:
    """AC-KI3 回归：原文与任何 term 写入互不影响。"""
    user, space = create_agent_fixture(db_session, name="纠3")
    raw = sf.create_raw_relation_input(
        db_session, author_account_id=user.account.id, text="俺爸", context={"note": "自由输入"}
    )
    db_session.commit()
    original_text = raw.text

    terms.set_personal_term(
        db_session,
        account_id=user.account.id,
        space_id=space.id,
        concept_code="Um",
        term="老爸",
    )
    _space_entry(db_session, space_id=space.id, concept_code="Um", term="爹地")
    terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Um",
        term="爹地",
        account_id=user.account.id,
        profile_id=user.id,
        source_event="manual_select",
    )

    refetched = db_session.get(RawRelationInput, raw.id)
    assert refetched is not None
    assert refetched.text == original_text
    assert refetched.context_json == {"note": "自由输入"}
    assert list(db_session.scalars(select(RawRelationInput)).all()) == [refetched]


# ---- 使用证据与两人晋升 ----


def _two_members(session: Session, prefix: str):
    owner, space = create_agent_fixture(session, name=prefix)
    u1 = _person(session, space.id, f"{prefix}-甲", "m")
    u2 = _person(session, space.id, f"{prefix}-乙", "f")
    return owner, space, u1, u2


def test_promotion_two_confirmed_accounts(db_session: Session) -> None:
    _, space, u1, u2 = _two_members(db_session, "晋1")

    usage1, created1, summary1 = terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Um",
        term="爹地",
        account_id=u1.account.id,
        profile_id=u1.id,
        source_event="manual_select",
    )
    assert created1 and summary1["promoted"] is False

    _usage2, created2, summary2 = terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Um",
        term="爹地",
        account_id=u2.account.id,
        profile_id=u2.id,
        source_event="assistant_query",
    )
    assert created2 and summary2["promoted"] is True and summary2["eligible_accounts"] == 2

    holder = db_session.scalar(
        select(TermEntry).where(
            TermEntry.level == "space",
            TermEntry.space_id == space.id,
            TermEntry.concept_code == "Um",
            TermEntry.term == "爹地",
        )
    )
    assert holder is not None and holder.status == "active"
    # usage 全部挂在同一载体行上（幂等计数的基础）
    usages = list(db_session.scalars(select(TermUsage).where(TermUsage.term_entry_id == holder.id)))
    assert {u.account_id for u in usages} == {u1.account.id, u2.account.id}
    assert usage1.term_entry_id == holder.id

    promoted_events = [e for e in _term_events(db_session) if e.type == terms.EVENT_SPACE_PROMOTED]
    assert len(promoted_events) == 1
    assert promoted_events[0].payload["supporter_account_ids"] == sorted(
        [u1.account.id, u2.account.id]
    )
    assert promoted_events[0].payload["usage_ids"]

    # 无管理员发布动作：事件流中没有审批类事件，词条也不复制到 locale/system
    types = {e.type for e in _term_events(db_session)}
    assert types <= {
        terms.EVENT_PERSONAL_UPDATED,
        terms.EVENT_SPACE_PROMOTED,
        terms.EVENT_SPACE_DEMOTED,
    }
    copied = db_session.scalar(
        select(TermEntry).where(
            TermEntry.concept_code == "Um",
            TermEntry.term == "爹地",
            TermEntry.level.in_(("locale", "system")),
        )
    )
    assert copied is None

    # 第三位成员读到空间层称谓
    third = _person(db_session, space.id, "晋1-丙", "m")
    resolved = terms.resolve_term(
        db_session, account_id=third.account.id, space_id=space.id, concept_code="Um"
    )
    assert (resolved.term, resolved.source_level, resolved.entry_id) == (
        "爹地",
        "space",
        holder.id,
    )


def test_promotion_blocked_when_second_account_provisional(db_session: Session) -> None:
    _, space, u1, u2 = _two_members(db_session, "晋2")
    u2.profile_status = "provisional"  # 未确档不计入合格使用者
    db_session.commit()

    terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Um",
        term="爹地",
        account_id=u1.account.id,
        profile_id=u1.id,
        source_event="manual_select",
    )
    _, _, summary = terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Um",
        term="爹地",
        account_id=u2.account.id,
        profile_id=u2.id,
        source_event="manual_select",
    )
    assert summary["promoted"] is False and summary["eligible_accounts"] == 1
    holder = db_session.scalar(
        select(TermEntry).where(TermEntry.level == "space", TermEntry.space_id == space.id)
    )
    assert holder is not None and holder.status == "superseded"

    # 确档后随下次 usage 变更重算即晋升（同账号幂等去重不影响资格计数）
    u2.profile_status = "identity_confirmed"
    db_session.commit()
    _, _, summary = terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Um",
        term="爹地",
        account_id=u2.account.id,
        profile_id=u2.id,
        source_event="assistant_query",
    )
    assert summary["promoted"] is True


def test_demote_after_usage_revoked(db_session: Session) -> None:
    _, space, u1, u2 = _two_members(db_session, "晋3")
    terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Uf",
        term="妈咪",
        account_id=u1.account.id,
        profile_id=u1.id,
        source_event="manual_select",
    )
    terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Uf",
        term="妈咪",
        account_id=u2.account.id,
        profile_id=u2.id,
        source_event="manual_select",
    )
    holder = db_session.scalar(
        select(TermEntry).where(TermEntry.level == "space", TermEntry.space_id == space.id)
    )
    assert holder is not None and holder.status == "active"

    usage = db_session.scalar(
        select(TermUsage).where(
            TermUsage.term_entry_id == holder.id, TermUsage.account_id == u2.account.id
        )
    )
    assert usage is not None
    db_session.delete(usage)
    db_session.commit()

    summary = terms.recompute_space_promotion(
        db_session, space_id=space.id, concept_code="Uf", term="妈咪"
    )
    assert summary["demoted"] is True and summary["eligible_accounts"] == 1
    db_session.refresh(holder)
    assert holder.status == "superseded"
    demoted_events = [e for e in _term_events(db_session) if e.type == terms.EVENT_SPACE_DEMOTED]
    assert len(demoted_events) == 1


def test_demote_after_member_exits_space(db_session: Session) -> None:
    """退出空间的账号失格：重算不足则词条降级。"""
    from app.models.space import SpaceMember

    _, space, u1, u2 = _two_members(db_session, "晋4")
    terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Um",
        term="老爷子",
        account_id=u1.account.id,
        profile_id=u1.id,
        source_event="manual_select",
    )
    terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Um",
        term="老爷子",
        account_id=u2.account.id,
        profile_id=u2.id,
        source_event="manual_select",
    )
    membership = db_session.scalar(
        select(SpaceMember).where(SpaceMember.space_id == space.id, SpaceMember.user_id == u2.id)
    )
    assert membership is not None
    membership.status = "removed"
    db_session.commit()

    summary = terms.recompute_space_promotion(
        db_session, space_id=space.id, concept_code="Um", term="老爷子"
    )
    assert summary["demoted"] is True


def test_duplicate_usage_counts_once_per_account(db_session: Session) -> None:
    """UNIQUE(term_entry, account, space)：同账号重复选择不计第二位使用者。"""
    _, space, u1, u2 = _two_members(db_session, "晋5")
    terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Sm",
        term="我家先生",
        account_id=u1.account.id,
        profile_id=u1.id,
        source_event="assistant_query",
    )
    _usage, created, summary = terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Sm",
        term="我家先生",
        account_id=u1.account.id,
        profile_id=u1.id,
        source_event="manual_select",
    )
    assert created is False
    assert summary["eligible_accounts"] == 1 and summary["promoted"] is False

    # 第二人补位才达标
    _, _, summary2 = terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Sm",
        term="我家先生",
        account_id=u2.account.id,
        profile_id=u2.id,
        source_event="manual_select",
    )
    assert summary2["promoted"] is True


def test_admin_role_irrelevant_to_promotion(db_session: Session) -> None:
    """platform_operator 身份不产生任何审批路径：晋升纯由两人 usage 驱动。"""
    _, space, u1, _u2 = _two_members(db_session, "晋6")
    operator = _person(db_session, space.id, "晋6-管理", "m", is_admin=True)

    terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Uf",
        term="老太太",
        account_id=operator.account.id,
        profile_id=operator.id,
        source_event="manual_select",
    )
    _, _, summary = terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Uf",
        term="老太太",
        account_id=u1.account.id,
        profile_id=u1.id,
        source_event="manual_select",
    )
    assert summary["promoted"] is True
    # 晋升事件无管理员 actor（系统自动），事件流中不存在任何审批类类型
    promoted = [e for e in _term_events(db_session) if e.type == terms.EVENT_SPACE_PROMOTED]
    assert len(promoted) == 1 and promoted[0].actor_account_id is None
    assert all(
        "approve" not in e.type and "publish" not in e.type for e in _term_events(db_session)
    )


def test_usage_on_locale_word_uses_space_candidate_holder(db_session: Session) -> None:
    """选系统词（爷爷）也走 space 候选载体，不把 usage 挂到共享 locale 词条上。"""
    _, space, u1, u2 = _two_members(db_session, "晋7")
    locale_entry = db_session.scalar(
        select(TermEntry).where(
            TermEntry.level == "locale",
            TermEntry.locale == "zh-CN",
            TermEntry.concept_code == "Um-Um",
            TermEntry.term == "爷爷",
        )
    )
    assert locale_entry is not None

    usage1, _, _ = terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Um-Um",
        term="爷爷",
        account_id=u1.account.id,
        profile_id=u1.id,
        source_event="manual_select",
    )
    assert usage1.term_entry_id != locale_entry.id
    holder = db_session.get(TermEntry, usage1.term_entry_id)
    assert holder is not None and holder.level == "space" and holder.status == "superseded"

    _, _, summary = terms.record_usage_and_promote(
        db_session,
        space_id=space.id,
        concept_code="Um-Um",
        term="爷爷",
        account_id=u2.account.id,
        profile_id=u2.id,
        source_event="manual_select",
    )
    assert summary["promoted"] is True
    db_session.refresh(holder)
    assert holder.status == "active"  # 原地激活


# ---- 输入校验 ----


@pytest.mark.parametrize("bad_code", ["", "   ", "self", "UM", "Um-", "-Um", "Um..Um", "a" * 129])
def test_invalid_concept_codes_rejected(db_session: Session, bad_code: str) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        terms.validate_concept_code(bad_code)
    detail = excinfo.value.detail
    assert isinstance(detail, dict)
    assert detail.get("__api_error__", {}).get("code") == "CONCEPT_CODE_INVALID"


@pytest.mark.parametrize("bad_term", ["", "   ", "长" * 65])
def test_invalid_terms_rejected(db_session: Session, bad_term: str) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        terms.validate_term_text(bad_term)
    detail = excinfo.value.detail
    assert isinstance(detail, dict)
    assert detail.get("__api_error__", {}).get("code") == "TERM_INVALID"


# ---- API 合同 ----


def _login_header(client: TestClient, user) -> dict[str, str]:
    pair = login(client, user.name, "123456").json()
    return auth_header(pair)


def test_flag_off_all_endpoints_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RELATIONSHIP_INTELLIGENCE_ENABLED", False)
    body = {"space_id": 1, "concept_code": "Um", "term": "x"}
    assert client.get("/api/kinship/terms/my").status_code == 503
    assert client.put("/api/kinship/terms/my", json=body).status_code == 503
    assert (
        client.get(
            "/api/kinship/resolve", params={"space_id": 1, "from_user_id": 1, "to_user_id": 1}
        ).status_code
        == 503
    )
    assert client.post("/api/kinship/usages", json=body).status_code == 503
    payload = client.get("/api/kinship/terms/my").json()
    assert payload["error"]["code"] == "KINSHIP_FLAG_DISABLED"


def test_api_my_terms_put_get_and_validation(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "RELATIONSHIP_INTELLIGENCE_ENABLED", True)
    user, space = create_agent_fixture(db_session, name="API1")
    create_space_member(db_session, space.id, user.id)  # owner 也需 active 成员资格
    headers = _login_header(client, user)

    put = client.put(
        "/api/kinship/terms/my",
        json={"space_id": space.id, "concept_code": "Um", "term": "  老爸  "},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    data = put.json()
    assert data["term"] == "老爸"  # 首尾空白归一
    assert data["concept_code"] == "Um"

    listing = client.get("/api/kinship/terms/my", params={"space_id": space.id}, headers=headers)
    assert listing.status_code == 200
    items = listing.json()
    assert len(items) == 1
    assert items[0]["resolved"]["source_level"] == "personal"
    assert items[0]["resolved"]["term"] == "老爸"

    # 无 space_id：仅列词条，无 resolved 字段
    plain = client.get("/api/kinship/terms/my", headers=headers).json()
    assert plain[0]["resolved"] is None

    # 校验失败矩阵
    assert (
        client.put(
            "/api/kinship/terms/my",
            json={"space_id": space.id, "concept_code": "Um", "term": ""},
            headers=headers,
        ).json()["error"]["code"]
        == "TERM_INVALID"
    )
    assert (
        client.put(
            "/api/kinship/terms/my",
            json={"space_id": space.id, "concept_code": "Um", "term": "长" * 65},
            headers=headers,
        ).json()["error"]["code"]
        == "TERM_INVALID"
    )
    assert (
        client.put(
            "/api/kinship/terms/my",
            json={"space_id": space.id, "concept_code": "bad code", "term": "x"},
            headers=headers,
        ).json()["error"]["code"]
        == "CONCEPT_CODE_INVALID"
    )
    assert (
        client.put(
            "/api/kinship/terms/my",
            json={"space_id": 999999, "concept_code": "Um", "term": "x"},
            headers=headers,
        ).json()["error"]["code"]
        == "SPACE_NOT_FOUND"
    )


def test_api_resolve_happy_path_self_and_fact_state(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "RELATIONSHIP_INTELLIGENCE_ENABLED", True)
    viewer, space = create_agent_fixture(db_session, name="API2")
    create_space_member(db_session, space.id, viewer.id)  # owner 也需 active 成员资格
    father = _person(db_session, space.id, "API父", "m")
    _confirm_parent(db_session, father.id, viewer.id, space_id=space.id)
    headers = _login_header(client, viewer)

    out = client.get(
        "/api/kinship/resolve",
        params={"space_id": space.id, "from_user_id": viewer.id, "to_user_id": father.id},
        headers=headers,
    )
    assert out.status_code == 200, out.text
    data = out.json()
    # resolve 输出 schema 字段清单（E4 前端合同）
    assert set(data) == {
        "found",
        "viewer_user_id",
        "target_user_id",
        "space_id",
        "path_class",
        "concept_code",
        "explanation_structural",
        "term",
        "term_source_level",
        "term_entry_id",
        "main_path",
        "alt_paths",
        "fact_state",
        "cache_hit",
        "algorithm_version",
    }
    assert data["found"] is True
    assert data["concept_code"] == "Um"
    assert data["path_class"] == "direct_line"
    assert data["term"] == "爸爸" and data["term_source_level"] == "locale"
    assert data["explanation_structural"] == "你的父亲"
    assert len(data["main_path"]) == 1
    step = data["main_path"][0]
    assert (step["from"], step["to"], step["edge_type"], step["direction"]) == (
        viewer.id,
        father.id,
        "parent",
        "up",
    )
    assert data["alt_paths"] == []
    assert data["fact_state"]["confirmed"] >= 1
    assert data["fact_state"]["evidence_fact_ids"]  # 主路径引用的事实 id 非空

    # SELF：系统标准称谓兜底
    self_out = client.get(
        "/api/kinship/resolve",
        params={"space_id": space.id, "from_user_id": viewer.id, "to_user_id": viewer.id},
        headers=headers,
    ).json()
    assert self_out["concept_code"] == "SELF"
    assert (self_out["term"], self_out["term_source_level"]) == ("自己", "system")

    # 个人纠正立即反映在 resolve 输出（AC-KI6）
    put = client.put(
        "/api/kinship/terms/my",
        json={"space_id": space.id, "concept_code": "Um", "term": "俺爹"},
        headers=headers,
    )
    assert put.status_code == 200
    after = client.get(
        "/api/kinship/resolve",
        params={"space_id": space.id, "from_user_id": viewer.id, "to_user_id": father.id},
        headers=headers,
    ).json()
    assert after["term"] == "俺爹" and after["term_source_level"] == "personal"


def test_api_resolve_guards(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "RELATIONSHIP_INTELLIGENCE_ENABLED", True)
    viewer, space = create_agent_fixture(db_session, name="API3")
    create_space_member(db_session, space.id, viewer.id)  # owner 也需 active 成员资格
    outsider = create_user_with_pin(db_session, "API3-外", "123456")
    stranger = _person(db_session, None, "API3-路人", "f")
    headers = _login_header(client, viewer)

    # 以他人视角解析被拒（防探测）
    foreign = client.get(
        "/api/kinship/resolve",
        params={"space_id": space.id, "from_user_id": outsider.id, "to_user_id": viewer.id},
        headers=headers,
    )
    assert foreign.status_code == 403
    assert foreign.json()["error"]["code"] == "SPACE_FORBIDDEN_ACTOR"

    # 非 active 成员访问该空间被拒
    outsider_headers = _login_header(client, outsider)
    denied = client.get(
        "/api/kinship/resolve",
        params={"space_id": space.id, "from_user_id": outsider.id, "to_user_id": viewer.id},
        headers=outsider_headers,
    )
    assert denied.status_code == 403

    # 未知空间 404
    missing = client.get(
        "/api/kinship/resolve",
        params={"space_id": 999999, "from_user_id": viewer.id, "to_user_id": viewer.id},
        headers=headers,
    )
    assert missing.status_code == 404

    # 无路径：found=false 统一形状（不泄露存在性）
    no_path = client.get(
        "/api/kinship/resolve",
        params={"space_id": space.id, "from_user_id": viewer.id, "to_user_id": stranger.id},
        headers=headers,
    ).json()
    assert no_path["found"] is False
    assert no_path["term"] is None and no_path["main_path"] == []


def test_api_resolve_no_path_zeroes_fact_state_for_invisible_target(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """found=false 时 fact_state 必须归零：不可见人物的存在性与事实状态

    不得经 resolve 探测得知（回归：此前 found=false 仍返回真实计数，
    与「不泄露存在性」合同矛盾）。
    """
    monkeypatch.setattr(config, "RELATIONSHIP_INTELLIGENCE_ENABLED", True)
    viewer, space = create_agent_fixture(db_session, name="API4")
    create_space_member(db_session, space.id, viewer.id)
    # 非成员（不在图内 → found=false），但与 viewer 存在已确认事实
    outsider_person = _person(db_session, None, "API4-隐匿人", "f")
    _confirm_parent(db_session, outsider_person.id, viewer.id, space_id=space.id)
    headers = _login_header(client, viewer)

    data = client.get(
        "/api/kinship/resolve",
        params={"space_id": space.id, "from_user_id": viewer.id, "to_user_id": outsider_person.id},
        headers=headers,
    ).json()
    assert data["found"] is False
    assert data["fact_state"] == {
        "confirmed": 0,
        "proposed": 0,
        "disputed": 0,
        "revoked": 0,
        "evidence_fact_ids": [],
    }


def test_api_usages_flow_and_promotion(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "RELATIONSHIP_INTELLIGENCE_ENABLED", True)
    viewer, space = create_agent_fixture(db_session, name="API4")
    create_space_member(db_session, space.id, viewer.id)  # owner 也需 active 成员资格
    u1 = _person(db_session, space.id, "API4-甲", "m")
    u2 = _person(db_session, space.id, "API4-乙", "f")
    h1 = _login_header(client, u1)
    h2 = _login_header(client, u2)

    body = {
        "space_id": space.id,
        "concept_code": "Uf",
        "term": "娘亲",
        "source_event": "manual_select",
    }
    first = client.post("/api/kinship/usages", json=body, headers=h1)
    assert first.status_code == 201, first.text
    assert first.json()["created"] is True
    assert first.json()["promotion"]["promoted"] is False

    dup = client.post(
        "/api/kinship/usages", json={**body, "source_event": "assistant_query"}, headers=h1
    )
    assert dup.status_code == 201
    assert dup.json()["created"] is False  # 同账号幂等去重

    second = client.post("/api/kinship/usages", json=body, headers=h2)
    assert second.json()["promotion"]["promoted"] is True
    assert second.json()["promotion"]["eligible_accounts"] == 2

    # 第三位成员经 resolve 读到本人视图（端点对普通成员可用）
    third = _person(db_session, space.id, "API4-丙", "m")
    resolved = client.get(
        "/api/kinship/resolve",
        params={"space_id": space.id, "from_user_id": third.id, "to_user_id": third.id},
        headers=_login_header(client, third),
    )
    assert resolved.status_code == 200

    # 非成员提交 usage 被拒
    outsider = create_user_with_pin(db_session, "API4-外", "123456")
    rejected = client.post(
        "/api/kinship/usages", json=body, headers=_login_header(client, outsider)
    )
    assert rejected.status_code == 403


# ---- 输入校验 ----
