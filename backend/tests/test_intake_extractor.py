"""ProfileIntakeExtractor 四级解析与 E4a 工具合同测试（V2.3 Block E4a；KI-3/AC-KI4/AC-KI7）。

覆盖（对齐分派合同）：
- 四类 resolution 各至少一例：determined（奶奶的兄弟在有确认事实图中给出
  概念与称谓）、supported（合法概念但无图路径 → 提案 + SourceFact 零写入）、
  ambiguous（缺中间环节/未识别词 → 恰好一个追问）、conflicting（两位已确认
  妈妈 → 冲突列表非空）；
- RawRelationInput append-only：每次解析落一行且原文逐字保留，服务层不
  UPDATE 既有行；
- parse API 合同：active 成员 201、非成员 403 SPACE_FORBIDDEN_ACTOR、flag
  关闭 503 KINSHIP_FLAG_DISABLED、text 空/>80 字 422；
- agent_tools 注册表形状：三工具 version=1/min_kind=assistant，
  record_term_usage 输出合同，get_term_alternatives limit 默认 5 与 1..10 钳位；
- golden 确定性：相同 facts 相同输入两次解析输出一致（除 raw_text_id 自增）。
"""

from __future__ import annotations

from typing import Any

import fastapi
import pytest
from conftest import (
    auth_header,
    create_agent_fixture,
    create_agent_session,
    create_space_member,
    create_user_with_pin,
    login,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models.relationship_facts import RawRelationInput, SourceFact
from app.models.term_registry import TermEntry
from app.services import agent_events, agent_queue, agent_tools, intake_extractor, terms
from app.services import source_facts as sf
from app.utils.timeutil import utcnow

_KINSHIP_TOOL_NAMES = [
    agent_tools.TOOL_RESOLVE_FREE_TEXT_RELATION,
    agent_tools.TOOL_GET_TERM_ALTERNATIVES,
    agent_tools.TOOL_RECORD_TERM_USAGE,
]

_PARSE_RESULT_KEYS = {
    "raw_text_id",
    "normalized_text",
    "resolution_class",
    "candidate",
    "graph_proof",
    "proposals",
    "conflicts",
    "clarifying_question",
    "evidence_morphemes",
}


# ---- 造数辅助 ----


@pytest.fixture(autouse=True)
def _seed_builtin_packs(db_session: Session) -> None:
    """清表夹具会连带清掉迁移种子；本文件所有测试先幂等重灌内置语言包。"""
    terms.seed_builtin_packs(db_session)
    db_session.commit()  # 立即释放写锁，避免与 TestClient 请求连接互斥


@pytest.fixture()
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RELATIONSHIP_INTELLIGENCE_ENABLED", True)


@pytest.fixture()
def _flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "RELATIONSHIP_INTELLIGENCE_ENABLED", False)


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
    session.commit()  # 立即释放写锁（API 测试中 TestClient 用另一连接登录/请求）
    return fact


def _parse(session: Session, *, account_id: int, user_id: int, space_id: int, text: str):
    return intake_extractor.parse_free_text_relation(
        session,
        account_id=account_id,
        user_id=user_id,
        space_id=space_id,
        text=text,
        surface=intake_extractor.SURFACE_BROWSER,
    )


def _world_granduncle(session: Session) -> dict[str, Any]:
    """「奶奶的兄弟」黄金世界：viewer—父—奶奶—舅爷爷 全部确认事实。"""
    _, space = create_agent_fixture(session, name="解析")
    viewer = _person(session, space.id, "小王", "f")
    father = _person(session, space.id, "王父", "m")
    grandma = _person(session, space.id, "王奶奶", "f")
    granduncle = _person(session, space.id, "舅爷爷", "m")
    _confirm(session, "biological_parent", father.id, viewer.id, space_id=space.id)
    _confirm(session, "biological_parent", grandma.id, father.id, space_id=space.id)
    _confirm(session, "direct_sibling", granduncle.id, grandma.id, space_id=space.id)
    return {
        "space": space,
        "viewer": viewer,
        "father": father,
        "grandma": grandma,
        "granduncle": granduncle,
    }


def _fact_rows(session: Session) -> list[SourceFact]:
    session.flush()
    return list(session.scalars(select(SourceFact).order_by(SourceFact.id)).all())


def _login_header(client: TestClient, user) -> dict[str, str]:
    pair = login(client, user.name, "123456").json()
    return auth_header(pair)


# ---- determined：候选被确认路径完全证明 ----


def test_determined_grandmothers_brother(db_session: Session) -> None:
    world = _world_granduncle(db_session)
    result = _parse(
        db_session,
        account_id=world["viewer"].account.id,
        user_id=world["viewer"].id,
        space_id=world["space"].id,
        text="奶奶的兄弟",
    )
    assert result["resolution_class"] == "determined"
    assert result["candidate"]["concept_code"] == "Um-Uf-Bm"
    assert result["candidate"]["term"] == "舅爷爷"  # zh-CN 语言包四级实时解析
    assert result["candidate"]["term_source_level"] == "locale"
    assert result["graph_proof"] == {
        "found": True,
        "explanation_structural": "你的父亲的母亲的兄弟",
    }
    assert result["proposals"] == []
    assert result["conflicts"] == []
    assert result["clarifying_question"] is None
    assert result["evidence_morphemes"] == ["奶奶", "兄弟"]
    assert result["normalized_text"] == "奶奶的兄弟"


# ---- supported：合法概念但无图路径 → 只出提案，绝不写 SourceFact ----


def test_supported_last_hop_missing_proposes_atomic_fact(db_session: Session) -> None:
    """父亲已确认、兄弟分支尚无人物：仅最后一跳缺失 → 一句话确认提案。"""
    _, space = create_agent_fixture(db_session, name="提1")
    viewer = _person(db_session, space.id, "提子", "m")
    father = _person(db_session, space.id, "提父", "m")
    _confirm(db_session, "biological_parent", father.id, viewer.id, space_id=space.id)

    result = _parse(
        db_session,
        account_id=viewer.account.id,
        user_id=viewer.id,
        space_id=space.id,
        text="叔叔",
    )
    assert result["resolution_class"] == "supported"
    assert result["candidate"]["concept_code"] == "Um-Bm"
    assert len(result["proposals"]) == 1
    proposal = result["proposals"][0]
    assert proposal["kind"] == "source_fact"
    assert proposal["fact_type"] == "direct_sibling"
    assert "提案" in proposal["summary"] and "叔叔" in proposal["summary"]
    assert result["graph_proof"]["found"] is False
    assert result["clarifying_question"] is None and result["conflicts"] == []

    # 解析零新增：SourceFact 表仍只有种子父亲事实那一行（主键不变、无新行）
    facts = _fact_rows(db_session)
    assert len(facts) == 1
    assert (facts[0].subject_user_id, facts[0].object_user_id) == (father.id, viewer.id)


def test_supported_writes_zero_source_facts_on_empty_graph(db_session: Session) -> None:
    """最强零写入断言：空图上解析后 SourceFact 表保持零行。"""
    _, space = create_agent_fixture(db_session, name="提2")
    viewer = _person(db_session, space.id, "独子", "m")

    result = _parse(
        db_session,
        account_id=viewer.account.id,
        user_id=viewer.id,
        space_id=space.id,
        text="儿子",
    )
    assert result["resolution_class"] == "supported"
    assert result["candidate"]["concept_code"] == "Dm"
    assert result["proposals"][0]["summary"]
    assert _fact_rows(db_session) == []
    assert len(list(db_session.scalars(select(RawRelationInput)).all())) == 1


# ---- ambiguous：无法唯一确定 → 恰好一个追问 ----


def test_ambiguous_missing_middle_hop_asks_one_question(db_session: Session) -> None:
    """叔叔需要父亲记录定位兄弟分支；缺记录时追问而不虚构中间亲属。"""
    _, space = create_agent_fixture(db_session, name="问1")
    viewer = _person(db_session, space.id, "问子", "m")

    result = _parse(
        db_session,
        account_id=viewer.account.id,
        user_id=viewer.id,
        space_id=space.id,
        text="叔叔",
    )
    assert result["resolution_class"] == "ambiguous"
    question = result["clarifying_question"]
    assert isinstance(question, str) and question  # 恰一个问题且非空
    assert "父亲或母亲" in question
    assert result["proposals"] == [] and result["conflicts"] == []


def test_ambiguous_unknown_morpheme_asks_one_question(db_session: Session) -> None:
    _, space = create_agent_fixture(db_session, name="问2")
    viewer = _person(db_session, space.id, "问女", "f")

    result = _parse(
        db_session,
        account_id=viewer.account.id,
        user_id=viewer.id,
        space_id=space.id,
        text="隔壁老王",
    )
    assert result["resolution_class"] == "ambiguous"
    assert result["clarifying_question"]
    assert "隔壁老王" in (result["clarifying_question"] or "")
    assert result["candidate"]["concept_code"] is None
    assert result["proposals"] == []


# ---- conflicting：候选与已确认事实矛盾 ----


def test_conflicting_two_confirmed_mothers(db_session: Session) -> None:
    _, space = create_agent_fixture(db_session, name="冲1")
    viewer = _person(db_session, space.id, "冲子", "m")
    mother1 = _person(db_session, space.id, "生母", "f")
    mother2 = _person(db_session, space.id, "继母录成母", "f")
    _confirm(db_session, "biological_parent", mother1.id, viewer.id, space_id=space.id)
    _confirm(db_session, "biological_parent", mother2.id, viewer.id, space_id=space.id)
    before_ids = [f.id for f in _fact_rows(db_session)]

    result = _parse(
        db_session,
        account_id=viewer.account.id,
        user_id=viewer.id,
        space_id=space.id,
        text="妈妈",
    )
    assert result["resolution_class"] == "conflicting"
    assert len(result["conflicts"]) == 1
    assert "妈妈" in result["conflicts"][0] and "2 位" in result["conflicts"][0]
    # 冲突仍附原子事实提案（标注冲突），但绝不静默写库
    assert result["proposals"][0]["kind"] == "source_fact"
    assert result["proposals"][0]["fact_type"] == "biological_parent"
    assert "冲突" in result["proposals"][0]["summary"]
    assert result["clarifying_question"] is None
    assert [f.id for f in _fact_rows(db_session)] == before_ids  # 零写入


# ---- 原文 append-only（AC-KI3）----


def test_raw_input_appended_verbatim_and_never_updated(db_session: Session) -> None:
    """每次解析追加新行；原文逐字保留；服务层从不 UPDATE 既有行。"""
    _, space = create_agent_fixture(db_session, name="原1")
    viewer = _person(db_session, space.id, "原文者", "f")

    first = _parse(
        db_session,
        account_id=viewer.account.id,
        user_id=viewer.id,
        space_id=space.id,
        text="奶奶的兄弟",
    )
    second = _parse(
        db_session,
        account_id=viewer.account.id,
        user_id=viewer.id,
        space_id=space.id,
        text="俺妈今年八十了",  # 含词素外内容的自由文本同样逐字入库
    )

    rows = list(db_session.scalars(select(RawRelationInput).order_by(RawRelationInput.id)).all())
    assert len(rows) == 2
    assert rows[0].id == first["raw_text_id"] and rows[1].id == second["raw_text_id"]
    assert rows[0].text == "奶奶的兄弟"
    assert rows[1].text == "俺妈今年八十了"
    assert rows[0].author_account_id == viewer.account.id
    assert rows[0].context_json == {"space_id": space.id, "surface": "browser_parse"}

    # 第二次解析不触碰第一行：逐字段复核首行未被 UPDATE
    refetched = db_session.get(RawRelationInput, rows[0].id)
    assert refetched is not None
    assert refetched.text == rows[0].text
    assert refetched.created_at == rows[0].created_at
    assert refetched.author_account_id == rows[0].author_account_id


# ---- golden 确定性（AC-KI7 解析侧）----


def test_parse_is_deterministic_for_same_facts_and_input(db_session: Session) -> None:
    world = _world_granduncle(db_session)
    kwargs: dict[str, Any] = {
        "account_id": world["viewer"].account.id,
        "user_id": world["viewer"].id,
        "space_id": world["space"].id,
        "text": "奶奶的兄弟",
    }
    first = _parse(db_session, **kwargs)
    second = _parse(db_session, **kwargs)

    assert first["raw_text_id"] != second["raw_text_id"]  # 各自 append-only 落行
    strip_id = lambda r: {k: v for k, v in r.items() if k != "raw_text_id"}  # noqa: E731
    assert strip_id(first) == strip_id(second)


# ---- parse API 合同 ----


def test_api_parse_active_member_gets_201(
    client: TestClient, db_session: Session, _flag_on
) -> None:
    world = _world_granduncle(db_session)
    headers = _login_header(client, world["viewer"])

    response = client.post(
        "/api/kinship/parse",
        json={"space_id": world["space"].id, "text": "奶奶的兄弟"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert set(data) == _PARSE_RESULT_KEYS
    assert data["resolution_class"] == "determined"
    assert data["candidate"]["concept_code"] == "Um-Uf-Bm"


def test_api_parse_non_member_403(client: TestClient, db_session: Session, _flag_on) -> None:
    _, space = create_agent_fixture(db_session, name="API外")
    outsider = create_user_with_pin(db_session, "API外-人", "123456")
    headers = _login_header(client, outsider)

    response = client.post(
        "/api/kinship/parse",
        json={"space_id": space.id, "text": "奶奶"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SPACE_FORBIDDEN_ACTOR"


def test_api_parse_flag_off_503(client: TestClient, db_session: Session, _flag_off) -> None:
    _, space = create_agent_fixture(db_session, name="API旗")
    viewer = _person(db_session, space.id, "旗子", "f")
    headers = _login_header(client, viewer)

    response = client.post(
        "/api/kinship/parse",
        json={"space_id": space.id, "text": "奶奶"},
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "KINSHIP_FLAG_DISABLED"


def test_api_parse_text_validation_422(client: TestClient, db_session: Session, _flag_on) -> None:
    _, space = create_agent_fixture(db_session, name="API校")
    viewer = _person(db_session, space.id, "校子", "m")
    headers = _login_header(client, viewer)

    empty = client.post(
        "/api/kinship/parse",
        json={"space_id": space.id, "text": ""},
        headers=headers,
    )
    too_long = client.post(
        "/api/kinship/parse",
        json={"space_id": space.id, "text": "长" * 81},
        headers=headers,
    )
    assert empty.status_code == 422
    assert too_long.status_code == 422


# ---- agent 工具注册表形状与执行合同 ----


def _assistant_run(session: Session, session_row):
    run = agent_queue.enqueue_run(
        session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=list(_KINSHIP_TOOL_NAMES),
    )
    grant = agent_queue.lease_next(session, kind="assistant", leased_by="test-sidecar")
    assert grant is not None and grant.run.id == run.id
    seq = agent_events.next_seq(session, run.id)
    agent_events.append_events(
        session,
        grant.run,
        [agent_events.EventEntry(seq=seq, type="run.started", public_payload={})],
    )
    return grant.run


def test_kinship_tools_registry_shape() -> None:
    """三工具 version=1、min_kind=assistant、无兼容版本集声明。"""
    for name in _KINSHIP_TOOL_NAMES:
        spec = agent_tools.resolve_tool(name, 1)
        assert spec.version == 1
        assert spec.min_kind == "assistant"
        assert spec.supported_versions is None


def test_resolve_tool_end_to_end_records_assistant_surface(db_session: Session, _flag_on) -> None:
    world = _world_granduncle(db_session)
    session_row = create_agent_session(
        db_session, account_id=world["viewer"].account.id, space_id=world["space"].id
    )
    run = _assistant_run(db_session, session_row)

    output = agent_tools.execute(
        db_session,
        run,
        session_row,
        {"agent_kind": "assistant"},
        name=agent_tools.TOOL_RESOLVE_FREE_TEXT_RELATION,
        version=1,
        input_payload={"text": "奶奶的兄弟"},
    )
    assert output["resolution_class"] == "determined"
    assert output["candidate"]["concept_code"] == "Um-Uf-Bm"

    raw = db_session.scalar(select(RawRelationInput).order_by(RawRelationInput.id.desc()))
    assert raw is not None
    assert raw.text == "奶奶的兄弟"
    assert raw.context_json == {"space_id": world["space"].id, "surface": "assistant_tool"}


def test_record_term_usage_tool_output_contract(db_session: Session, _flag_on) -> None:
    _, space = create_agent_fixture(db_session, name="用1")
    member = _person(db_session, space.id, "用甲", "m")
    session_row = create_agent_session(db_session, account_id=member.account.id, space_id=space.id)
    session_row.term_usage_consent = True
    db_session.commit()
    run = _assistant_run(db_session, session_row)

    def call() -> dict[str, Any]:
        return agent_tools.execute(
            db_session,
            run,
            session_row,
            {"agent_kind": "assistant"},
            name=agent_tools.TOOL_RECORD_TERM_USAGE,
            version=1,
            input_payload={"concept_code": "Um", "term": "爹地", "consent_confirmed": True},
        )

    # 合同：demoted 反映载体行当前非 active（单人不足两人晋升门槛）
    first = call()
    assert first == {
        "recorded": True,
        "promotion": {"promoted": False, "demoted": True, "eligible_accounts": 1},
    }
    second = call()  # 同账号重复只计一次
    assert second["recorded"] is False
    assert second == {
        "recorded": False,
        "promotion": {"promoted": False, "demoted": True, "eligible_accounts": 1},
    }


def test_get_term_alternatives_limit_default_five_and_clamp(db_session: Session, _flag_on) -> None:
    _, space = create_agent_fixture(db_session, name="备1")
    member = _person(db_session, space.id, "备甲", "m")
    now = utcnow()
    for index in range(7):  # 7 条空间词条：验证默认截断与上限钳位
        db_session.add(
            TermEntry(
                concept_code="Um",
                level="space",
                space_id=space.id,
                owner_account_id=None,
                locale=None,
                term=f"t{index + 1}",
                status="active",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
    db_session.commit()
    terms.set_personal_term(
        db_session,
        account_id=member.account.id,
        space_id=space.id,
        concept_code="Um",
        term="俺爹",
    )
    session_row = create_agent_session(db_session, account_id=member.account.id, space_id=space.id)
    run = _assistant_run(db_session, session_row)

    def call(limit: int | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"concept_code": "Um"}
        if limit is not None:
            payload["limit"] = limit
        return agent_tools.execute(
            db_session,
            run,
            session_row,
            {"agent_kind": "assistant"},
            name=agent_tools.TOOL_GET_TERM_ALTERNATIVES,
            version=1,
            input_payload=payload,
        )

    default_out = call(None)
    assert default_out["concept_code"] == "Um"
    assert default_out["personal"] == {"term": "俺爹", "source": "personal"}
    # 默认 limit=5：8+ 个候选只返回前 5（空间层按 term 字典序 t1..t5）
    assert [alt["term"] for alt in default_out["alternatives"]] == ["t1", "t2", "t3", "t4", "t5"]

    max_out = call(10)
    terms_out = [alt["term"] for alt in max_out["alternatives"]]
    assert terms_out[:7] == ["t1", "t2", "t3", "t4", "t5", "t6", "t7"]
    assert len(terms_out) >= 8  # 空间 7 条 + locale 兜底（爸爸）

    for bad_limit in (0, 11):
        with pytest.raises(fastapi.HTTPException) as excinfo:
            call(bad_limit)
        detail = excinfo.value.detail
        assert detail["__api_error__"]["code"] == "AGENT_TOOL_SCHEMA_INVALID"  # type: ignore[index]


def test_kinship_tools_flag_off_denied(db_session: Session, _flag_off) -> None:
    """Flag 关闭：三工具一律 503 KINSHIP_FLAG_DISABLED（与浏览器面同一口径）。"""
    _, space = create_agent_fixture(db_session, name="旗工")
    member = _person(db_session, space.id, "旗甲", "f")
    session_row = create_agent_session(db_session, account_id=member.account.id, space_id=space.id)
    run = _assistant_run(db_session, session_row)

    cases: list[tuple[str, dict[str, Any]]] = [
        (agent_tools.TOOL_RESOLVE_FREE_TEXT_RELATION, {"text": "奶奶"}),
        (agent_tools.TOOL_GET_TERM_ALTERNATIVES, {"concept_code": "Um"}),
        (agent_tools.TOOL_RECORD_TERM_USAGE, {"concept_code": "Um", "term": "爹地"}),
    ]
    for name, payload in cases:
        with pytest.raises(fastapi.HTTPException) as excinfo:
            agent_tools.execute(
                db_session,
                run,
                session_row,
                {"agent_kind": "assistant"},
                name=name,
                version=1,
                input_payload=payload,
            )
        detail = excinfo.value.detail
        assert excinfo.value.status_code == 503
        assert detail["__api_error__"]["code"] == "KINSHIP_FLAG_DISABLED"  # type: ignore[index]
