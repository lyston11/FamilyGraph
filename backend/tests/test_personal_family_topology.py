"""PFV confirmed 结构拓扑（topology_edges）与桥接时钟到期合同测试。

覆盖 09-13 family-tree-relationship-topology：
- 共同父母兄妹同代、配偶/子女/孙辈逐代连线（AC1/AC2）；
- 主/备选路径覆盖之外的直接事实仍进入拓扑（AC3）；
- 全局/空间重复与反向对称事实去重、稳定 id（AC3）；
- 父母未知 direct_sibling、多父母、再婚、孤立节点（AC5）；
- proposed/disputed/revoked、隐藏端点、跨空间事实不进拓扑（AC6）；
- bridge 仅时钟跨过 expires_at 即失效（无状态写入、无事件），
  首次 GET 安全空态、重算后移除；独立授权保留（AC6）；
- 最终载荷 ETag：相同数据 304，事实变化后旧 ETag 不命中（AC6）。
"""

from __future__ import annotations

import pytest
from conftest import (
    auth_header,
    create_agent_fixture,
    create_space_member,
    create_user_with_pin,
    login,
)
from sqlalchemy import select

from app import config
from app.models.personal_family_view import PersonalFamilyBridge, PersonalFamilyView
from app.models.space import FamilySpace
from app.services import personal_family_bridge, personal_family_view
from app.services import source_facts as sf
from app.utils.timeutil import utcnow

# ---- 公共辅助（与 consistency 契约测试同款） ----


def _make_space(session, owner, name: str, *, kind: str = "household") -> FamilySpace:
    space = FamilySpace(name=name, kind=kind, owner_id=owner.id, created_at=utcnow())
    session.add(space)
    session.flush()
    create_space_member(session, space.id, owner.id)
    return space


def _confirm(session, fact_type: str, subject_id: int, object_id: int, space_id: int | None):
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


def _materialize(session, account, space_id) -> PersonalFamilyView:
    view = personal_family_view.rebuild_view(session, account=account, space_id=space_id)
    session.commit()
    return view


def _payload(session, account, space_id) -> dict:
    return personal_family_view.view_payload(session, account=account, space_id=space_id)


def _topo_set(payload: dict) -> set[tuple[str, str | None, int, int]]:
    return {
        (edge["edge_kind"], edge["subtype"], edge["from_user_id"], edge["to_user_id"])
        for edge in payload["topology_edges"]
    }


def _saved_fact_ids(payload: dict) -> set[int]:
    ids: set[int] = set()
    for edge in payload["edges"]:
        for path in [edge["path"], *edge["alternative_paths"]]:
            for step in path:
                if isinstance(step.get("fact_id"), int) and step["fact_id"] > 0:
                    ids.add(step["fact_id"])
    return ids


# ---- AC1/AC2：世代与家庭结构 ----


def test_topology_common_parents_siblings_same_generation(db_session) -> None:
    """共同父母下兄妹各自连接父母；个人摘要仍在，但不产生兄妹结构边。"""
    viewer, space = create_agent_fixture(db_session, name="topo-sib")
    sister = create_user_with_pin(db_session, "topo-sis", "123456", gender="f")
    father = create_user_with_pin(db_session, "topo-dad", "123456", gender="m")
    mother = create_user_with_pin(db_session, "topo-mom", "123456", gender="f")
    for user in (sister, father, mother):
        create_space_member(db_session, space.id, user.id)
    _confirm(db_session, "biological_parent", father.id, viewer.id, space.id)
    _confirm(db_session, "biological_parent", father.id, sister.id, space.id)
    _confirm(db_session, "biological_parent", mother.id, viewer.id, space.id)
    _confirm(db_session, "biological_parent", mother.id, sister.id, space.id)
    db_session.commit()
    _materialize(db_session, viewer.account, space.id)

    payload = _payload(db_session, viewer.account, space.id)
    assert payload["status"] == "current"
    assert _topo_set(payload) == {
        ("parent", "biological", father.id, viewer.id),
        ("parent", "biological", father.id, sister.id),
        ("parent", "biological", mother.id, viewer.id),
        ("parent", "biological", mother.id, sister.id),
    }
    # 稳定 id 由规范化四元组生成，与 fact.id 无关
    assert {edge["id"] for edge in payload["topology_edges"]} == {
        f"parent:biological:{father.id}:{viewer.id}",
        f"parent:biological:{father.id}:{sister.id}",
        f"parent:biological:{mother.id}:{viewer.id}",
        f"parent:biological:{mother.id}:{sister.id}",
    }
    # 个人摘要边不受影响：本人→妹妹的称谓路径仍存在
    summary_pairs = {(edge["from_user_id"], edge["to_user_id"]) for edge in payload["edges"]}
    assert (viewer.id, sister.id) in summary_pairs
    assert (viewer.id, father.id) in summary_pairs


def test_topology_spouse_children_grandchildren_multi_origin(db_session) -> None:
    """夫妻横连、子女连双方父母、孙辈只连实际父母；存在非本人起点的边。"""
    viewer, space = create_agent_fixture(db_session, name="topo-gen")
    wife = create_user_with_pin(db_session, "topo-wife", "123456", gender="f")
    child = create_user_with_pin(db_session, "topo-child", "123456", gender="f")
    child_husband = create_user_with_pin(db_session, "topo-chusb", "123456", gender="m")
    grandchild = create_user_with_pin(db_session, "topo-grand", "123456", gender="m")
    for user in (wife, child, child_husband, grandchild):
        create_space_member(db_session, space.id, user.id)
    _confirm(db_session, "spouse", viewer.id, wife.id, space.id)
    _confirm(db_session, "biological_parent", viewer.id, child.id, space.id)
    _confirm(db_session, "biological_parent", wife.id, child.id, space.id)
    _confirm(db_session, "spouse", child.id, child_husband.id, space.id)
    _confirm(db_session, "biological_parent", child.id, grandchild.id, space.id)
    _confirm(db_session, "biological_parent", child_husband.id, grandchild.id, space.id)
    db_session.commit()
    _materialize(db_session, viewer.account, space.id)

    payload = _payload(db_session, viewer.account, space.id)
    assert _topo_set(payload) == {
        ("spouse", None, min(viewer.id, wife.id), max(viewer.id, wife.id)),
        ("parent", "biological", viewer.id, child.id),
        ("parent", "biological", wife.id, child.id),
        ("spouse", None, min(child.id, child_husband.id), max(child.id, child_husband.id)),
        ("parent", "biological", child.id, grandchild.id),
        ("parent", "biological", child_husband.id, grandchild.id),
    }
    # 存在非本人起点的结构边；本人不跨代直连孙辈
    origins = {edge["from_user_id"] for edge in payload["topology_edges"]}
    assert origins != {viewer.id}
    assert all(
        (edge["from_user_id"], edge["to_user_id"]) != (viewer.id, grandchild.id)
        for edge in payload["topology_edges"]
    )


# ---- AC3：路径覆盖之外的事实与去重 ----


def test_topology_includes_facts_outside_saved_paths(db_session) -> None:
    """备选路径覆盖之外的直接事实仍进入拓扑（先证明其不在保存路径中）。

    刻意构造的合成图（不是现实亲属语义）：viewer 的手足 X、Y 互为 partner；
    X 另有 3 名同样可见的 spouse（W1..W3），Y 另有 3 名 spouse（Z1..Z3），
    均为 viewer 手足。到 X/Y 的 2 步路线各有 4 条同形候选（3 条 spouse +
    1 条经 partner），按 (边数, 非血缘步数, 姻亲步数, 节点 id 序列) 排序且
    W/Z 的 id 小于 X/Y，partner 路线排在第 4 位被 ALT_PATH_LIMIT=3 截断；
    partner 边不可延伸，更长绕路不存在。据此 X—Y partner 事实不出现在
    任何保存 path/alternative_paths 中，但 topology 必须包含它。
    """
    viewer, space = create_agent_fixture(db_session, name="topo-alt")
    ws = [
        create_user_with_pin(db_session, f"topo-alt-w{i}", "123456", gender="f") for i in (1, 2, 3)
    ]
    zs = [
        create_user_with_pin(db_session, f"topo-alt-z{i}", "123456", gender="m") for i in (1, 2, 3)
    ]
    x = create_user_with_pin(db_session, "topo-alt-x", "123456", gender="m")
    y = create_user_with_pin(db_session, "topo-alt-y", "123456", gender="f")
    assert ws[0].id < zs[0].id < x.id < y.id
    for user in (*ws, *zs, x, y):
        create_space_member(db_session, space.id, user.id)
    for sibling in (*ws, *zs, x, y):
        _confirm(db_session, "direct_sibling", viewer.id, sibling.id, space.id)
    for w in ws:
        _confirm(db_session, "spouse", w.id, x.id, space.id)
    for z in zs:
        _confirm(db_session, "spouse", z.id, y.id, space.id)
    partner_fact = _confirm(db_session, "partner", x.id, y.id, space.id)
    db_session.commit()
    _materialize(db_session, viewer.account, space.id)

    payload = _payload(db_session, viewer.account, space.id)
    assert payload["status"] == "current"
    # 前提：该 partner 事实确实不在任何保存 path/alternative_paths 中
    assert partner_fact.id not in _saved_fact_ids(payload)
    # topology 不依赖保存路径：该事实的结构边仍然完整出现
    lo, hi = sorted((x.id, y.id))
    assert {
        ("partner", None, lo, hi),
    } <= _topo_set(payload)
    assert f"partner:-:{lo}:{hi}" in {edge["id"] for edge in payload["topology_edges"]}
    # 路径覆盖之内的事实（如 W—X spouse）同样在 topology 中，无重复
    for w in ws:
        assert ("spouse", None, min(w.id, x.id), max(w.id, x.id)) in _topo_set(payload)
    ids = [edge["id"] for edge in payload["topology_edges"]]
    assert len(ids) == len(set(ids))


def test_topology_dedupes_scope_and_reverse_duplicates_stable_id(db_session) -> None:
    """全局/空间重复事实与反向申报对称事实合并；撤销其一 id 不变。"""
    viewer, space = create_agent_fixture(db_session, name="topo-dup")
    partner = create_user_with_pin(db_session, "topo-dup-p", "123456", gender="f")
    create_space_member(db_session, space.id, partner.id)
    in_space = _confirm(db_session, "spouse", viewer.id, partner.id, space.id)
    global_reverse = _confirm(db_session, "spouse", partner.id, viewer.id, None)
    db_session.commit()
    _materialize(db_session, viewer.account, space.id)

    payload = _payload(db_session, viewer.account, space.id)
    spouse_edges = [e for e in payload["topology_edges"] if e["edge_kind"] == "spouse"]
    assert len(spouse_edges) == 1
    stable_id = spouse_edges[0]["id"]
    lo, hi = sorted((viewer.id, partner.id))
    assert stable_id == f"spouse:-:{lo}:{hi}"
    # 输出按 key 稳定排序，与遍历顺序无关
    ids = [edge["id"] for edge in payload["topology_edges"]]
    assert ids == sorted(ids)

    sf.transition_source_fact(db_session, in_space, "revoke")
    db_session.commit()
    _materialize(db_session, viewer.account, space.id)
    payload = _payload(db_session, viewer.account, space.id)
    spouse_edges = [e for e in payload["topology_edges"] if e["edge_kind"] == "spouse"]
    assert len(spouse_edges) == 1
    assert spouse_edges[0]["id"] == stable_id

    sf.transition_source_fact(db_session, global_reverse, "revoke")
    db_session.commit()
    _materialize(db_session, viewer.account, space.id)
    payload = _payload(db_session, viewer.account, space.id)
    assert not [e for e in payload["topology_edges"] if e["edge_kind"] == "spouse"]


# ---- AC5：父母未知 / 多父母 / 再婚 / 孤立节点 ----


def test_topology_direct_sibling_without_parents_invents_nothing(db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="topo-dsib")
    sister = create_user_with_pin(db_session, "topo-dsib-s", "123456", gender="f")
    create_space_member(db_session, space.id, sister.id)
    _confirm(db_session, "direct_sibling", viewer.id, sister.id, space.id)
    db_session.commit()
    _materialize(db_session, viewer.account, space.id)

    payload = _payload(db_session, viewer.account, space.id)
    assert _topo_set(payload) == {
        ("sibling", None, min(viewer.id, sister.id), max(viewer.id, sister.id)),
    }


def test_topology_multi_parent_remarriage_no_dangling_edges(db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="topo-multi")
    bio_father = create_user_with_pin(db_session, "topo-multi-bf", "123456", gender="m")
    adopt_mother = create_user_with_pin(db_session, "topo-multi-am", "123456", gender="f")
    step_parent = create_user_with_pin(db_session, "topo-multi-sp", "123456", gender="m")
    ex_wife = create_user_with_pin(db_session, "topo-multi-w1", "123456", gender="f")
    new_wife = create_user_with_pin(db_session, "topo-multi-w2", "123456", gender="f")
    isolated = create_user_with_pin(db_session, "topo-multi-iso", "123456", gender="f")
    for user in (bio_father, adopt_mother, step_parent, ex_wife, new_wife, isolated):
        create_space_member(db_session, space.id, user.id)
    _confirm(db_session, "biological_parent", bio_father.id, viewer.id, space.id)
    _confirm(db_session, "adoptive_parent", adopt_mother.id, viewer.id, space.id)
    _confirm(db_session, "step_parent", step_parent.id, viewer.id, space.id)
    _confirm(db_session, "spouse", bio_father.id, ex_wife.id, space.id)
    _confirm(db_session, "spouse", bio_father.id, new_wife.id, space.id)
    db_session.commit()
    _materialize(db_session, viewer.account, space.id)

    payload = _payload(db_session, viewer.account, space.id)
    assert _topo_set(payload) == {
        ("parent", "biological", bio_father.id, viewer.id),
        ("parent", "adoptive", adopt_mother.id, viewer.id),
        ("parent", "step", step_parent.id, viewer.id),
        ("spouse", None, min(bio_father.id, ex_wife.id), max(bio_father.id, ex_wife.id)),
        ("spouse", None, min(bio_father.id, new_wife.id), max(bio_father.id, new_wife.id)),
    }
    # 每人只有一个人物节点；与 viewer 无已确认路径的孤立成员不进入 PFV
    # （既有 confirmed-reachable 合同），更不会产生结构边。
    node_ids = [node["user_id"] for node in payload["nodes"]]
    assert len(node_ids) == len(set(node_ids))
    assert isolated.id not in node_ids
    # 无悬空边：结构边端点必然都在本次授权节点集合内
    assert all(
        edge["from_user_id"] in node_ids and edge["to_user_id"] in node_ids
        for edge in payload["topology_edges"]
    )


# ---- AC6：状态 / 隐藏 / 跨空间隔离 ----


def test_topology_excludes_non_confirmed_hidden_and_cross_space(db_session) -> None:
    viewer, space = create_agent_fixture(db_session, name="topo-guard")
    partner = create_user_with_pin(db_session, "topo-guard-p", "123456", gender="f")
    candidate = create_user_with_pin(db_session, "topo-guard-c", "123456", gender="m")
    outsider = create_user_with_pin(db_session, "topo-guard-x", "123456", gender="m")
    for user in (partner, candidate):
        create_space_member(db_session, space.id, user.id)
    confirmed = _confirm(db_session, "spouse", viewer.id, partner.id, space.id)

    proposed = sf.create_source_fact(
        db_session,
        fact_type="biological_parent",
        subject_user_id=candidate.id,
        object_user_id=viewer.id,
        provenance="agent_proposal",
        space_id=space.id,
    )
    # disputed 只能从 proposed 进入（confirmed 不可 dispute）
    disputed = sf.create_source_fact(
        db_session,
        fact_type="direct_sibling",
        subject_user_id=viewer.id,
        object_user_id=candidate.id,
        provenance="manual_entry",
        space_id=space.id,
    )
    sf.transition_source_fact(db_session, disputed, "dispute")
    revoked = _confirm(db_session, "spouse", partner.id, candidate.id, space.id)
    sf.transition_source_fact(db_session, revoked, "revoke")
    # 两端可见但事实属于另一空间：不进入当前空间拓扑
    other_space = _make_space(db_session, outsider, "topo-guard-other", kind="lineage")
    _confirm(db_session, "biological_parent", outsider.id, viewer.id, other_space.id)
    # 全局事实但端点不可见（outsider 不在本空间）：不出现悬空边
    _confirm(db_session, "biological_parent", outsider.id, partner.id, None)
    db_session.commit()
    assert proposed.state == "proposed"
    _materialize(db_session, viewer.account, space.id)

    payload = _payload(db_session, viewer.account, space.id)
    assert _topo_set(payload) == {
        ("spouse", None, min(viewer.id, partner.id), max(viewer.id, partner.id)),
    }
    node_ids = {node["user_id"] for node in payload["nodes"]}
    assert outsider.id not in node_ids
    assert candidate.id not in {e["from_user_id"] for e in payload["topology_edges"]}

    sf.transition_source_fact(db_session, confirmed, "revoke")
    db_session.commit()
    _materialize(db_session, viewer.account, space.id)
    payload = _payload(db_session, viewer.account, space.id)
    assert payload["topology_edges"] == []


# ---- AC6：bridge 仅时钟到期 ----


@pytest.fixture()
def _pfv_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)


def _client_get(client, headers, space_id, etag=None):
    return client.get(
        "/api/personal-family-view",
        params={"space_id": space_id},
        headers={**headers, **({"If-None-Match": etag} if etag else {})},
    )


def _bridge_left_right(db_session, *, expires_at):
    left = create_user_with_pin(db_session, "topo-br-left", "123456", gender="m")
    right = create_user_with_pin(db_session, "topo-br-right", "123456", gender="f")
    member = create_user_with_pin(db_session, "topo-br-mem", "123456", gender="f")
    left_space = _make_space(db_session, left, "topo-br-left-space", kind="lineage")
    right_space = _make_space(db_session, right, "topo-br-right-space", kind="lineage")
    create_space_member(db_session, right_space.id, member.id)
    _confirm(db_session, "spouse", left.id, right.id, left_space.id)
    _confirm(db_session, "spouse", right.id, member.id, right_space.id)
    bridge = personal_family_bridge.create_bridge(
        db_session,
        account=left.account,
        anchor_user_id=left.id,
        other_space_id=right_space.id,
        other_anchor_user_id=right.id,
        scope={"mode": "anchor_paths"},
        expires_at=expires_at,
    )
    personal_family_bridge.consent_bridge(
        db_session, bridge_id=bridge.id, account=right.account, revision=bridge.revision
    )
    db_session.commit()
    return left, right, member, left_space, right_space, bridge


def test_bridge_clock_expiry_needs_no_status_write_or_event(
    db_session, client, _pfv_enabled
) -> None:
    left, right, _member, left_space, _right_space, bridge = _bridge_left_right(
        db_session, expires_at=None
    )
    _materialize(db_session, left.account, left_space.id)

    headers = auth_header(login(client, "topo-br-left", "123456").json())
    first = _client_get(client, headers, left_space.id)
    assert first.status_code == 200
    assert right.id in {node["user_id"] for node in first.json()["nodes"]}
    assert any(
        edge["edge_kind"] == "spouse" and right.id in (edge["from_user_id"], edge["to_user_id"])
        for edge in first.json()["topology_edges"]
    )
    etag = first.headers["ETag"]
    assert _client_get(client, headers, left_space.id, etag).status_code == 304

    # 仅时钟跨过 expires_at：不改 status、不写 revision、不发事件。
    bridge.expires_at = utcnow()
    db_session.commit()
    status_before = bridge.status
    revision_before = bridge.revision

    response = _client_get(client, headers, left_space.id, etag)
    assert response.status_code == 200
    assert response.json()["status"] == "stale"
    assert response.json()["nodes"] == []
    assert response.json()["topology_edges"] == []

    # 行状态仍为 current（仅指纹漂移），直接走单视图重算命令
    personal_family_view.rebuild_view(db_session, account=left.account, space_id=left_space.id)
    db_session.commit()
    after = _client_get(client, headers, left_space.id)
    assert after.status_code == 200
    assert after.json()["status"] == "current"
    assert right.id not in {node["user_id"] for node in after.json()["nodes"]}
    assert after.json()["topology_edges"] == []
    # GET 与重算全程只读：bridge 状态与修订未被改动
    row = db_session.get(PersonalFamilyBridge, bridge.id)
    assert row.status == status_before == "active"
    assert row.revision == revision_before
    # 旧 ETag 在新载荷下不能再 304
    assert _client_get(client, headers, left_space.id, etag).status_code == 200


def test_bridge_expiry_keeps_independently_authorized_nodes(
    db_session, client, _pfv_enabled
) -> None:
    """仅依赖到期 bridge 的端点消失；独立授权（自身成员资格+事实）保留。"""
    left = create_user_with_pin(db_session, "topo-ind-left", "123456", gender="m")
    right = create_user_with_pin(db_session, "topo-ind-right", "123456", gender="f")
    member = create_user_with_pin(db_session, "topo-ind-mem", "123456", gender="f")
    left_space = _make_space(db_session, left, "topo-ind-left-space", kind="lineage")
    right_space = _make_space(db_session, right, "topo-ind-right-space", kind="lineage")
    create_space_member(db_session, right_space.id, member.id)
    # right 同时是 left_space 的直接成员（独立授权）与 bridge 对方锚点
    create_space_member(db_session, left_space.id, right.id)
    _confirm(db_session, "biological_parent", right.id, left.id, left_space.id)
    _confirm(db_session, "spouse", right.id, member.id, right_space.id)
    bridge = personal_family_bridge.create_bridge(
        db_session,
        account=left.account,
        anchor_user_id=left.id,
        other_space_id=right_space.id,
        other_anchor_user_id=right.id,
        scope={"mode": "anchor_paths"},
    )
    personal_family_bridge.consent_bridge(
        db_session, bridge_id=bridge.id, account=right.account, revision=bridge.revision
    )
    _materialize(db_session, left.account, left_space.id)

    headers = auth_header(login(client, "topo-ind-left", "123456").json())
    first = _client_get(client, headers, left_space.id)
    assert first.status_code == 200
    node_ids = {node["user_id"] for node in first.json()["nodes"]}
    assert {left.id, right.id, member.id} <= node_ids

    bridge.expires_at = utcnow()
    db_session.commit()
    stale = _client_get(client, headers, left_space.id)
    assert stale.status_code == 200
    assert stale.json()["status"] == "stale"
    assert stale.json()["nodes"] == []

    personal_family_view.rebuild_view(db_session, account=left.account, space_id=left_space.id)
    db_session.commit()
    after = _client_get(client, headers, left_space.id)
    node_ids = {node["user_id"] for node in after.json()["nodes"]}
    assert left.id in node_ids and right.id in node_ids
    assert member.id not in node_ids
    # 独立授权的亲子结构边保留
    assert ("parent", "biological", right.id, left.id) in _topo_set(after.json())


# ---- AC6：最终载荷 ETag ----


def test_api_topology_304_roundtrip_and_fact_change(db_session, client, _pfv_enabled) -> None:
    viewer, space = create_agent_fixture(db_session, name="topo-etag")
    sister = create_user_with_pin(db_session, "topo-etag-s", "123456", gender="f")
    create_space_member(db_session, space.id, sister.id)
    fact = _confirm(db_session, "direct_sibling", viewer.id, sister.id, space.id)
    db_session.commit()
    _materialize(db_session, viewer.account, space.id)

    headers = auth_header(login(client, "topo-etag", "123456").json())
    first = _client_get(client, headers, space.id)
    assert first.status_code == 200
    body = first.json()
    assert body["topology_edges"] == [
        {
            "id": f"sibling:-:{min(viewer.id, sister.id)}:{max(viewer.id, sister.id)}",
            "from_user_id": min(viewer.id, sister.id),
            "to_user_id": max(viewer.id, sister.id),
            "edge_kind": "sibling",
            "subtype": None,
        }
    ]
    etag = first.headers["ETag"]
    assert _client_get(client, headers, space.id, etag).status_code == 304

    # 事实撤回 → 最终载荷变化，旧 ETag 绝不 304
    sf.transition_source_fact(db_session, fact, "revoke")
    db_session.commit()
    response = _client_get(client, headers, space.id, etag)
    assert response.status_code == 200
    assert response.json()["status"] == "stale"
    assert response.json()["topology_edges"] == []

    personal_family_view.rebuild_space_views(db_session, space_id=space.id)
    db_session.commit()
    after = _client_get(client, headers, space.id, etag)
    assert after.status_code == 200
    assert after.json()["status"] == "current"
    assert after.json()["topology_edges"] == []


def test_api_safe_empty_states_include_topology_edges(db_session, client, _pfv_enabled) -> None:
    viewer, space = create_agent_fixture(db_session, name="topo-empty")
    # 无投影行：never_computed 安全空态显式携带 topology_edges=[]
    headers = auth_header(login(client, "topo-empty", "123456").json())
    first = _client_get(client, headers, space.id)
    assert first.status_code == 200
    assert first.json()["topology_edges"] == []

    _materialize(db_session, viewer.account, space.id)
    view = db_session.scalar(
        select(PersonalFamilyView).where(
            PersonalFamilyView.viewer_account_id == viewer.account.id,
            PersonalFamilyView.space_id == space.id,
        )
    )
    view.status = "failed"
    view.failed_reason = "boom"
    db_session.commit()
    response = _client_get(client, headers, space.id)
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["topology_edges"] == []
