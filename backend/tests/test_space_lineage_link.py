"""家族空间显式配对合同（lineage_space_id + PUT lineage-link + 迁移回填）。

- PUT /spaces/{id}/lineage-link：household 管理员设置/解除所属 lineage；
  授权（空间管理员）、目标校验（存在 + kind=lineage + 操作者为 active 成员，
  防枚举统一 404）、lineage 行不可被配对（422）；
- POST /spaces 带 lineage_space_id：创建即挂入（须为该 lineage active 成员）；
  lineage 空间不接受配对（422）；
- GET /spaces 投影携带 lineage_space_id（前端「当前家族空间」切换的数据基础）；
- 迁移 0035 存量回填 SQL：owner 恰好拥有一个 lineage 时回填未配对 household；
  歧义（多 lineage）/已有显式配对的行不动；幂等可重复执行。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from conftest import (
    auth_header,
    create_space_member,
    create_user_with_pin,
    login,
    seed_space_with_owner,
)
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.space import FamilySpace

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0035_space_lineage_link.py"
)


def _load_migration_module():
    """按路径加载 0035 迁移模块（文件名以数字开头，无法常规 import）。"""
    spec = importlib.util.spec_from_file_location("_mig_0035_space_lineage_link", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _login(client: TestClient, name: str, pin: str) -> dict[str, str]:
    response = login(client, name, pin)
    assert response.status_code == 200, response.text
    return auth_header(response.json())


def _make_household_with_lineage(db_session, *, pin: str = "111111"):
    """household owner（加入 lineage）+ 独立 owner 的 lineage，返回三元组。"""
    owner = create_user_with_pin(db_session, "房主", pin)
    lineage_owner = create_user_with_pin(db_session, "族长", "222222")
    lineage = seed_space_with_owner(db_session, lineage_owner.id, name="测试家族", kind="lineage")
    household = seed_space_with_owner(db_session, owner.id, name="测试家庭", kind="household")
    create_space_member(db_session, lineage.id, owner.id, role="member")
    db_session.commit()
    return owner, household, lineage


def test_lineage_link_set_and_projected(client, db_session) -> None:
    """管理员设置配对 → 200 + 响应与 GET /spaces 均携带 lineage_space_id。"""
    owner, household, lineage = _make_household_with_lineage(db_session)
    headers = _login(client, owner.name, "111111")

    response = client.put(
        f"/api/spaces/{household.id}/lineage-link",
        headers=headers,
        json={"lineage_space_id": lineage.id},
    )

    assert response.status_code == 200, response.text
    assert response.json()["lineage_space_id"] == lineage.id
    spaces = {space["id"]: space for space in client.get("/api/spaces", headers=headers).json()}
    assert spaces[household.id]["lineage_space_id"] == lineage.id


def test_lineage_link_clear_with_null(client, db_session) -> None:
    """lineage_space_id=null → 解除配对。"""
    owner, household, lineage = _make_household_with_lineage(db_session)
    household.lineage_space_id = lineage.id
    db_session.commit()
    headers = _login(client, owner.name, "111111")

    response = client.put(
        f"/api/spaces/{household.id}/lineage-link",
        headers=headers,
        json={"lineage_space_id": None},
    )

    assert response.status_code == 200, response.text
    assert response.json()["lineage_space_id"] is None


def test_lineage_link_requires_space_manager(client, db_session) -> None:
    """普通成员（非管理员）→ 403。"""
    owner, household, lineage = _make_household_with_lineage(db_session)
    member = create_user_with_pin(db_session, "家庭成员", "333333")
    create_space_member(db_session, household.id, member.id, role="member")
    db_session.commit()

    response = client.put(
        f"/api/spaces/{household.id}/lineage-link",
        headers=_login(client, member.name, "333333"),
        json={"lineage_space_id": lineage.id},
    )

    assert response.status_code == 403, response.text


def test_lineage_link_rejects_household_target(client, db_session) -> None:
    """配对目标必须是 lineage（指向 household → 422）。"""
    owner, household, _lineage = _make_household_with_lineage(db_session)
    other_household = seed_space_with_owner(db_session, owner.id, name="另一家庭", kind="household")

    response = client.put(
        f"/api/spaces/{household.id}/lineage-link",
        headers=_login(client, owner.name, "111111"),
        json={"lineage_space_id": other_household.id},
    )

    assert response.status_code == 422, response.text


def test_lineage_link_rejected_on_lineage_space(client, db_session) -> None:
    """lineage 空间本身不可被配对（其管理员操作 → 422）。"""
    _owner, _household, lineage = _make_household_with_lineage(db_session)
    lineage_owner = create_user_with_pin(db_session, "族长本人", "444444")
    # 重新造一个由登录者自有的 lineage，避免依赖 _make_household_with_lineage 的族长 PIN
    own_lineage = seed_space_with_owner(
        db_session, lineage_owner.id, name="自有家族", kind="lineage"
    )

    response = client.put(
        f"/api/spaces/{own_lineage.id}/lineage-link",
        headers=_login(client, lineage_owner.name, "444444"),
        json={"lineage_space_id": lineage.id},
    )

    assert response.status_code == 422, response.text


def test_lineage_link_requires_lineage_membership(client, db_session) -> None:
    """操作者不是目标 lineage 的 active 成员 → 404（与不存在同码，防枚举）。"""
    owner = create_user_with_pin(db_session, "无族成员", "111111")
    lineage_owner = create_user_with_pin(db_session, "外族族长", "222222")
    lineage = seed_space_with_owner(db_session, lineage_owner.id, name="外族家族", kind="lineage")
    household = seed_space_with_owner(db_session, owner.id, name="无族家庭", kind="household")

    response = client.put(
        f"/api/spaces/{household.id}/lineage-link",
        headers=_login(client, owner.name, "111111"),
        json={"lineage_space_id": lineage.id},
    )

    assert response.status_code == 404, response.text


def test_create_space_with_lineage_link(client, db_session) -> None:
    """POST /spaces 带 lineage_space_id：active 成员创建 household 即挂入。"""
    owner, _household, lineage = _make_household_with_lineage(db_session)
    headers = _login(client, owner.name, "111111")

    response = client.post(
        "/api/spaces",
        headers=headers,
        json={"name": "新建家庭", "kind": "household", "lineage_space_id": lineage.id},
    )

    assert response.status_code == 201, response.text
    assert response.json()["lineage_space_id"] == lineage.id


def test_create_space_rejects_lineage_link_for_lineage_kind(client, db_session) -> None:
    """kind=lineage 时携带配对 → 422。"""
    owner, _household, _lineage = _make_household_with_lineage(db_session)

    response = client.post(
        "/api/spaces",
        headers=_login(client, owner.name, "111111"),
        json={"name": "新家族", "kind": "lineage", "lineage_space_id": 1},
    )

    assert response.status_code == 422, response.text


def test_create_space_requires_lineage_membership(client, db_session) -> None:
    """创建者不是目标 lineage 成员 → 404（防枚举）。"""
    owner = create_user_with_pin(db_session, "入族申请人", "111111")
    lineage_owner = create_user_with_pin(db_session, "另一族长", "222222")
    lineage = seed_space_with_owner(db_session, lineage_owner.id, name="他族家族", kind="lineage")

    response = client.post(
        "/api/spaces",
        headers=_login(client, owner.name, "111111"),
        json={"name": "无族新家庭", "kind": "household", "lineage_space_id": lineage.id},
    )

    assert response.status_code == 404, response.text


def test_owner_pairing_backfill_links_unambiguous_household(db_session) -> None:
    """owner 恰好拥有一个 lineage → 未配对 household 被回填；重复执行幂等。"""
    owner = create_user_with_pin(db_session, "回填族长", "111111")
    lineage = seed_space_with_owner(db_session, owner.id, name="回填家族", kind="lineage")
    household = seed_space_with_owner(db_session, owner.id, name="回填家庭", kind="household")
    module = _load_migration_module()

    db_session.execute(text(module.OWNER_PAIRING_BACKFILL_SQL))
    db_session.commit()
    db_session.execute(text(module.OWNER_PAIRING_BACKFILL_SQL))
    db_session.commit()
    db_session.expire_all()

    assert db_session.get(FamilySpace, household.id).lineage_space_id == lineage.id


def test_owner_pairing_backfill_skips_ambiguous_owner(db_session) -> None:
    """owner 名下两个 lineage → 歧义不猜，household 保持 NULL。"""
    owner = create_user_with_pin(db_session, "双族族长", "111111")
    seed_space_with_owner(db_session, owner.id, name="双族其一", kind="lineage")
    seed_space_with_owner(db_session, owner.id, name="双族其二", kind="lineage")
    household = seed_space_with_owner(db_session, owner.id, name="双族家庭", kind="household")
    module = _load_migration_module()

    db_session.execute(text(module.OWNER_PAIRING_BACKFILL_SQL))
    db_session.commit()
    db_session.expire_all()

    assert db_session.get(FamilySpace, household.id).lineage_space_id is None


def test_owner_pairing_backfill_keeps_explicit_link(db_session) -> None:
    """已有显式配对的 household 不被回填覆盖。"""
    owner = create_user_with_pin(db_session, "显式族长", "111111")
    lineage_a = seed_space_with_owner(db_session, owner.id, name="显家族甲", kind="lineage")
    seed_space_with_owner(db_session, owner.id, name="显家族乙", kind="lineage")
    household = seed_space_with_owner(db_session, owner.id, name="显家庭", kind="household")
    household.lineage_space_id = lineage_a.id
    db_session.commit()
    module = _load_migration_module()

    db_session.execute(text(module.OWNER_PAIRING_BACKFILL_SQL))
    db_session.commit()
    db_session.expire_all()

    assert db_session.get(FamilySpace, household.id).lineage_space_id == lineage_a.id
