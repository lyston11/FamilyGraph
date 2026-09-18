"""管家自动修复称谓（09-13-steward-term-autofix）：词典补全 + 长幼消歧 + 泛化。

黄金用例来自 2026-09-13 用户截图（viewer 王德海 1953）：
- 父亲之女（1962）→ 妹妹（原「你的父亲的女儿」结构回退）
- 儿子之子 → 孙子、儿子之女 → 孙女
- 父亲之女的丈夫 → 妹夫（原「你的父亲的女儿的丈夫」）
- 妻子/儿子/儿媳 等既有正确显示不回归
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models.term_registry import BUILTIN_SYSTEM_TERMS, BUILTIN_ZH_CN_TERMS
from app.services import personal_family_view, terms
from app.services import source_facts as sf
from app.services.relationship_graph import load_birth_years
from conftest import create_agent_fixture, create_space_member, create_user_with_pin


@pytest.fixture(autouse=True)
def _seed_builtin_packs(db_session) -> None:
    """清表夹具会连带清掉迁移种子；本文件所有测试先幂等重灌内置包。"""
    terms.seed_builtin_packs(db_session)
    db_session.commit()


def _birth(year: int, month: int = 6, cal_type: str = "solar") -> dict:
    return {"date": f"{year}-{month:02d}-15", "cal_type": cal_type}


def _confirm(session, fact_type: str, subject_id: int, object_id: int, space_id: int) -> None:
    fact = sf.create_source_fact(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        provenance="manual_entry",
        space_id=space_id,
    )
    sf.transition_source_fact(session, fact, "confirm")


def _solar_births(person_years: dict[int, int]) -> dict:
    return {uid: ("solar", year) for uid, year in person_years.items()}


# ---- 1. 词典补全 ----


def test_golden_codes_present_in_zh_cn_pack() -> None:
    """截图黄金用例与常用亲属码全部入包（泛化词形态）。"""
    zh = dict(BUILTIN_ZH_CN_TERMS)
    expected = {
        "Um-Df": "姐妹",  # 父亲的女儿（消歧类）
        "Um-Dm": "兄弟",
        "Dm-Dm": "孙子",
        "Dm-Df": "孙女",
        "Df-Dm": "外孙",
        "Df-Df": "外孙女",
        "Bf-Sm": "姐妹的丈夫",  # 姐夫/妹夫（消歧类）
        "Bm-Sf": "兄弟的妻子",  # 嫂子/弟媳（消歧类）
        "Um-Df-Sm": "姐妹的丈夫",  # 截图黄金用例 3 跳
        "Bm-Dm": "侄子",
        "Bf-Dm": "外甥",
        "Um-Um-Um": "曾祖父",
        "Dm-Dm-Dm": "曾孙",
        "Um-Um-Um-Um": "高祖父",
        "U-U": "祖父母",  # 性别未知中性
    }
    for code, term in expected.items():
        assert zh.get(code) == term, f"zh-CN 缺少/不一致: {code}"
    system = dict(BUILTIN_SYSTEM_TERMS)
    assert system["U-D"] == "兄弟姐妹"
    assert system["B-D"] == "侄甥"


def test_zh_cn_codes_validate_against_concept_code_contract() -> None:
    """所有 zh-CN 码符合 concept_code 编码合同（可被 resolve_term 接受）。"""
    import re

    pattern = re.compile(r"^SELF$|^[A-Z][sga]?[mf]?(?:-[A-Z][sga]?[mf]?)*$")
    for code, _term in BUILTIN_ZH_CN_TERMS:
        assert pattern.match(code), code


# ---- 2. 长幼消歧 ----


def _sibling_via_parent_path(viewer_id: int, parent_id: int, sibling_id: int, spouse_id=None):
    path = [
        {
            "from": viewer_id,
            "to": parent_id,
            "edge_type": "parent",
            "subtype": "biological",
            "direction": "up",
            "fact_id": 11,
        },
        {
            "from": parent_id,
            "to": sibling_id,
            "edge_type": "parent",
            "subtype": "biological",
            "direction": "down",
            "fact_id": 12,
        },
    ]
    if spouse_id is not None:
        path.append(
            {
                "from": sibling_id,
                "to": spouse_id,
                "edge_type": "spouse",
                "subtype": None,
                "direction": "sym",
                "fact_id": 13,
            }
        )
    return path


def test_variant_upgrades_to_younger_sister(db_session) -> None:
    """viewer(1953) 的父亲之女(1962) → 妹妹；无出生数据 → 泛化词姐妹。"""
    _account, space = create_agent_fixture(db_session, name="disambig")
    viewer = create_user_with_pin(db_session, "da-viewer", "123456", gender="m", birth=_birth(1953))
    ctx = terms.VariantContext(
        viewer_user_id=viewer.id,
        path=_sibling_via_parent_path(viewer.id, 2, 3),
        births=_solar_births({viewer.id: 1953, 2: 1930, 3: 1962}),
    )
    resolved = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df",
        structural_description="你的父亲的女儿",
        variant_context=ctx,
    )
    assert resolved["term"] == "妹妹"
    assert resolved["source_level"] == "locale"

    plain = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df",
        structural_description="你的父亲的女儿",
    )
    assert plain["term"] == "姐妹"


def test_variant_spouse_terms_by_age(db_session) -> None:
    """姐妹的丈夫按长幼 → 姐夫/妹夫；兄弟的妻子 → 嫂子/弟媳。"""
    _account, space = create_agent_fixture(db_session, name="disambig-spouse")
    viewer = create_user_with_pin(db_session, "da-spouse", "123456", gender="m", birth=_birth(1953))
    path = _sibling_via_parent_path(viewer.id, 2, 3, spouse_id=4)

    younger = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df-Sm",
        structural_description="你的父亲的女儿的丈夫",
        variant_context=terms.VariantContext(
            viewer_user_id=viewer.id,
            path=path,
            births=_solar_births({viewer.id: 1953, 3: 1962, 4: 1960}),
        ),
    )
    assert younger["term"] == "妹夫"

    elder = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df-Sm",
        structural_description="你的父亲的女儿的丈夫",
        variant_context=terms.VariantContext(
            viewer_user_id=viewer.id,
            path=path,
            births=_solar_births({viewer.id: 1953, 3: 1940, 4: 1939}),
        ),
    )
    assert elder["term"] == "姐夫"


def test_variant_guards_missing_mixed_and_same_year_births(db_session) -> None:
    """出生缺失/混合日历/同年 → 不消歧（泛化词兜底）。"""
    _account, space = create_agent_fixture(db_session, name="disambig-guard")
    viewer = create_user_with_pin(db_session, "da-guard", "123456", gender="m", birth=_birth(1953))
    path = _sibling_via_parent_path(viewer.id, 2, 3)

    missing = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df",
        structural_description="你的父亲的女儿",
        variant_context=terms.VariantContext(
            viewer_user_id=viewer.id, path=path, births={viewer.id: None, 2: None, 3: None}
        ),
    )
    assert missing["term"] == "姐妹"

    mixed = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df",
        structural_description="你的父亲的女儿",
        variant_context=terms.VariantContext(
            viewer_user_id=viewer.id,
            path=path,
            births={viewer.id: ("solar", 1953), 2: ("lunar", 1930), 3: ("solar", 1962)},
        ),
    )
    assert mixed["term"] == "姐妹"

    same_year = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df",
        structural_description="你的父亲的女儿",
        variant_context=terms.VariantContext(
            viewer_user_id=viewer.id,
            path=path,
            births=_solar_births({viewer.id: 1962, 3: 1962}),
        ),
    )
    assert same_year["term"] == "姐妹"


def test_personal_term_overrides_variant(db_session) -> None:
    """personal 层用户自定义词条优先于消歧升级（用户叫法永不覆盖）。"""
    _account, space = create_agent_fixture(db_session, name="disambig-personal")
    viewer = create_user_with_pin(
        db_session, "da-personal", "123456", gender="m", birth=_birth(1953)
    )
    terms.set_personal_term(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df",
        term="老妹",
    )
    resolved = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df",
        structural_description="你的父亲的女儿",
        variant_context=terms.VariantContext(
            viewer_user_id=viewer.id,
            path=_sibling_via_parent_path(viewer.id, 2, 3),
            births=_solar_births({viewer.id: 1953, 3: 1962}),
        ),
    )
    assert resolved["term"] == "老妹"
    assert resolved["source_level"] == "personal"


# ---- 3. 长链泛化 ----


def test_generalized_term_names_longest_prefix(db_session) -> None:
    """未收码走第 5 级泛化：最长命名前缀 + 残链小词。"""
    _account, space = create_agent_fixture(db_session, name="generalize")
    viewer = create_user_with_pin(db_session, "gen-viewer", "123456", gender="m")
    resolved = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Um-Sf",
        structural_description="你的爷爷的妻子",
    )
    assert resolved["term"] == "爷爷的妻子"
    assert resolved["source_level"] == terms.SOURCE_LEVEL_DERIVED

    deep = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df-Sm-Um",
        structural_description="你的父亲的女儿的丈夫的父亲",
    )
    assert deep["term"] == "姐妹的丈夫的父亲"
    assert deep["source_level"] == terms.SOURCE_LEVEL_DERIVED


def test_unnameable_code_falls_back_to_structural(db_session) -> None:
    """无任何可命名前缀（非亲属域字母）→ 维持结构描述。"""
    _account, space = create_agent_fixture(db_session, name="generalize-none")
    viewer = create_user_with_pin(db_session, "gen-none", "123456", gender="m")
    resolved = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Qm",
        structural_description="你的未识别亲属",
    )
    assert resolved["term"] == "你的未识别亲属"
    assert resolved["source_level"] == terms.SOURCE_LEVEL_STRUCTURAL


# ---- 4. 图谱出生数据 ----


def test_load_birth_years_follows_display_purpose(db_session) -> None:
    """load_birth_years 按展示口径（PURPOSE_GRAPH）收集出生数据。

    空间成员 household_detail 层 birth 明文 → (cal_type, 年份)；
    缺失/不可解析 → None。
    """
    _account, space = create_agent_fixture(db_session, name="birth-years")
    viewer = create_user_with_pin(db_session, "by-viewer", "123456", gender="m", birth=_birth(1953))
    with_solar = create_user_with_pin(
        db_session, "by-solar", "123456", gender="f", birth=_birth(1962)
    )
    with_lunar = create_user_with_pin(
        db_session, "by-lunar", "123456", gender="m", birth=_birth(1948, cal_type="lunar")
    )
    no_birth = create_user_with_pin(db_session, "by-none", "123456", gender="f")
    bad_birth = create_user_with_pin(
        db_session, "by-bad", "123456", gender="m", birth={"cal_type": "solar", "date": "abc"}
    )
    create_space_member(db_session, space.id, viewer.id)
    for user in (with_solar, with_lunar, no_birth, bad_birth):
        create_space_member(db_session, space.id, user.id)

    births = load_birth_years(
        db_session,
        viewer_user_id=viewer.id,
        space_id=space.id,
        user_ids={viewer.id, with_solar.id, with_lunar.id, no_birth.id, bad_birth.id},
    )
    assert births[viewer.id] == ("solar", 1953)
    assert births[with_solar.id] == ("solar", 1962)
    assert births[with_lunar.id] == ("lunar", 1948)
    assert births[no_birth.id] is None
    assert births[bad_birth.id] is None


def test_minor_birth_masked_so_no_disambiguation(db_session) -> None:
    """未成年人 birth 对非本人 masked → 出生年为 None → 消歧不升级。

    成员（household_detail 层）本可 clear，但未成年人 overlay 最后收紧。
    """
    _account, space = create_agent_fixture(db_session, name="birth-minor")
    viewer = create_user_with_pin(db_session, "bm-viewer", "123456", gender="m", birth=_birth(1953))
    this_year = date.today().year
    minor_sibling = create_user_with_pin(
        db_session,
        "bm-minor",
        "123456",
        gender="f",
        birth=_birth(this_year - 10),
    )
    create_space_member(db_session, space.id, viewer.id)
    create_space_member(db_session, space.id, minor_sibling.id)
    parent = create_user_with_pin(db_session, "bm-parent", "123456", gender="m")
    create_space_member(db_session, space.id, parent.id)
    _confirm(db_session, "biological_parent", parent.id, viewer.id, space.id)
    _confirm(db_session, "biological_parent", parent.id, minor_sibling.id, space.id)

    births = load_birth_years(
        db_session,
        viewer_user_id=viewer.id,
        space_id=space.id,
        user_ids={viewer.id, parent.id, minor_sibling.id},
    )
    assert births[minor_sibling.id] is None

    resolved = terms.resolve_term_or_structural(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        concept_code="Um-Df",
        structural_description="你的父亲的女儿",
        variant_context=terms.VariantContext(
            viewer_user_id=viewer.id,
            path=[
                {
                    "from": viewer.id,
                    "to": parent.id,
                    "edge_type": "parent",
                    "subtype": "biological",
                    "direction": "up",
                    "fact_id": 1,
                },
                {
                    "from": parent.id,
                    "to": minor_sibling.id,
                    "edge_type": "parent",
                    "subtype": "biological",
                    "direction": "down",
                    "fact_id": 2,
                },
            ],
            births=births,
        ),
    )
    assert resolved["term"] == "姐妹"


# ---- 5. PFV 端到端黄金用例（截图回归）----


def test_pfv_golden_terms_from_screenshot(db_session) -> None:
    """viewer(1953) 树上：妹妹/妹夫/孙子/孙女取代结构回退描述。"""
    _account, space = create_agent_fixture(db_session, name="golden")
    viewer = create_user_with_pin(
        db_session, "golden-viewer", "123456", gender="m", birth=_birth(1953)
    )
    father = create_user_with_pin(
        db_session, "golden-father", "123456", gender="m", birth=_birth(1930)
    )
    sister = create_user_with_pin(
        db_session, "golden-sister", "123456", gender="f", birth=_birth(1962)
    )
    brother_in_law = create_user_with_pin(
        db_session, "golden-bil", "123456", gender="m", birth=_birth(1960)
    )
    son = create_user_with_pin(db_session, "golden-son", "123456", gender="m", birth=_birth(1980))
    grandson = create_user_with_pin(
        db_session, "golden-grandson", "123456", gender="m", birth=_birth(2008)
    )
    granddaughter = create_user_with_pin(
        db_session, "golden-gd", "123456", gender="f", birth=_birth(2010)
    )
    daughter_in_law = create_user_with_pin(
        db_session, "golden-dil", "123456", gender="f", birth=_birth(1982)
    )
    create_space_member(db_session, space.id, viewer.id)
    for user in (father, sister, brother_in_law, son, grandson, granddaughter, daughter_in_law):
        create_space_member(db_session, space.id, user.id)

    _confirm(db_session, "biological_parent", father.id, viewer.id, space.id)
    _confirm(db_session, "biological_parent", father.id, sister.id, space.id)
    _confirm(db_session, "spouse", sister.id, brother_in_law.id, space.id)
    _confirm(db_session, "biological_parent", viewer.id, son.id, space.id)
    _confirm(db_session, "biological_parent", son.id, grandson.id, space.id)
    _confirm(db_session, "biological_parent", son.id, granddaughter.id, space.id)
    _confirm(db_session, "spouse", son.id, daughter_in_law.id, space.id)

    personal_family_view.rebuild_view(db_session, account=viewer.account, space_id=space.id)
    payload = personal_family_view.view_payload(
        db_session, account=viewer.account, space_id=space.id
    )
    terms_by_user = {edge["to_user_id"]: edge for edge in payload["edges"]}
    code_by_user = {edge["to_user_id"]: edge["concept_code"] for edge in payload["edges"]}

    assert code_by_user[father.id] == "Um"
    assert terms_by_user[father.id]["term"] == "爸爸"  # 既有显示不回归
    assert code_by_user[sister.id] == "Um-Df"
    assert terms_by_user[sister.id]["term"] == "妹妹"  # 原「你的父亲的女儿」
    assert code_by_user[brother_in_law.id] == "Um-Df-Sm"
    assert terms_by_user[brother_in_law.id]["term"] == "妹夫"  # 原「你的父亲的女儿的丈夫」
    assert code_by_user[grandson.id] == "Dm-Dm"
    assert terms_by_user[grandson.id]["term"] == "孙子"  # 原「你的儿子的儿子」
    assert code_by_user[granddaughter.id] == "Dm-Df"
    assert terms_by_user[granddaughter.id]["term"] == "孙女"
    assert code_by_user[daughter_in_law.id] == "Dm-Sf"
    assert terms_by_user[daughter_in_law.id]["term"] == "儿媳"  # 既有显示不回归


def test_pfv_computation_version_bumped(db_session) -> None:
    """版本化发布升级计算合同，旧称谓缓存不能直接充当当前完整结果。"""
    assert personal_family_view.COMPUTATION_VERSION == "pfv-v6-space-members"


def test_compose_resolution_view_sibling_terms(db_session) -> None:
    """kinship resolve 组合层与 PFV 同语义：妹妹/姐夫实时解析。"""
    _account, space = create_agent_fixture(db_session, name="compose-golden")
    viewer = create_user_with_pin(db_session, "cg-viewer", "123456", gender="m", birth=_birth(1953))
    father = create_user_with_pin(db_session, "cg-father", "123456", gender="m", birth=_birth(1930))
    sister = create_user_with_pin(db_session, "cg-sister", "123456", gender="f", birth=_birth(1962))
    create_space_member(db_session, space.id, viewer.id)
    for user in (father, sister):
        create_space_member(db_session, space.id, user.id)
    _confirm(db_session, "biological_parent", father.id, viewer.id, space.id)
    _confirm(db_session, "biological_parent", father.id, sister.id, space.id)

    view = terms.compose_resolution_view(
        db_session,
        viewer_user_id=viewer.id,
        target_user_id=sister.id,
        space_id=space.id,
        account_id=viewer.account.id,
    )
    assert view["found"] is True
    assert view["concept_code"] == "Um-Df"
    assert view["term"] == "妹妹"
    assert view["term_source_level"] == "locale"
