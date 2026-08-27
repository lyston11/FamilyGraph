"""v1 结构边 → confirmed SourceFact 的生产映射（E1），修复两套事实源断链。

验证：connection accept 结构边（elder/younger/spouse）同事务写 confirmed
SourceFact（全局 scope），peer 不映射；revoke 同步失效；映射幂等。
"""

from __future__ import annotations

import pytest
from conftest import create_user_with_pin
from sqlalchemy import select

from app.models.relationship_facts import SourceFact
from app.services import source_facts
from app.services.source_facts import (
    FACT_CONFIRMED,
    FACT_PROPOSED,
    FACT_REVOKED,
    map_structural_edge_to_fact,
    revoke_structural_edge_fact,
)


@pytest.fixture()
def two_users(db_session):
    a = create_user_with_pin(db_session, "映射甲", "111111")
    b = create_user_with_pin(db_session, "映射乙", "222222")
    db_session.commit()
    return a, b


def _confirmed(db_session, fact_type: str, subject: int, obj: int) -> SourceFact | None:
    return db_session.scalar(
        select(SourceFact).where(
            SourceFact.fact_type == fact_type,
            SourceFact.subject_user_id == subject,
            SourceFact.object_user_id == obj,
            SourceFact.space_id.is_(None),
        )
    )


def test_elder_maps_to_biological_parent_direction(db_session, two_users):
    a, b = two_users
    # v1 elder f→t：to_user(b) 是 from_user(a) 的长辈 → biological_parent(b, a)
    fact = map_structural_edge_to_fact(
        db_session, from_user=a.id, to_user=b.id, dir_class="elder"
    )
    db_session.commit()
    assert fact is not None
    assert fact.fact_type == "biological_parent"
    assert fact.subject_user_id == b.id
    assert fact.object_user_id == a.id
    assert fact.state == FACT_CONFIRMED
    assert fact.provenance == "connection_accept"
    assert fact.space_id is None


def test_younger_maps_to_biological_parent_direction(db_session, two_users):
    a, b = two_users
    # v1 younger f→t：from_user(a) 是 to_user(b) 的长辈 → biological_parent(a, b)
    fact = map_structural_edge_to_fact(
        db_session, from_user=a.id, to_user=b.id, dir_class="younger"
    )
    db_session.commit()
    assert fact.fact_type == "biological_parent"
    assert fact.subject_user_id == a.id
    assert fact.object_user_id == b.id
    assert fact.state == FACT_CONFIRMED


def test_spouse_maps_to_spouse_symmetric(db_session, two_users):
    a, b = two_users
    fact = map_structural_edge_to_fact(
        db_session, from_user=a.id, to_user=b.id, dir_class="spouse"
    )
    db_session.commit()
    assert fact.fact_type == "spouse"
    assert fact.subject_user_id == a.id
    assert fact.object_user_id == b.id
    assert fact.state == FACT_CONFIRMED


def test_peer_does_not_map(db_session, two_users):
    a, b = two_users
    fact = map_structural_edge_to_fact(
        db_session, from_user=a.id, to_user=b.id, dir_class="peer"
    )
    assert fact is None
    assert db_session.scalars(select(SourceFact)).all() == []


def test_mapping_is_idempotent_and_promotes_existing(db_session, two_users):
    a, b = two_users
    # 先存在一条 proposed 事实（如 agent 提案）
    proposed = source_facts.create_source_fact(
        db_session,
        fact_type="biological_parent",
        subject_user_id=b.id,
        object_user_id=a.id,
        provenance="agent_proposal",
        state=FACT_PROPOSED,
    )
    db_session.commit()
    assert proposed.state == FACT_PROPOSED

    # accept 映射不抛 duplicate，而是晋升 confirmed 并复用同一条
    mapped = map_structural_edge_to_fact(
        db_session, from_user=a.id, to_user=b.id, dir_class="elder"
    )
    db_session.commit()
    assert mapped.id == proposed.id
    assert mapped.state == FACT_CONFIRMED
    # 仍然只有一条事实
    assert len(db_session.scalars(select(SourceFact)).all()) == 1


def test_revoke_synchronously_invalidates_fact(db_session, two_users):
    a, b = two_users
    fact = map_structural_edge_to_fact(
        db_session, from_user=a.id, to_user=b.id, dir_class="elder"
    )
    db_session.commit()
    assert fact.state == FACT_CONFIRMED

    revoke_structural_edge_fact(
        db_session, from_user=a.id, to_user=b.id, dir_class="elder"
    )
    db_session.commit()
    db_session.refresh(fact)
    assert fact.state == FACT_REVOKED


def test_revoke_unknown_or_peer_is_noop(db_session, two_users):
    a, b = two_users
    # peer 无对应事实，revoke 不抛错
    revoke_structural_edge_fact(db_session, from_user=a.id, to_user=b.id, dir_class="peer")
    # 无事实则可安全调用
    revoke_structural_edge_fact(db_session, from_user=a.id, to_user=b.id, dir_class="spouse")
    db_session.commit()
    assert db_session.scalars(select(SourceFact)).all() == []


# ---- 命令层端到端（connection accept/revoke 真实接入）----


def test_connection_accept_writes_fact_and_revoke_invalidates(client, db_session):
    """HTTP 全链路：accept 结构边写 confirmed SourceFact；revoke 同步失效。"""
    from conftest import auth_header, login

    a = create_user_with_pin(db_session, "链路甲", "111111")
    b = create_user_with_pin(db_session, "链路乙", "222222")
    db_session.commit()
    ha = auth_header(login(client, "链路甲", "111111").json())
    hb = auth_header(login(client, "链路乙", "222222").json())

    r = client.post(
        "/api/connection-requests", json={"target_id": b.id, "dir_class": "elder"}, headers=ha
    )
    assert r.status_code == 201, r.text
    edge_id = r.json()["id"]

    acc = client.post(f"/api/connection-requests/{edge_id}/accept", headers=hb)
    assert acc.status_code == 200, acc.text

    # elder f→t：b 是 a 的长辈 → biological_parent(b, a)
    fact = _confirmed(db_session, "biological_parent", b.id, a.id)
    assert fact is not None
    assert fact.state == FACT_CONFIRMED
    assert fact.provenance == "connection_accept"

    # revoke 断连 → 事实同步 revoked
    rv = client.post(f"/api/relations/{edge_id}/revoke", headers=ha)
    assert rv.status_code == 200, rv.text
    db_session.refresh(fact)
    assert fact.state == FACT_REVOKED