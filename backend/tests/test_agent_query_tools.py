"""V2.2 只读 Assistant 领域工具测试（Block C1）。

覆盖任务验收：六工具合同形状（经 internal execute 端点）、跨空间防枚举、
minor 字段零泄露、关系路径可达性、零写入断言、分页/limit/输出截断上限、
V2.1 协议校验对新工具仍然生效。
"""

import json

import pytest
from conftest import (
    create_agent_fixture,
    create_agent_session,
    create_space_member,
    create_user_with_pin,
)
from fastapi import HTTPException
from sqlalchemy import text

from app.models.audit_log import AuditLog
from app.models.relation import Relation
from app.models.space import FamilySpace, SpaceProfileRef
from app.models.user import User
from app.models.v2_foundation import DisclosurePreference
from app.services import agent_events, agent_query, agent_queue, agent_tools, visibility
from app.services.agent_query import enforce_output_limit
from app.services.agent_tokens import issue_service_token
from app.utils import timeutil

# ---- 世界构造（implement.md E2E fixture 要求的最小形态） ----

A_NAME, B_NAME, D_NAME, M_NAME, F_NAME = "张伟", "李娜", "王强", "张小明", "钱多多"
P_NAME, E_NAME, G_NAME = "赵阿福", "张老太君", "孙七"
M_BIRTH_DATE = "2018-05-01"
M_BIO = "小学生的秘密日记"
ALL_SIX_TOOLS = sorted(agent_query.QUERY_TOOL_NAMES)


def _add_disclosure(db, user: User, categories: tuple[str, ...]) -> None:
    now = timeutil.utcnow()
    for category in categories:
        db.add(
            DisclosurePreference(
                profile_id=user.id,
                category=category,
                scope="global",
                space_id=None,
                allowed=True,
                updated_at=now,
            )
        )
    db.commit()


def _add_relation(db, *, from_user: User, to_user: User, dir_class: str, label: str) -> Relation:
    now = timeutil.utcnow()
    rel = Relation(
        from_user=from_user.id,
        to_user=to_user.id,
        dir_class=dir_class,
        label=label,
        created_by=from_user.id,
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(rel)
    db.commit()
    return rel


def _world(db):
    """两人多空间 + provisional/minor/masked 人物；返回实体字典。

    - household H「张家老宅」：成员 A(owner)/B/D/M/F，provisional P 仅 ref；
    - lineage L「张氏宗族」：成员 A(owner)/E/G（对 B 的 H-scope 完全不可见）；
    - 边：A=spouse=B（老伴）、A=younger→M（孙女）、B=peer=F（表哥）、
      A=elder→E（姑婆，跨空间直系边仅授予 lineage 基线，不进 H 的候选集）；
    - A 全局披露 dates+bio（lineage 层字段扩展投影的对照）。
    """
    now = timeutil.utcnow()
    a = create_user_with_pin(
        db,
        A_NAME,
        "111111",
        birth={"cal_type": "solar", "date": "1970-01-01"},
        bio="族谱记载的生平",
    )
    b = create_user_with_pin(db, B_NAME, "222222")
    d = create_user_with_pin(db, D_NAME, "333333", bio="私人日记内容")
    m = create_user_with_pin(
        db, M_NAME, "444444", birth={"cal_type": "solar", "date": M_BIRTH_DATE}, bio=M_BIO
    )
    f = create_user_with_pin(db, F_NAME, "555555")
    p = create_user_with_pin(db, P_NAME, "666666", profile_status="provisional")
    e = create_user_with_pin(db, E_NAME, "777777")
    g = create_user_with_pin(db, G_NAME, "888888")

    household = FamilySpace(name="张家老宅", kind="household", owner_id=a.id, created_at=now)
    lineage = FamilySpace(name="张氏宗族", kind="lineage", owner_id=a.id, created_at=now)
    db.add_all([household, lineage])
    db.commit()
    for user, role in ((a, "owner"), (b, "member"), (d, "member"), (m, "member"), (f, "member")):
        create_space_member(db, household.id, user.id, role=role)
    for user in (e, g):
        create_space_member(db, lineage.id, user.id)
    db.add(SpaceProfileRef(space_id=household.id, user_id=p.id, added_by=a.id, created_at=now))
    db.commit()
    _add_disclosure(db, a, ("dates", "bio"))

    spouse = _add_relation(db, from_user=a, to_user=b, dir_class="spouse", label="老伴")
    grandchild = _add_relation(db, from_user=a, to_user=m, dir_class="younger", label="孙女")
    peer_edge = _add_relation(db, from_user=b, to_user=f, dir_class="peer", label="表哥")
    elder_e = _add_relation(db, from_user=a, to_user=e, dir_class="elder", label="姑婆")
    return {
        "a": a,
        "b": b,
        "d": d,
        "m": m,
        "f": f,
        "p": p,
        "e": e,
        "g": g,
        "household": household,
        "lineage": lineage,
        "spouse": spouse,
        "grandchild": grandchild,
        "peer": peer_edge,
        "elder_e": elder_e,
    }


# ---- 服务层执行辅助（复用 V2.1 测试惯例） ----


def _assistant_run(db, agent_session_row, allowlist):
    run = agent_queue.enqueue_run(
        db,
        agent_session=agent_session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=list(allowlist),
    )
    grant = agent_queue.lease_next(db, kind="assistant", leased_by="test-sidecar")
    assert grant is not None and grant.run.id == run.id
    seq = agent_events.next_seq(db, run.id)
    agent_events.append_events(
        db, grant.run, [agent_events.EventEntry(seq=seq, type="run.started", public_payload={})]
    )
    return grant.run


def _call(db, run, session_row, name, payload=None, claims=None):
    return agent_tools.execute(
        db,
        run,
        session_row,
        claims or {"agent_kind": "assistant"},
        name=name,
        version=1,
        input_payload=payload if payload is not None else {},
    )


def _b_context(db, world):
    """B 的 assistant 会话（scope=household H）。"""
    return create_agent_session(
        db, account_id=world["b"].account.id, space_id=world["household"].id
    )


def _error_code(exc_info) -> str:
    detail = exc_info.value.detail  # type: ignore[union-attr]
    assert isinstance(detail, dict) and "__api_error__" in detail
    return str(detail["__api_error__"]["code"])


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- 合同形状：经 internal execute 端点 ----


def test_six_tools_contract_via_internal_endpoint(client, db_session):
    world = _world(db_session)
    session_row = _b_context(db_session, world)
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=ALL_SIX_TOOLS,
    )
    lease = client.post(
        "/internal/agent/jobs/lease",
        json={"leased_by": "sidecar-c1"},
        headers=_bearer(issue_service_token()),
    )
    assert lease.status_code == 200
    body = lease.json()
    assert body["run_id"] == run.id
    headers = _bearer(body["run_token"])
    started = client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={"events": [{"seq": 0, "type": "run.started", "public_payload": {}}]},
        headers=headers,
    )
    assert started.status_code == 200

    def _execute(name: str, payload: dict) -> dict:
        response = client.post(
            f"/internal/agent/runs/{run.id}/tools/{name}/execute",
            json={"version": 1, "input": payload, "tool_call_id": f"tc-{name}"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True and data["tool"] == name and data["version"] == 1
        return data["output"]

    self_ctx = _execute(agent_query.TOOL_GET_SELF_CONTEXT, {})
    assert self_ctx == {
        "space_id": world["household"].id,
        "space_name": "张家老宅",
        "space_kind": "household",
        "self_user_id": world["b"].id,
        "self_name": B_NAME,
        "profile_status": "identity_confirmed",
        "visible_people_count": 6,  # A/B/D/M/F 成员 + P(ref)；E/G 在 lineage 空间不计
    }

    listing = _execute(agent_query.TOOL_LIST_VISIBLE_PEOPLE, {"query": "", "limit": 10})
    assert {person["user_id"] for person in listing["people"]} == {
        world[key].id for key in ("a", "b", "d", "m", "f", "p")
    }
    for person in listing["people"]:
        assert set(person) == {"user_id", "name", "generation", "relation_to_self", "fact_state"}
        assert person["fact_state"] in ("confirmed", "provisional", "masked")

    summary = _execute(agent_query.TOOL_GET_PROFILE_SUMMARY, {"user_id": world["a"].id})
    assert summary["visibility_level"] == visibility.LEVEL_LINEAGE_SUMMARY
    assert summary["fact_state"] == "confirmed"
    assert summary["birth"]["date"] == "1970-01-01"  # 已披露类别在 lineage 层扩展投影
    assert summary["gender"] == visibility.MASKED  # 未披露类别保持遮罩哨兵

    search = _execute(agent_query.TOOL_SEARCH_SPACE, {"query": A_NAME})
    assert search == {"hits": [{"user_id": world["a"].id, "name": A_NAME, "matched_field": "name"}]}

    path = _execute(
        agent_query.TOOL_GET_RELATIONSHIP_PATH,
        {"to_user_id": world["m"].id, "from_user_id": world["b"].id},
    )
    assert path["found"] is True and path["path_class"] == "multi_hop"
    assert [hop["dir_class"] for hop in path["path"]] == ["spouse", "younger"]
    assert path["evidence_relation_ids"] == [world["spouse"].id, world["grandchild"].id]

    explained = _execute(
        agent_query.TOOL_EXPLAIN_STRUCTURAL_PATH,
        {"to_user_id": world["m"].id, "from_user_id": world["b"].id},
    )
    assert set(explained) == {"explanation", "path_class", "caveats"}
    assert explained["path_class"] == "multi_hop"
    assert B_NAME in explained["explanation"] and M_NAME in explained["explanation"]
    assert len(explained["explanation"]) <= 600

    # 额外字段仍被 V2.1 schema 校验拒绝（fail-closed 不因新工具放宽）
    bad = client.post(
        f"/internal/agent/runs/{run.id}/tools/{agent_query.TOOL_GET_SELF_CONTEXT}/execute",
        json={"version": 1, "input": {"space_id": world["lineage"].id}},
        headers=headers,
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "AGENT_TOOL_SCHEMA_INVALID"


# ---- 可见性与防枚举 ----


def test_profile_not_available_and_scope_isolation(db_session):
    """B 对 lineage 空间外人物 summary → FG_PROFILE_NOT_AVAILABLE；list/search 不含。"""
    world = _world(db_session)
    session_row = _b_context(db_session, world)
    run = _assistant_run(db_session, session_row, ALL_SIX_TOOLS)

    with pytest.raises(HTTPException) as exc_info:
        _call(
            db_session,
            run,
            session_row,
            agent_query.TOOL_GET_PROFILE_SUMMARY,
            {"user_id": world["g"].id},
        )
    assert _error_code(exc_info) == "FG_PROFILE_NOT_AVAILABLE"
    with pytest.raises(HTTPException) as exc_info:
        _call(
            db_session, run, session_row, agent_query.TOOL_GET_PROFILE_SUMMARY, {"user_id": 999999}
        )
    assert _error_code(exc_info) == "FG_PROFILE_NOT_AVAILABLE"  # 不存在与不可见同码

    listing = _call(db_session, run, session_row, agent_query.TOOL_LIST_VISIBLE_PEOPLE)
    listed_ids = {person["user_id"] for person in listing["people"]}
    assert world["g"].id not in listed_ids and world["e"].id not in listed_ids

    search = _call(db_session, run, session_row, agent_query.TOOL_SEARCH_SPACE, {"query": G_NAME})
    assert search["hits"] == []

    # 对照直接调 visibility 投影：G/E 对 B 在 H-scope 下不可见，A 可见
    actor = db_session.get(User, world["b"].id)
    for key, expect_visible in (("a", True), ("e", False), ("g", False)):
        target = db_session.get(User, world[key].id)
        decision = visibility.evaluate(
            db_session,
            actor,
            target,
            space_context=world["household"].id,
            purpose=visibility.PURPOSE_AGENT,
        )
        assert decision.visible is expect_visible


def test_fact_state_mapping(db_session):
    """confirmed / provisional / masked 三态映射符合合同语义。"""
    world = _world(db_session)
    session_row = _b_context(db_session, world)
    run = _assistant_run(db_session, session_row, ALL_SIX_TOOLS)
    listing = _call(db_session, run, session_row, agent_query.TOOL_LIST_VISIBLE_PEOPLE)
    states = {person["user_id"]: person["fact_state"] for person in listing["people"]}
    generations = {person["user_id"]: person["generation"] for person in listing["people"]}
    relations = {person["user_id"]: person["relation_to_self"] for person in listing["people"]}
    assert states[world["b"].id] == "confirmed"  # 本人已确档
    assert states[world["a"].id] == "confirmed"  # 已确档且披露 dates/bio
    assert states[world["d"].id] == "masked"  # 已确档但内容全遮蔽
    assert states[world["m"].id] == "masked"  # minor overlay 收紧后内容全遮蔽
    assert states[world["p"].id] == "provisional"  # 未确档
    assert generations[world["a"].id] == 0  # 配偶同代
    assert generations[world["m"].id] == -1  # 经 A 两跳推导晚一辈
    assert generations[world["f"].id] is None  # peer 边不参与世代推导
    assert relations[world["a"].id] == "老伴"  # 创建者原文保留（D3）
    assert relations[world["f"].id] == "表哥"
    assert relations[world["m"].id] is None  # 无直连边不编造称谓


# ---- 关系路径与解释 ----


def test_relationship_path_direct_multi_hop_and_invisible(db_session):
    world = _world(db_session)
    session_row = _b_context(db_session, world)
    run = _assistant_run(db_session, session_row, ALL_SIX_TOOLS)

    direct = _call(
        db_session,
        run,
        session_row,
        agent_query.TOOL_GET_RELATIONSHIP_PATH,
        {"to_user_id": world["a"].id},
    )
    assert direct["path_class"] == "direct"
    assert len(direct["path"]) == 1

    multi = _call(
        db_session,
        run,
        session_row,
        agent_query.TOOL_GET_RELATIONSHIP_PATH,
        {"to_user_id": world["m"].id, "from_user_id": world["b"].id},
    )
    assert multi["found"] is True and multi["path_class"] == "multi_hop"
    assert multi["path"][1]["label"] == "孙女"

    same = _call(
        db_session,
        run,
        session_row,
        agent_query.TOOL_GET_RELATIONSHIP_PATH,
        {"to_user_id": world["b"].id},
    )
    assert same["found"] is False and same["path"] == []

    # 跨空间不可达：B→G 需途经 E，但 E 对 B 不可见 → 剪枝后找不到，且不泄露存在性
    hidden = _call(
        db_session,
        run,
        session_row,
        agent_query.TOOL_GET_RELATIONSHIP_PATH,
        {"to_user_id": world["g"].id},
    )
    assert hidden == {"found": False, "path_class": "none", "path": [], "evidence_relation_ids": []}
    serialized = json.dumps(hidden, ensure_ascii=False)
    assert E_NAME not in serialized and G_NAME not in serialized
    assert str(world["e"].id) not in serialized and str(world["g"].id) not in serialized


def test_explain_structural_path_deterministic(db_session):
    world = _world(db_session)
    session_row = _b_context(db_session, world)
    run = _assistant_run(db_session, session_row, ALL_SIX_TOOLS)

    structural = _call(
        db_session,
        run,
        session_row,
        agent_query.TOOL_EXPLAIN_STRUCTURAL_PATH,
        {"to_user_id": world["m"].id, "from_user_id": world["b"].id},
    )
    assert structural["caveats"] == []  # 全部为确定结构跳且均已确档
    assert "配偶（同辈）" in structural["explanation"]
    assert "晚辈（晚一辈）" in structural["explanation"]
    assert f"#{world['spouse'].id}" in structural["explanation"]

    peer_hop = _call(
        db_session,
        run,
        session_row,
        agent_query.TOOL_EXPLAIN_STRUCTURAL_PATH,
        {"to_user_id": world["f"].id, "from_user_id": world["b"].id},
    )
    assert peer_hop["path_class"] == "direct"
    assert any("非确定结构关系" in caveat for caveat in peer_hop["caveats"])

    missing = _call(
        db_session,
        run,
        session_row,
        agent_query.TOOL_EXPLAIN_STRUCTURAL_PATH,
        {"to_user_id": world["g"].id},
    )
    assert missing["path_class"] == "none"
    assert any("资料不足" in caveat for caveat in missing["caveats"])
    assert G_NAME not in missing["explanation"]


# ---- minor 保护 ----


def test_minor_sensitive_fields_never_leak(db_session):
    world = _world(db_session)
    session_row = _b_context(db_session, world)
    run = _assistant_run(db_session, session_row, ALL_SIX_TOOLS)

    outputs = [
        _call(db_session, run, session_row, agent_query.TOOL_LIST_VISIBLE_PEOPLE),
        _call(
            db_session,
            run,
            session_row,
            agent_query.TOOL_GET_PROFILE_SUMMARY,
            {"user_id": world["m"].id},
        ),
        _call(db_session, run, session_row, agent_query.TOOL_SEARCH_SPACE, {"query": M_NAME}),
        _call(
            db_session,
            run,
            session_row,
            agent_query.TOOL_GET_RELATIONSHIP_PATH,
            {"to_user_id": world["m"].id},
        ),
        _call(
            db_session,
            run,
            session_row,
            agent_query.TOOL_EXPLAIN_STRUCTURAL_PATH,
            {"to_user_id": world["m"].id},
        ),
    ]
    for output in outputs:
        serialized = json.dumps(output, ensure_ascii=False)
        assert M_BIRTH_DATE not in serialized
        assert M_BIO not in serialized
    summary = outputs[1]
    assert summary["birth"] == visibility.MASKED
    assert summary["bio"] == visibility.MASKED
    assert summary["name"] == M_NAME  # 基线字段（名字）可见


# ---- 上限与分页 ----


def test_pagination_walk_and_limit_ranges(db_session):
    world = _world(db_session)
    session_row = _b_context(db_session, world)
    run = _assistant_run(db_session, session_row, ALL_SIX_TOOLS)

    extra = [create_user_with_pin(db_session, f"批量成员{i:02d}", "000000") for i in range(25)]
    for user in extra:
        create_space_member(db_session, world["household"].id, user.id)

    collected: list[int] = []
    cursor = 0
    while True:
        page = _call(
            db_session,
            run,
            session_row,
            agent_query.TOOL_LIST_VISIBLE_PEOPLE,
            {"limit": 10, "cursor": cursor},
        )
        collected.extend(person["user_id"] for person in page["people"])
        if page["next_cursor"] is None:
            break
        assert page["next_cursor"] == cursor + 10
        cursor = page["next_cursor"]
    expected_ids = {user.id for user in extra} | {
        world[key].id for key in ("a", "b", "d", "m", "f", "p")
    }
    assert len(collected) == 31
    assert set(collected) == expected_ids
    assert collected == sorted(collected)  # offset 分页稳定序

    exhausted = _call(
        db_session, run, session_row, agent_query.TOOL_LIST_VISIBLE_PEOPLE, {"cursor": 9999}
    )
    assert exhausted == {"people": [], "next_cursor": None}

    # limit/cursor 越界 → 协议拒绝并写审计（QueryToolError 经 _dispatch 转译）
    for payload in ({"limit": 51}, {"limit": 0}, {"cursor": -1}, {"limit": True}):
        with pytest.raises(HTTPException) as exc_info:
            _call(db_session, run, session_row, agent_query.TOOL_LIST_VISIBLE_PEOPLE, payload)
        assert _error_code(exc_info) == "AGENT_TOOL_SCHEMA_INVALID"
    denials = db_session.query(AuditLog).filter_by(action="agent_tool_denied").all()
    assert len(denials) >= 4
    with pytest.raises(HTTPException) as exc_info:
        _call(
            db_session,
            run,
            session_row,
            agent_query.TOOL_SEARCH_SPACE,
            {"query": "张", "limit": 21},
        )
    assert _error_code(exc_info) == "AGENT_TOOL_SCHEMA_INVALID"


def test_output_truncation_cap():
    """8KB 截断机制：超限列表自尾部裁剪并置 truncated=true。"""
    big_entry = {"text": "字" * 512}
    output: dict = {"people": [dict(big_entry) for _ in range(64)], "next_cursor": None}
    trimmed = enforce_output_limit(output)
    size = len(json.dumps(trimmed, ensure_ascii=False).encode("utf-8"))
    assert size <= agent_query.OUTPUT_MAX_BYTES
    assert trimmed.get("truncated") is True
    small = {"hits": [{"a": 1}]}
    assert enforce_output_limit(small) == small  # 未超限原样返回


# ---- 零写入 ----


def test_zero_write_business_tables(db_session):
    world = _world(db_session)
    session_row = _b_context(db_session, world)
    run = _assistant_run(db_session, session_row, ALL_SIX_TOOLS)

    business_tables = (
        "users",
        "accounts",
        "relations",
        "family_spaces",
        "space_members",
        "space_profile_refs",
        "disclosure_preferences",
        "node_positions",
        "attachments",
        "agent_sessions",
        "agent_runs",
        "agent_jobs",
        "agent_run_events",
        "agent_messages",
        "domain_events",
        "profile_fact_reviews",
        "data_right_requests",
        "claim_disputes",
        "ownership_transfers",
        "owner_invitations",
        "platform_role_assignments",
        "refresh_sessions",
        "auth_challenges",
    )

    def snapshot() -> dict[str, int]:
        return {
            table: db_session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in business_tables
        }

    before = snapshot()
    calls: list[tuple[str, dict]] = [
        (agent_query.TOOL_GET_SELF_CONTEXT, {}),
        (agent_query.TOOL_LIST_VISIBLE_PEOPLE, {}),
        (agent_query.TOOL_GET_PROFILE_SUMMARY, {"user_id": world["a"].id}),
        (agent_query.TOOL_SEARCH_SPACE, {"query": "张"}),
        (agent_query.TOOL_GET_RELATIONSHIP_PATH, {"to_user_id": world["m"].id}),
        (agent_query.TOOL_EXPLAIN_STRUCTURAL_PATH, {"to_user_id": world["m"].id}),
    ]
    for name, payload in calls:
        _call(db_session, run, session_row, name, payload)
    # 结果类错误（档案不可见）同样不得产生任何业务写入
    with pytest.raises(HTTPException):
        _call(
            db_session,
            run,
            session_row,
            agent_query.TOOL_GET_PROFILE_SUMMARY,
            {"user_id": world["g"].id},
        )
    db_session.commit()  # 等价 API 层成功审计提交
    assert snapshot() == before


# ---- 注册表 / 门禁集成 ----


def test_registry_min_kind_gating(db_session):
    """新工具 min_kind=assistant：默认白名单自动纳入；steward kind 被拒。"""
    world = _world(db_session)
    assistant_default = agent_tools.default_allowlist("assistant")
    for name in ALL_SIX_TOOLS:
        assert name in assistant_default
    assert agent_query.TOOL_GET_SELF_CONTEXT not in agent_tools.default_allowlist("steward")

    steward_session = create_agent_session(
        db_session,
        account_id=world["a"].account.id,
        space_id=world["household"].id,
        kind="steward",
    )
    agent_queue.enqueue_run(
        db_session,
        agent_session=steward_session,
        kind="steward",
        policy_version="p1",
        tool_allowlist=[agent_query.TOOL_GET_SELF_CONTEXT],
    )
    grant = agent_queue.lease_next(db_session, kind="steward", leased_by="sc")
    assert grant is not None
    agent_events.append_events(
        db_session,
        grant.run,
        [agent_events.EventEntry(seq=0, type="run.started", public_payload={})],
    )
    with pytest.raises(HTTPException) as exc_info:
        agent_tools.execute(
            db_session,
            grant.run,
            steward_session,
            {"agent_kind": "steward"},
            name=agent_query.TOOL_GET_SELF_CONTEXT,
            version=1,
            input_payload={},
        )
    assert _error_code(exc_info) == "AGENT_TOOL_SCOPE_DENIED"

    # assistant 白名单未含新工具时拒绝（not_in_allowlist 路径）
    other_user, other_space = create_agent_fixture(db_session, name="allowlist-probe")
    session_row = create_agent_session(
        db_session, account_id=other_user.account.id, space_id=other_space.id
    )
    limited_run = _assistant_run(db_session, session_row, ["familygraph.echo"])
    with pytest.raises(HTTPException) as exc_info:
        _call(db_session, limited_run, session_row, agent_query.TOOL_GET_SELF_CONTEXT)
    assert _error_code(exc_info) == "AGENT_TOOL_SCOPE_DENIED"
