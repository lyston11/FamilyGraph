"""确定性亲属路径解析器与 DerivedFact 缓存测试（V2.3 Block E2）。

覆盖 implement.md 黄金用例：爷爷系/外公系区分、奶奶的兄弟（UUB 类旁系）、
父母未知 direct_sibling、收养 vs 亲生不同 concept、继亲、配偶父母（affinal）、
partner 不进姻亲链、再婚两条配偶边主路径稳定、多路径主/替排序稳定性。
不变量：确定性（同快照两次 resolve 相等）、环安全、跨空间隔离、revoked 后
答案改变且缓存重算（AC-KI8）、flag off 回归逐字节不变、缓存命中/失效计数、
深度上限拒绝。
"""

from __future__ import annotations

from typing import Any

import fastapi
import pytest
from conftest import (
    create_agent_fixture,
    create_agent_session,
    create_space_member,
    create_user_with_pin,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models.derived_fact import DerivedFact
from app.models.relationship_facts import SourceFact
from app.models.v2_foundation import DomainEvent
from app.services import agent_events, agent_queue, agent_tools, derived_facts
from app.services import source_facts as sf
from app.services.agent_query import TOOL_EXPLAIN_STRUCTURAL_PATH, TOOL_GET_RELATIONSHIP_PATH
from app.services.derived_facts import KINSHIP_ALGO_VERSION
from app.services.relationship_resolver import resolve_relationship
from app.utils.timeutil import utcnow

# ---- 造数辅助 ----


def _person(session: Session, space_id: int | None, name: str, gender: str):
    """建人并加入空间（active 成员，保证 viewer 可见）。"""
    user = create_user_with_pin(session, name, "123456", gender=gender)
    if space_id is not None:
        create_space_member(session, space_id, user.id)
    return user


def _confirm(
    session: Session,
    fact_type: str,
    subject_id: int,
    object_id: int,
    *,
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
    return fact


def _latest_event(session: Session, fact_id: int) -> DomainEvent:
    session.flush()
    row = session.scalar(
        select(DomainEvent)
        .where(DomainEvent.aggregate_type == "source_fact", DomainEvent.aggregate_id == fact_id)
        .order_by(DomainEvent.id.desc())
    )
    assert row is not None
    return row


def _cached_row_count(session: Session) -> int:
    session.flush()
    return len(list(session.scalars(select(DerivedFact)).all()))


# ---- 黄金用例：concept_code 编码 ----


def test_golden_paternal_maternal_lines_and_grandmother(db_session) -> None:
    """爷爷系 Um-Um / 外公系 Uf-Um / 奶奶 Um-Uf：父系母系方向可区分。"""
    _, space = create_agent_fixture(db_session, name="金1")
    viewer = _person(db_session, space.id, "小张", "m")
    father = _person(db_session, space.id, "张父", "m")
    mother = _person(db_session, space.id, "张母", "f")
    pgf = _person(db_session, space.id, "张爷爷", "m")
    pgm = _person(db_session, space.id, "张奶奶", "f")
    mgf = _person(db_session, space.id, "外公公", "m")
    _confirm(db_session, "biological_parent", father.id, viewer.id, space_id=space.id)
    _confirm(db_session, "biological_parent", mother.id, viewer.id, space_id=space.id)
    _confirm(db_session, "biological_parent", pgf.id, father.id, space_id=space.id)
    _confirm(db_session, "biological_parent", pgm.id, father.id, space_id=space.id)
    _confirm(db_session, "biological_parent", mgf.id, mother.id, space_id=space.id)

    resolve = resolve_relationship
    r_pgf = resolve(db_session, viewer_user_id=viewer.id, target_user_id=pgf.id, space_id=space.id)
    assert r_pgf.found and r_pgf.concept_code == "Um-Um"
    assert r_pgf.path_class == "direct_line"

    r_pgm = resolve(db_session, viewer_user_id=viewer.id, target_user_id=pgm.id, space_id=space.id)
    assert r_pgm.concept_code == "Um-Uf"

    r_mgf = resolve(db_session, viewer_user_id=viewer.id, target_user_id=mgf.id, space_id=space.id)
    assert r_mgf.concept_code == "Uf-Um"
    assert r_mgf.explanation_structural == "你的母亲的父亲"


def test_golden_grandmother_brother_collateral(db_session) -> None:
    """奶奶的兄弟 = Um-Uf-Bm：祖辈旁系概念 + 可解释依据。"""
    _, space = create_agent_fixture(db_session, name="金2")
    viewer = _person(db_session, space.id, "小王", "f")
    father = _person(db_session, space.id, "王父", "m")
    grandma = _person(db_session, space.id, "王奶奶", "f")
    granduncle = _person(db_session, space.id, "舅爷爷", "m")
    _confirm(db_session, "biological_parent", father.id, viewer.id, space_id=space.id)
    _confirm(db_session, "biological_parent", grandma.id, father.id, space_id=space.id)
    _confirm(db_session, "direct_sibling", granduncle.id, grandma.id, space_id=space.id)

    r = resolve_relationship(
        db_session, viewer_user_id=viewer.id, target_user_id=granduncle.id, space_id=space.id
    )
    assert r.found and r.concept_code == "Um-Uf-Bm"
    assert r.path_class == "collateral"
    assert r.explanation_structural == "你的父亲的母亲的兄弟"


def test_golden_direct_sibling_without_parents(db_session) -> None:
    """父母未知的 direct_sibling 独立成立，不反推任何父母。"""
    _, space = create_agent_fixture(db_session, name="金3")
    brother = _person(db_session, space.id, "阿哥", "m")
    sister = _person(db_session, space.id, "阿妹", "f")
    _confirm(db_session, "direct_sibling", brother.id, sister.id, space_id=space.id)

    r = resolve_relationship(
        db_session, viewer_user_id=sister.id, target_user_id=brother.id, space_id=space.id
    )
    assert r.found and r.concept_code == "Bm"
    assert r.path_class == "collateral"
    parents = list(
        db_session.scalars(
            select(SourceFact).where(SourceFact.fact_type.in_(sf.PARENT_FACT_TYPES))
        ).all()
    )
    assert parents == []


def test_golden_adoptive_step_guardian_distinct_concepts(db_session) -> None:
    """收养/继亲/监护与亲生 concept 不同，path_class 各归其位。"""
    _, space = create_agent_fixture(db_session, name="金4")

    def world(prefix: str, fact_type: str, parent_gender: str) -> tuple[int, int, str]:
        child = _person(db_session, space.id, f"{prefix}-子", "m")
        parent = _person(db_session, space.id, f"{prefix}-亲", parent_gender)
        _confirm(db_session, fact_type, parent.id, child.id, space_id=space.id)
        suffix = "m" if parent_gender != "unknown" else ""
        return child.id, parent.id, suffix

    bio_child, bio_parent, _ = world("生", "biological_parent", "m")
    r_bio = resolve_relationship(
        db_session, viewer_user_id=bio_child, target_user_id=bio_parent, space_id=space.id
    )
    assert (r_bio.concept_code, r_bio.path_class) == ("Um", "direct_line")

    ado_child, ado_parent, suffix = world("养", "adoptive_parent", "m")
    r_ado = resolve_relationship(
        db_session, viewer_user_id=ado_child, target_user_id=ado_parent, space_id=space.id
    )
    assert (r_ado.concept_code, r_ado.path_class) == (f"Ua{suffix}", "step_adoptive")

    step_child, step_parent, _suffix = world("继", "step_parent", "f")
    r_step = resolve_relationship(
        db_session, viewer_user_id=step_child, target_user_id=step_parent, space_id=space.id
    )
    assert (r_step.concept_code, r_step.path_class) == ("Usf", "step_adoptive")
    assert r_step.explanation_structural == "你的继母"

    guard_child, guard_parent, suffix = world("监", "guardian", "m")
    r_guard = resolve_relationship(
        db_session, viewer_user_id=guard_child, target_user_id=guard_parent, space_id=space.id
    )
    assert (r_guard.concept_code, r_guard.path_class) == (f"Ug{suffix}", "guardian")


def test_golden_gender_unknown_omits_suffix(db_session) -> None:
    """gender unknown 时省略性别字母（编码合同）。"""
    _, space = create_agent_fixture(db_session, name="金5")
    child = _person(db_session, space.id, "无性子", "m")
    parent = _person(db_session, space.id, "无性亲", "unknown")
    _confirm(db_session, "biological_parent", parent.id, child.id, space_id=space.id)
    r = resolve_relationship(
        db_session, viewer_user_id=child.id, target_user_id=parent.id, space_id=space.id
    )
    assert r.concept_code == "U"


def test_golden_spouse_parent_affinal_and_self(db_session) -> None:
    """配偶的父母走姻亲链（仅经 spouse）；SELF 特例。"""
    _, space = create_agent_fixture(db_session, name="金6")
    viewer = _person(db_session, space.id, "阿妻", "f")
    husband = _person(db_session, space.id, "阿夫", "m")
    father_in_law = _person(db_session, space.id, "公公", "m")
    _confirm(db_session, "spouse", husband.id, viewer.id, space_id=space.id)
    _confirm(db_session, "biological_parent", father_in_law.id, husband.id, space_id=space.id)

    r_spouse = resolve_relationship(
        db_session, viewer_user_id=viewer.id, target_user_id=husband.id, space_id=space.id
    )
    assert (r_spouse.concept_code, r_spouse.path_class) == ("Sm", "affinal")

    r_fil = resolve_relationship(
        db_session, viewer_user_id=viewer.id, target_user_id=father_in_law.id, space_id=space.id
    )
    assert r_fil.concept_code == "Sm-Um"
    assert r_fil.path_class == "affinal"

    r_self = resolve_relationship(
        db_session, viewer_user_id=viewer.id, target_user_id=viewer.id, space_id=space.id
    )
    assert (r_self.found, r_self.concept_code, r_self.path_class) == (True, "SELF", "self")


def test_partner_single_hop_only_not_extended_through(db_session) -> None:
    """partner 可直达但不进姻亲延伸链（未披露语义，AC-KI1）。"""
    _, space = create_agent_fixture(db_session, name="金7")
    viewer = _person(db_session, space.id, "本人", "f")
    partner = _person(db_session, space.id, "伴侣", "m")
    partner_father = _person(db_session, space.id, "伴侣之父", "m")
    _confirm(db_session, "partner", partner.id, viewer.id, space_id=space.id)
    _confirm(db_session, "biological_parent", partner_father.id, partner.id, space_id=space.id)

    r_direct = resolve_relationship(
        db_session, viewer_user_id=viewer.id, target_user_id=partner.id, space_id=space.id
    )
    assert (r_direct.found, r_direct.concept_code, r_direct.path_class) == (
        True,
        "Pm",
        "affinal",
    )

    r_through = resolve_relationship(
        db_session, viewer_user_id=viewer.id, target_user_id=partner_father.id, space_id=space.id
    )
    assert r_through.found is False  # 不经 partner 推断伴侣之父


def test_remarriage_two_spouse_edges_main_path_stable(db_session) -> None:
    """再婚两条配偶边并存：最短确认边胜出且乱序插入结果稳定。"""

    def build(reverse: bool) -> tuple[int, int, str]:
        _, space = create_agent_fixture(db_session, name=f"再婚{reverse}")
        child = _person(db_session, space.id, "再婚子女", "f")
        father = _person(db_session, space.id, "再婚之父", "m")
        birth_mother = _person(db_session, space.id, "生母", "f")
        step_mother = _person(db_session, space.id, "继母?", "f")
        facts = [
            ("biological_parent", father.id, child.id),
            ("biological_parent", birth_mother.id, child.id),
            ("spouse", father.id, birth_mother.id),
            ("spouse", father.id, step_mother.id),
        ]
        if reverse:
            facts.reverse()
        for fact_type, subject_id, object_id in facts:
            _confirm(db_session, fact_type, subject_id, object_id, space_id=space.id)
        return child.id, step_mother.id, space.id

    child_id, step_mother_id, space_id = build(reverse=False)
    r = resolve_relationship(
        db_session, viewer_user_id=child_id, target_user_id=step_mother_id, space_id=space_id
    )
    assert r.concept_code == "Um-Sf"  # 经父亲一跳再经配偶边
    assert r.path_class == "affinal"
    r_again = resolve_relationship(
        db_session, viewer_user_id=child_id, target_user_id=step_mother_id, space_id=space_id
    )
    assert r_again == r  # 同快照两次解析逐字段相等（AC-KI7）

    child2_id, step_mother2_id, space2_id = build(reverse=True)
    r2 = resolve_relationship(
        db_session, viewer_user_id=child2_id, target_user_id=step_mother2_id, space_id=space2_id
    )
    assert r2.concept_code == r.concept_code  # 插入顺序不影响结论


def test_multi_path_diamond_main_alt_ordering_stable(db_session) -> None:
    """菱形双等长血缘路径：按节点 id 序列字典序选主，替代路径保留且有序。"""
    _, space = create_agent_fixture(db_session, name="菱形")
    viewer = _person(db_session, space.id, "菱后", "f")
    father = _person(db_session, space.id, "菱父", "m")
    mother = _person(db_session, space.id, "菱母", "f")
    grandfather = _person(db_session, space.id, "菱祖", "m")
    _confirm(db_session, "biological_parent", father.id, viewer.id, space_id=space.id)
    _confirm(db_session, "biological_parent", mother.id, viewer.id, space_id=space.id)
    _confirm(db_session, "biological_parent", grandfather.id, father.id, space_id=space.id)
    _confirm(db_session, "biological_parent", grandfather.id, mother.id, space_id=space.id)

    r = resolve_relationship(
        db_session, viewer_user_id=viewer.id, target_user_id=grandfather.id, space_id=space.id
    )
    assert r.found and len(r.main_path) == 2
    mid_via_main = r.main_path[0].to_id
    expected_mid = min(father.id, mother.id)  # 同分字典序：中间节点 id 小者为主
    assert mid_via_main == expected_mid
    alt_mid = r.alt_paths[0][0].to_id
    assert alt_mid == max(father.id, mother.id)
    assert len(r.alt_descriptions) == 1
    if mother.id < father.id:
        assert r.concept_code == "Uf-Um"
        assert r.alt_descriptions[0] == "你的父亲的父亲"
    else:
        assert r.concept_code == "Um-Um"
        assert r.alt_descriptions[0] == "你的母亲的父亲"

    r_again = resolve_relationship(
        db_session, viewer_user_id=viewer.id, target_user_id=grandfather.id, space_id=space.id
    )
    assert r_again == r


# ---- 不变量 ----


def test_cycle_safety_terminates_with_simple_path(db_session) -> None:
    """ORM 直插成环事实（绕过服务层环检测）：解析终止且只返回简单路径。"""
    _, space = create_agent_fixture(db_session, name="环")
    a = _person(db_session, space.id, "环甲", "m")
    b = _person(db_session, space.id, "环乙", "f")
    c = _person(db_session, space.id, "环丙", "m")
    now = utcnow()
    for subject_id, object_id in ((a.id, b.id), (b.id, c.id), (c.id, a.id)):
        db_session.add(
            SourceFact(
                fact_type="biological_parent",
                subject_user_id=subject_id,
                object_user_id=object_id,
                space_id=space.id,
                provenance="import",
                state="confirmed",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
    db_session.commit()

    r = resolve_relationship(
        db_session, viewer_user_id=a.id, target_user_id=c.id, space_id=space.id
    )
    assert r.found
    visited = [r.main_path[0].from_id, *(step.to_id for step in r.main_path)]
    assert len(visited) == len(set(visited))  # 简单路径：节点不重复


def test_cross_space_isolation_and_global_facts(db_session) -> None:
    """空间事实不跨空间消费；全局事实处处可消费；他空间不可达不泄露存在。"""
    _, space = create_agent_fixture(db_session, name="隔1")
    _, space2 = create_agent_fixture(db_session, name="隔2")
    viewer = _person(db_session, space.id, "隔离者", "f")
    create_space_member(db_session, space2.id, viewer.id)
    target = _person(db_session, space.id, "被隔者", "m")
    create_space_member(db_session, space2.id, target.id)
    parent = _person(db_session, space.id, "隔离之亲", "m")
    create_space_member(db_session, space2.id, parent.id)

    _confirm(db_session, "biological_parent", parent.id, viewer.id, space_id=space.id)
    _confirm(db_session, "spouse", target.id, viewer.id, space_id=space2.id)

    r_s1 = resolve_relationship(
        db_session, viewer_user_id=viewer.id, target_user_id=target.id, space_id=space.id
    )
    assert r_s1.found is False  # 配偶事实在空间 2，空间 1 不可见

    r_s2 = resolve_relationship(
        db_session, viewer_user_id=viewer.id, target_user_id=target.id, space_id=space2.id
    )
    assert r_s2.found and r_s2.concept_code == "Sm"

    # 全局事实：任意空间均可消费
    _confirm(db_session, "biological_parent", parent.id, target.id, space_id=None)
    r_global = resolve_relationship(
        db_session, viewer_user_id=viewer.id, target_user_id=parent.id, space_id=space2.id
    )
    assert r_global.found and r_global.concept_code == "Sm-Um"


def test_depth_limit_twelve_steps(db_session) -> None:
    """>12 步不再枚举（found=false）；恰好 12 步可达。"""
    _, space = create_agent_fixture(db_session, name="深度")
    users = [
        _person(db_session, space.id, f"深{i:02d}", "m" if i % 2 == 0 else "f") for i in range(14)
    ]
    for child, parent in zip(users, users[1:], strict=False):
        _confirm(db_session, "biological_parent", parent.id, child.id, space_id=space.id)

    ok = resolve_relationship(
        db_session, viewer_user_id=users[0].id, target_user_id=users[12].id, space_id=space.id
    )
    assert ok.found and len(ok.main_path) == 12

    too_deep = resolve_relationship(
        db_session, viewer_user_id=users[0].id, target_user_id=users[13].id, space_id=space.id
    )
    assert too_deep.found is False
    assert too_deep.concept_code is None and too_deep.main_path == ()


# ---- DerivedFact 缓存与失效 ----


def test_cache_hit_miss_and_revocation_invalidation(db_session) -> None:
    """命中/未命中计数；revoked 后事件失效删行，答案即时改变（AC-KI8）。"""
    _, space = create_agent_fixture(db_session, name="缓1")
    viewer = _person(db_session, space.id, "缓妻", "f")
    husband = _person(db_session, space.id, "缓夫", "m")
    fact = _confirm(db_session, "spouse", husband.id, viewer.id, space_id=space.id)

    first = derived_facts.get_or_compute(
        db_session, viewer_user_id=viewer.id, target_user_id=husband.id, space_id=space.id
    )
    assert first.cache_hit is False and first.found and first.concept_code == "Sm"

    second = derived_facts.get_or_compute(
        db_session, viewer_user_id=viewer.id, target_user_id=husband.id, space_id=space.id
    )
    assert second.cache_hit is True
    assert second.evidence_hash == first.evidence_hash
    assert _cached_row_count(db_session) == 1

    sf.transition_source_fact(db_session, fact, "revoke")
    removed = derived_facts.invalidate_for_event(db_session, _latest_event(db_session, fact.id))
    db_session.commit()
    assert removed == 1
    assert _cached_row_count(db_session) == 0

    after = derived_facts.get_or_compute(
        db_session, viewer_user_id=viewer.id, target_user_id=husband.id, space_id=space.id
    )
    assert after.cache_hit is False and after.found is False  # 撤销后不再给出旧结论


def test_cache_detects_stale_snapshot_without_explicit_invalidation(db_session) -> None:
    """未消费失效事件时读取：snapshot_hash 不匹配 → 自动重算，绝不过期返回。"""
    _, space = create_agent_fixture(db_session, name="缓2")
    viewer = _person(db_session, space.id, "缓子", "f")
    father = _person(db_session, space.id, "缓父", "m")
    grandfather = _person(db_session, space.id, "缓祖", "m")
    _confirm(db_session, "biological_parent", father.id, viewer.id, space_id=space.id)

    cached = derived_facts.get_or_compute(
        db_session, viewer_user_id=viewer.id, target_user_id=father.id, space_id=space.id
    )
    assert cached.cache_hit is False

    _confirm(db_session, "biological_parent", grandfather.id, father.id, space_id=space.id)
    refreshed = derived_facts.get_or_compute(
        db_session, viewer_user_id=viewer.id, target_user_id=father.id, space_id=space.id
    )
    assert refreshed.cache_hit is False  # 快照变化 → 重算
    assert refreshed.evidence_hash != cached.evidence_hash
    assert refreshed.resolution.explanation_structural == "你的父亲"


def test_invalidate_for_event_space_narrowing_and_unrelated_kept(db_session) -> None:
    """空间事件只清该空间行；无关对的行保留；全局事实影响所有空间。"""
    _, space1 = create_agent_fixture(db_session, name="失1")
    _, space2 = create_agent_fixture(db_session, name="失2")
    a = _person(db_session, space1.id, "失甲", "m")
    create_space_member(db_session, space2.id, a.id)
    b = _person(db_session, space1.id, "失乙", "f")
    create_space_member(db_session, space2.id, b.id)
    c = _person(db_session, space1.id, "失丙", "m")
    d = _person(db_session, space1.id, "失丁", "f")

    scoped_fact = _confirm(
        db_session, "biological_parent", a.id, b.id, space_id=space1.id
    )  # 仅空间 1
    _confirm(db_session, "spouse", c.id, d.id, space_id=space1.id)  # 无关对

    def cache_pair(left: int, right: int, space_id: int) -> None:
        result = derived_facts.get_or_compute(
            db_session, viewer_user_id=left, target_user_id=right, space_id=space_id
        )
        assert result.found, (left, right, space_id)

    cache_pair(a.id, b.id, space1.id)
    cache_pair(b.id, a.id, space1.id)
    cache_pair(c.id, d.id, space1.id)
    assert _cached_row_count(db_session) == 3

    sf.transition_source_fact(db_session, scoped_fact, "revoke")
    removed = derived_facts.invalidate_for_event(
        db_session, _latest_event(db_session, scoped_fact.id)
    )
    assert removed == 2  # 甲/乙任一端的行
    remaining = {
        (row.viewer_user_id, row.target_user_id)
        for row in db_session.scalars(select(DerivedFact)).all()
    }
    assert remaining == {(c.id, d.id)}  # 无关对保留


def test_global_fact_event_invalidates_all_spaces(db_session) -> None:
    """全局事实（space NULL）的失效事件删除所有空间的对应行。"""
    _, space1 = create_agent_fixture(db_session, name="失3")
    _, space2 = create_agent_fixture(db_session, name="失4")
    a = _person(db_session, space1.id, "全甲", "m")
    create_space_member(db_session, space2.id, a.id)
    b = _person(db_session, space1.id, "全乙", "f")
    create_space_member(db_session, space2.id, b.id)
    global_fact = _confirm(db_session, "biological_parent", a.id, b.id, space_id=None)

    for space_id in (space1.id, space2.id):
        result = derived_facts.get_or_compute(
            db_session, viewer_user_id=b.id, target_user_id=a.id, space_id=space_id
        )
        assert result.found
    assert _cached_row_count(db_session) == 2

    sf.transition_source_fact(db_session, global_fact, "revoke")
    removed = derived_facts.invalidate_for_event(
        db_session, _latest_event(db_session, global_fact.id)
    )
    assert removed == 2  # 两个空间各一行
    assert _cached_row_count(db_session) == 0


def test_invalidate_ignores_non_source_fact_events(db_session) -> None:
    a, space = create_agent_fixture(db_session, name="失5")
    event = DomainEvent(
        type="profile.updated",
        aggregate_type="profile",
        aggregate_id=a.id,
        payload={"subject_user_id": a.id, "object_user_id": a.id},
        space_id=space.id,
        created_at=utcnow(),
    )
    assert derived_facts.invalidate_for_event(db_session, event) == 0


def test_rebuild_space_recomputes_and_drops_dead_rows(db_session) -> None:
    """rebuild_space 全量重算：活行更新、死行清除（运维入口）。"""
    _, space = create_agent_fixture(db_session, name="重建")
    viewer = _person(db_session, space.id, "重子", "f")
    father = _person(db_session, space.id, "重父", "m")
    fact = _confirm(db_session, "biological_parent", father.id, viewer.id, space_id=space.id)
    first = derived_facts.get_or_compute(
        db_session, viewer_user_id=viewer.id, target_user_id=father.id, space_id=space.id
    )
    assert first.found

    sf.transition_source_fact(db_session, fact, "revoke")  # 不消费事件，制造过期行
    stats = derived_facts.rebuild_space(db_session, space.id)
    assert stats == {"kept": 0, "dropped": 1}
    assert _cached_row_count(db_session) == 0

    _confirm(db_session, "biological_parent", father.id, viewer.id, space_id=space.id)  # 重建事实
    stats2 = derived_facts.rebuild_space(db_session, space.id)
    assert stats2 == {"kept": 0, "dropped": 0}  # 无既有行则无事可算


def test_term_version_left_null_for_e3(db_session) -> None:
    """E2 不写 term_version 列（E3 TermRegistry 接入后填充）。"""
    _, space = create_agent_fixture(db_session, name="词版")
    viewer = _person(db_session, space.id, "词子", "f")
    father = _person(db_session, space.id, "词父", "m")
    _confirm(db_session, "biological_parent", father.id, viewer.id, space_id=space.id)
    derived_facts.get_or_compute(
        db_session, viewer_user_id=viewer.id, target_user_id=father.id, space_id=space.id
    )
    row = db_session.scalar(select(DerivedFact))
    assert row is not None and row.term_version is None
    assert row.algorithm_version == KINSHIP_ALGO_VERSION == "v1"


# ---- 工具升级（flag 门禁） ----


def _tool_world(db_session: Session) -> dict[str, Any]:
    """B 的 assistant 会话 + SourceFact 世界：B—A 配偶、A—M 父女。"""
    _, space = create_agent_fixture(db_session, name="工具")
    a = _person(db_session, space.id, "工甲", "m")
    b = _person(db_session, space.id, "工乙", "f")
    m = _person(db_session, space.id, "工丙", "f")
    _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    _confirm(db_session, "biological_parent", a.id, m.id, space_id=space.id)
    session_row = create_agent_session(db_session, account_id=b.account.id, space_id=space.id)
    return {"space": space, "a": a, "b": b, "m": m, "session": session_row}


def _assistant_run(db_session: Session, session_row: Any):
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[TOOL_GET_RELATIONSHIP_PATH, TOOL_EXPLAIN_STRUCTURAL_PATH],
    )
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="test-sidecar")
    assert grant is not None and grant.run.id == run.id
    seq = agent_events.next_seq(db_session, run.id)
    agent_events.append_events(
        db_session,
        grant.run,
        [agent_events.EventEntry(seq=seq, type="run.started", public_payload={})],
    )
    return grant.run


@pytest.fixture()
def _flag_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "RELATIONSHIP_INTELLIGENCE_ENABLED", False)


@pytest.fixture()
def _flag_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "RELATIONSHIP_INTELLIGENCE_ENABLED", True)


def test_flag_off_tool_output_byte_identical_legacy(db_session, _flag_off) -> None:
    """Flag 关闭：get_relationship_path 保持 V2.2 行为逐字节不变（回归）。"""
    from conftest import create_v1_relation

    _, space = create_agent_fixture(db_session, name="回旧")
    b = create_user_with_pin(db_session, "旧乙", "123456", gender="f")
    a = create_user_with_pin(db_session, "旧甲", "123456", gender="m")
    m = create_user_with_pin(db_session, "旧丙", "123456", gender="f")
    for user in (b, a, m):
        create_space_member(db_session, space.id, user.id)
    spouse_edge = create_v1_relation(
        db_session, from_user_id=a.id, to_user_id=b.id, dir_class="spouse"
    )
    child_edge = create_v1_relation(
        db_session, from_user_id=a.id, to_user_id=m.id, dir_class="younger"
    )
    session_row = create_agent_session(db_session, account_id=b.account.id, space_id=space.id)
    run = _assistant_run(db_session, session_row)

    output = agent_tools.execute(
        db_session,
        run,
        session_row,
        {"agent_kind": "assistant"},
        name=TOOL_GET_RELATIONSHIP_PATH,
        version=1,
        input_payload={"to_user_id": m.id},
    )
    assert output == {
        "found": True,
        "path_class": "multi_hop",
        "path": [
            {"user_id": a.id, "name": "旧甲", "dir_class": "spouse"},
            {"user_id": m.id, "name": "旧丙", "dir_class": "younger"},
        ],
        "evidence_relation_ids": [spouse_edge.id, child_edge.id],
    }
    assert "concept_code" not in output and "algorithm_version" not in output
    assert _cached_row_count(db_session) == 0  # 旧实现不写 DerivedFact


def test_flag_on_tools_expose_additive_fields_both_versions(db_session, _flag_on) -> None:
    """Flag 开启：@1/@2 都可用，输出携带 concept_code/path_class 新词表/
    alt_paths/evidence_fact_ids/algorithm_version；@99 版本拒绝。"""
    world = _tool_world(db_session)
    run = _assistant_run(db_session, world["session"])

    for version in (1, 2):
        output = agent_tools.execute(
            db_session,
            run,
            world["session"],
            {"agent_kind": "assistant"},
            name=TOOL_GET_RELATIONSHIP_PATH,
            version=version,
            input_payload={"to_user_id": world["m"].id},
        )
        assert output["found"] is True
        assert output["concept_code"] == "Sm-Df"
        assert output["path_class"] == "affinal"
        assert [hop["edge_type"] for hop in output["path"]] == ["spouse", "parent"]
        assert output["algorithm_version"] == "v1"
        assert isinstance(output["alt_paths"], list)
        assert len(output["evidence_fact_ids"]) == 2
        assert {hop["name"] for hop in output["path"]} == {"工甲", "工丙"}

    explained = agent_tools.execute(
        db_session,
        run,
        world["session"],
        {"agent_kind": "assistant"},
        name=TOOL_EXPLAIN_STRUCTURAL_PATH,
        version=2,
        input_payload={"to_user_id": world["m"].id},
    )
    assert explained["explanation"] == "你的丈夫的女儿"
    assert explained["caveats"] == []
    assert explained["concept_code"] == "Sm-Df"

    # 缓存写入恰好一行（工具执行复用 get_or_compute）
    assert _cached_row_count(db_session) == 1

    with pytest.raises(fastapi.HTTPException) as exc_info:
        agent_tools.execute(
            db_session,
            run,
            world["session"],
            {"agent_kind": "assistant"},
            name=TOOL_GET_RELATIONSHIP_PATH,
            version=99,
            input_payload={"to_user_id": world["m"].id},
        )
    detail = exc_info.value.detail
    assert detail["__api_error__"]["code"] == "AGENT_TOOL_VERSION_UNSUPPORTED"  # type: ignore[index]


def test_registry_versions_and_compat_declaration() -> None:
    """注册表：两个关系工具 @2 主版本 + @1 兼容声明；其余工具仍 @1 单版本。"""
    spec_path = agent_tools.resolve_tool(TOOL_GET_RELATIONSHIP_PATH, 2)
    assert spec_path.version == 2 and spec_path.supported_versions == (1, 2)
    assert agent_tools.resolve_tool(TOOL_GET_RELATIONSHIP_PATH, 1).version == 2
    spec_explain = agent_tools.resolve_tool(TOOL_EXPLAIN_STRUCTURAL_PATH, 2)
    assert spec_explain.supported_versions == (1, 2)

    echo = agent_tools.resolve_tool("familygraph.echo", 1)
    assert echo.version == 1 and echo.supported_versions is None

    with pytest.raises(agent_tools.ToolProtocolError):
        agent_tools.resolve_tool("familygraph.echo", 2)


def test_flag_off_default_in_config() -> None:
    """默认关闭（config.py 合同），服务层动态读取便于按请求切换。"""
    assert config.RELATIONSHIP_INTELLIGENCE_ENABLED is False
