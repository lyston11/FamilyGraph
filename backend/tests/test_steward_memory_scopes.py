"""Steward 可读记忆级别的配置语义（10-10）。

## 这组测试证明的三件事

1. **三层取交集**：env ∩ 平台列 ∩ 空间列，任一层为空即整体为空。用并集会
   让「空间关掉」被平台打开覆盖——那是数据所有者收不回的授权。
2. **private 的额外约束不可由配置放宽**：没有 viewer 的 kind 一律去掉 private。
   这是防「空间级 kind 回落到 space admin 身份后读到管理员私事」的那条门。
3. **脏配置 fail-closed**：未知 scope 读取时被忽略（并告警），写入时 422。
   一个拼错的词不得放开任何东西。
"""

from __future__ import annotations

import pytest

from app import config
from app.models.agent_provider import AgentSpaceProviderSetting
from app.models.platform_features import PlatformFeatureConfig
from app.services import steward_memory
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture


def _set_env(monkeypatch, value: str) -> None:
    monkeypatch.setattr(config, "STEWARD_MEMORY_SCOPES", value, raising=False)


def _set_platform(db, value: str) -> None:
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, updated_at=utcnow())
        db.add(row)
    row.steward_memory_scopes = value
    db.commit()


def _set_space(db, space_id: int, value: str) -> None:
    row = AgentSpaceProviderSetting(
        space_id=space_id,
        agent_kind="steward",
        steward_memory_scopes=value,
        enabled=True,
    )
    db.add(row)
    db.commit()


# ---- 解析与编码 ----


def test_parse_normalizes_to_declaration_order_and_dedupes():
    assert steward_memory.parse_scopes("lineage, private,lineage") == ("private", "lineage")
    assert steward_memory.parse_scopes("") == ()
    assert steward_memory.parse_scopes(None) == ()
    assert steward_memory.parse_scopes("  household  ") == ("household",)


def test_parse_ignores_unknown_scopes_instead_of_opening_them(caplog):
    # 拼错的词不得被当成合法 scope，也不得让整份配置被丢弃（其余项仍然生效）。
    with caplog.at_level("WARNING"):
        assert steward_memory.parse_scopes("private,public,secrets") == ("private",)
    assert "secrets" in caplog.text


def test_parse_strict_rejects_unknown_scopes():
    # 管理面写入路径：未知值必须 422（由路由转），不能静默丢弃。
    with pytest.raises(ValueError):
        steward_memory.parse_scopes_strict("private,public")
    assert steward_memory.parse_scopes_strict("private,lineage") == ("private", "lineage")
    assert steward_memory.parse_scopes_strict("") == ()


def test_encode_is_canonical():
    assert steward_memory.encode_scopes({"lineage", "private"}) == "private,lineage"
    assert steward_memory.encode_scopes([]) == ""


# ---- 三层交集 ----


def test_effective_is_intersection_of_all_three_layers(db_session, monkeypatch):
    user, space = create_agent_fixture(db_session, name="scopes-intersect")
    _set_env(monkeypatch, "private,household,lineage")
    _set_platform(db_session, "household,lineage")
    _set_space(db_session, space.id, "lineage")

    assert steward_memory.effective_scopes(db_session, space_id=space.id) == ("lineage",)


def test_any_empty_layer_yields_empty(db_session, monkeypatch):
    user, space = create_agent_fixture(db_session, name="scopes-empty")
    _set_env(monkeypatch, "private,household,lineage")
    _set_platform(db_session, "household,lineage")
    _set_space(db_session, space.id, "")

    # 空间层为空 → 整体为空（空间可以单方面收回授权）。
    assert steward_memory.effective_scopes(db_session, space_id=space.id) == ()

    _set_space_row(db_session, space.id, "household")
    monkeypatch.setattr(config, "STEWARD_MEMORY_SCOPES", "", raising=False)
    # env 为空 → 部署级关闭，即使平台与空间都开着。
    assert steward_memory.effective_scopes(db_session, space_id=space.id) == ()


def _set_space_row(db, space_id: int, value: str) -> None:
    row = db.query(AgentSpaceProviderSetting).filter_by(space_id=space_id).one()
    row.steward_memory_scopes = value
    db.commit()


def test_missing_platform_row_is_not_an_implicit_allow(db_session, monkeypatch):
    user, space = create_agent_fixture(db_session, name="scopes-no-platform-row")
    _set_env(monkeypatch, "private,household,lineage")
    _set_space(db_session, space.id, "household")

    # 平台行缺失 → 平台层为空（与 assist 开关「行缺失不视为开启」同口径）。
    assert steward_memory.effective_scopes(db_session, space_id=space.id) == ()


# ---- private 的额外约束 ----


def test_private_is_dropped_when_the_run_has_no_viewer(db_session, monkeypatch):
    user, space = create_agent_fixture(db_session, name="scopes-no-viewer")
    _set_env(monkeypatch, "private,household,lineage")
    _set_platform(db_session, "private,household,lineage")
    _set_space(db_session, space.id, "private,household,lineage")

    # 空间级 kind（无 viewer）拿不到 private，即使三层配置都允许。
    assert steward_memory.readable_scopes(
        db_session, space_id=space.id, viewer_account_id=None
    ) == ("household", "lineage")
    # 带 viewer 时才可能读 private。
    assert steward_memory.readable_scopes(
        db_session, space_id=space.id, viewer_account_id=user.account.id
    ) == ("private", "household", "lineage")


def test_kind_may_read_private_only_for_viewer_scoped_kinds():
    assert steward_memory.kind_may_read_private("terminology") is True
    for kind in ("candidate", "ranking", "explanation", None):
        assert steward_memory.kind_may_read_private(kind) is False


# ---- 读取身份解析 ----


def test_resolve_reader_prefers_viewer(db_session):
    user, space = create_agent_fixture(db_session, name="reader-viewer")
    resolved = steward_memory.resolve_reader(
        db_session, space_id=space.id, viewer_account_id=user.account.id
    )
    assert resolved is not None
    assert resolved[0].id == user.id
    assert resolved[1].id == user.account.id


def test_resolve_reader_falls_back_to_space_admin(db_session):
    user, space = create_agent_fixture(db_session, name="reader-admin")
    resolved = steward_memory.resolve_reader(db_session, space_id=space.id, viewer_account_id=None)
    assert resolved is not None
    assert resolved[0].id == user.id


def test_resolve_reader_is_none_without_admin_or_viewer(db_session):
    user, space = create_agent_fixture(db_session, name="reader-none")
    from app.models.space import SpaceMember

    member = db_session.query(SpaceMember).filter_by(space_id=space.id).one()
    member.status = "removed"
    db_session.commit()

    # 无 viewer 且无 active space_admin → 调用方必须 fail-closed。
    assert (
        steward_memory.resolve_reader(db_session, space_id=space.id, viewer_account_id=None) is None
    )
