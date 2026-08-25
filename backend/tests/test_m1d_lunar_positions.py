"""m1d：农历互转、positions 端点权限。"""

from __future__ import annotations

from conftest import auth_header, create_user_with_pin, login
from fastapi.testclient import TestClient

from app.services.lunar import enrich_structured_date, lunar_to_solar, solar_to_lunar


def test_leap_month_roundtrip():
    lunar = solar_to_lunar("2023-04-05")
    assert lunar == "2023:-2:15"
    assert lunar_to_solar(lunar) == "2023-04-05"
    assert solar_to_lunar(lunar_to_solar(lunar)) == lunar


def test_enrich_and_garbage():
    assert (
        enrich_structured_date({"cal_type": "lunar", "date": "2023:-2:15"})["mirror_date"]
        == "2023-04-05"
    )
    assert enrich_structured_date({"cal_type": "solar", "date": "garbage"})["mirror_date"] is None
    assert enrich_structured_date({"cal_type": "none"}) is not None


def test_positions_requires_active_membership(db_session, client: TestClient):
    owner = create_user_with_pin(db_session, "主", "101010")
    outsider = create_user_with_pin(db_session, "外人", "909090")
    db_session.commit()

    token = login(client, "主", "101010").json()
    headers = auth_header(token)
    space_id = client.post("/api/spaces", json={"name": "我家"}, headers=headers).json()["id"]

    # PUT 保存 + 回读
    put = client.put(
        f"/api/spaces/{space_id}/positions",
        json={"items": [{"user_id": owner.id, "x": 10.5, "y": -20}]},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    got = client.get(f"/api/spaces/{space_id}/positions", headers=headers)
    assert got.status_code == 200
    assert got.json() == [{"user_id": owner.id, "x": 10.5, "y": -20.0}]

    # 非成员 404（防枚举）
    o_token = login(client, "外人", "909090").json()
    r = client.get(f"/api/spaces/{space_id}/positions", headers=auth_header(o_token))
    assert r.status_code == 404
