"""m3b/m3c/m3d：lunar 镜像端点、统计口径、搜索范围。"""

from __future__ import annotations

import pytest
from conftest import auth_header, create_user_with_pin, login
from fastapi.testclient import TestClient

from app.models.relation import Relation
from app.utils.timeutil import utcnow


def _login(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


@pytest.fixture()
def three_families(db_session):
    a = create_user_with_pin(
        db_session,
        "甲",
        "111111",
        claim_status="claimed",
        birth={"cal_type": "solar", "date": "1960-06-15"},
        gender="m",
    )
    b = create_user_with_pin(
        db_session,
        "乙",
        "222222",
        claim_status="claimed",
        gender="f",
    )
    c = create_user_with_pin(
        db_session,
        "丙",
        "333333",
        claim_status="claimed",
        birth={"cal_type": "lunar", "date": "1955:3:8"},
    )
    d = create_user_with_pin(db_session, "丁", "444444", claim_status="claimed")

    def edge(f, t, cls):
        now = utcnow()
        db_session.add(
            Relation(
                from_user=f.id,
                to_user=t.id,
                dir_class=cls,
                created_by=f.id,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )

    edge(a, b, "elder")
    edge(b, c, "peer")
    db_session.commit()
    return {"A": a, "B": b, "C": c, "D": d}


# ---- m3b lunar mirror ----


def test_lunar_mirror_endpoint(client: TestClient, three_families):
    h = _login(client, "甲", "111111")
    r = client.get("/api/lunar/mirror?cal_type=solar&date=2023-04-05", headers=h)
    assert r.status_code == 200 and r.json()["mirror"] == "2023:-2:15"
    r2 = client.get(
        "/api/lunar/mirror?cal_type=lunar&date=2023:-2:15".replace("--2", "-2"), headers=h
    )
    assert r2.status_code == 200


# ---- m3c 统计 ----


def test_stats_scope_excludes_invisible(db_session, client: TestClient, three_families):
    ha = _login(client, "甲", "111111")
    s = client.get("/api/stats", headers=ha).json()
    # v2：甲可见 = 自己 + 直系对端乙（lineage_summary）；peer 对端丙不再计入
    assert s["total"] == 2
    assert s["by_gender"]["m"] >= 1

    hd = _login(client, "丁", "444444")
    sd = client.get("/api/stats", headers=hd).json()
    assert sd["total"] == 1  # 只有丁自己


# ---- m3d 搜索 ----


def test_stats_does_not_leak_masked_gender(client: TestClient, three_families):
    """F2：直系边对端仅 lineage_summary，gender 被遮蔽，不得计入 m/f 桶。"""
    ha = _login(client, "甲", "111111")
    s = client.get("/api/stats", headers=ha).json()
    # 甲（本人 self）gender=m 明文；乙（直系边 lineage_summary）gender 被遮蔽进 unknown
    assert s["by_gender"]["m"] == 1
    assert s["by_gender"]["f"] == 0
    assert s["by_gender"]["unknown"] == 1
    # 乙的性别不能通过任何聚合桶出现
    assert sum(s["by_gender"].values()) == s["total"]


def test_search_hits_within_visibility_only(db_session, client: TestClient, three_families):
    ha = _login(client, "甲", "111111")
    # v2：直系对端乙可命中；peer 对端丙与无关丁不可命中
    r = client.get("/api/search?q=乙", headers=ha).json()
    ids = [x["id"] for x in r]
    assert three_families["B"].id in ids

    r_c = client.get("/api/search?q=丙", headers=ha).json()
    assert all(x["id"] != three_families["C"].id for x in r_c)

    r_d = client.get("/api/search?q=丁", headers=ha).json()
    assert all(x["id"] != three_families["D"].id for x in r_d)

    # 称谓标签命中（对端为直系可达者）
    hb = _login(client, "乙", "222222")
    from app.models.relation import Relation as R

    e = (
        db_session.query(R)
        .filter(R.from_user == three_families["B"].id, R.to_user == three_families["A"].id)
        .first()
    )
    if e is None:
        now = utcnow()
        e = R(
            from_user=three_families["B"].id,
            to_user=three_families["A"].id,
            dir_class="younger",
            label="儿子",
            created_by=three_families["B"].id,
            status="active",
            created_at=now,
            updated_at=now,
        )
        db_session.add(e)
        db_session.commit()
    r_label = client.get("/api/search?q=儿", headers=hb).json()
    assert any(x["id"] == three_families["A"].id for x in r_label)


def test_search_level_lineage_summary_for_direct_edge(
    db_session, client: TestClient, three_families
):
    """v2：直系跨空间搜索结果为 lineage_summary 级。"""
    ha = _login(client, "甲", "111111")
    r = client.get("/api/search?q=乙", headers=ha).json()
    entry = next(x for x in r if x["id"] == three_families["B"].id)
    assert entry["level"] == "lineage_summary"
