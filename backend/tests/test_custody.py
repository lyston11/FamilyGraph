"""custody 权限矩阵全分支单测（D5 归属模式逐行；v2 Foundation 语义）。

矩阵：
| 主体                          | view | edit | delete |
| 本人（claimed）               | full | ✔    | ✔      |
| 代管创建者·handover 未 claimed | full | ✔    | ✔      |
| 代管创建者·handover 已 claimed | full | ✘403 | ✘      |
| 创建者·perpetual              | full | ✔    | ✔      |
| platform_operator             | none | ✘404 | ✘404   |
| 其他已登录用户                 | none | ✘    | ✘      |
"""

import pytest
from conftest import create_user_with_pin
from fastapi import HTTPException

from app.errors import extract_api_error
from app.services import custody


def _creator_and_target(db_session, *, privacy_mode: str, claimed: bool):
    creator = create_user_with_pin(db_session, "创建者", "111111")
    target = create_user_with_pin(
        db_session,
        "亲人",
        "222222",
        pin_must_change=not claimed,
        claim_status="claimed" if claimed else "managed",
        privacy_mode=privacy_mode,
        created_by=creator.id,
    )
    return creator, target


def test_self_full_access(db_session) -> None:
    user = create_user_with_pin(db_session, "本人", "123456")
    access = custody.resolve_relation(user, user)
    assert access == custody.RelationAccess(custody.VIEW_FULL, True, True)


def test_creator_handover_managed_full_custody(db_session) -> None:
    creator, target = _creator_and_target(db_session, privacy_mode="handover", claimed=False)
    access = custody.resolve_relation(creator, target)
    assert access == custody.RelationAccess(custody.VIEW_FULL, True, True)


def test_creator_handover_claimed_loses_edit_and_delete(db_session) -> None:
    creator, target = _creator_and_target(db_session, privacy_mode="handover", claimed=True)
    access = custody.resolve_relation(creator, target)
    assert access == custody.RelationAccess(custody.VIEW_FULL, False, False)

    with pytest.raises(HTTPException) as exc_info:
        custody.assert_can_edit(creator, target)
    error = extract_api_error(exc_info.value.detail)
    assert error is not None and error["code"] == "CUSTODY_HANDOVER_DONE"

    with pytest.raises(HTTPException) as del_exc:
        custody.assert_can_delete(creator, target)
    assert extract_api_error(del_exc.value.detail)["code"] == "CUSTODY_HANDOVER_DONE"


def test_creator_perpetual_keeps_rights_after_claim(db_session) -> None:
    creator, target = _creator_and_target(db_session, privacy_mode="perpetual", claimed=True)
    access = custody.resolve_relation(creator, target)
    assert access == custody.RelationAccess(custody.VIEW_FULL, True, True)
    custody.assert_can_edit(creator, target)  # 不抛错
    custody.assert_can_delete(creator, target)


def test_platform_operator_no_custody_on_foreign_profile(db_session) -> None:
    """v2 §0.2：platform_operator 无任何家庭数据编辑/删除权（none→404）。"""
    operator = create_user_with_pin(db_session, "运营者", "999999", is_admin=True)
    stranger = create_user_with_pin(db_session, "路人", "888888")
    access = custody.resolve_relation(operator, stranger)
    assert access == custody.RelationAccess(custody.VIEW_NONE, False, False)

    with pytest.raises(HTTPException) as exc_info:
        custody.assert_can_edit(operator, stranger)
    error = extract_api_error(exc_info.value.detail)
    assert exc_info.value.status_code == 404
    assert error is not None and error["code"] == "USER_NOT_FOUND"


def test_unrelated_user_none_semantics(db_session) -> None:
    actor = create_user_with_pin(db_session, "无关者", "777777")
    target = create_user_with_pin(db_session, "他人档案", "666666")
    access = custody.resolve_relation(actor, target)
    assert access == custody.RelationAccess(custody.VIEW_NONE, False, False)

    for guard in (custody.assert_can_edit, custody.assert_can_delete):
        with pytest.raises(HTTPException) as exc_info:
            guard(actor, target)
        error = extract_api_error(exc_info.value.detail)
        assert exc_info.value.status_code == 404
        assert error is not None and error["code"] == "USER_NOT_FOUND"
