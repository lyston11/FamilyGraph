"""m1d：农历互转、positions 端点权限。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.lunar import enrich_structured_date, lunar_to_solar, solar_to_lunar
from conftest import auth_header, create_user_with_pin, login


def test_leap_month_roundtrip():
    """闰月经 (ISO, is_leap_month) 对表达，往返稳定。"""
    assert solar_to_lunar("2023-04-05") == ("2023-02-15", True)
    assert lunar_to_solar("2023-02-15", is_leap_month=True) == "2023-04-05"

    lunar_date, is_leap = solar_to_lunar("2023-04-05")
    assert solar_to_lunar(lunar_to_solar(lunar_date, is_leap_month=is_leap)) == (
        lunar_date,
        is_leap,
    )


def test_leap_and_plain_month_are_distinct():
    """闰二月十五与平二月十五在同一 ISO 容器里必须换算到不同公历日。"""
    assert lunar_to_solar("2023-02-15", is_leap_month=True) == "2023-04-05"
    assert lunar_to_solar("2023-02-15", is_leap_month=False) == "2023-03-06"
    assert solar_to_lunar("2023-03-06") == ("2023-02-15", False)


def test_enrich_lunar_produces_mirror():
    """农历录入必须产出公历 mirror_date —— 此前该分支恒为 None。"""
    enriched = enrich_structured_date({"cal_type": "lunar", "date": "1948-03-12"})
    assert enriched["mirror_date"] == "1948-04-20"
    assert enriched["is_leap_month"] is False

    leap = enrich_structured_date(
        {"cal_type": "lunar", "date": "2023-02-15", "is_leap_month": True}
    )
    assert leap["mirror_date"] == "2023-04-05"


def test_enrich_solar_carries_leap_flag():
    """公历录入的农历镜像落在闰月时，is_leap_month 描述镜像那一侧。"""
    plain = enrich_structured_date({"cal_type": "solar", "date": "1948-04-20"})
    assert plain["mirror_date"] == "1948-03-12"
    assert plain["is_leap_month"] is False

    onto_leap = enrich_structured_date({"cal_type": "solar", "date": "2023-04-05"})
    assert onto_leap["mirror_date"] == "2023-02-15"
    assert onto_leap["is_leap_month"] is True


def test_enrich_and_garbage():
    assert enrich_structured_date({"cal_type": "solar", "date": "garbage"})["mirror_date"] is None
    assert enrich_structured_date({"cal_type": "lunar", "date": "garbage"})["mirror_date"] is None
    assert enrich_structured_date({"cal_type": "none"}) is not None


def test_positions_requires_active_membership(db_session, client: TestClient):
    owner = create_user_with_pin(db_session, "主", "101010")
    create_user_with_pin(db_session, "外人", "909090")
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
