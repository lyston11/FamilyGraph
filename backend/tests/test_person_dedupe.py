"""同一空间内重复建档防护（services/person_identity + 建档门禁）。

断言点分三类：
1. 归一与强度判定的纯函数口径（繁简、农历/公历、生日缺失）；
2. 建档门禁的拒绝与放行，含"强匹配不受消歧开关影响"；
3. 并发建档恰好一个成功——这条依赖 BEGIN IMMEDIATE，是唯一索引缺位时的
   唯一保证，必须有用例守住。
"""

from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException

from app.commands import members as member_commands
from app.commands.context import ActorContext
from app.db import SessionLocal
from app.errors import PERSON_DUPLICATE_AMBIGUOUS, PERSON_DUPLICATE_IN_SPACE, extract_api_error
from app.models.space import FamilySpace, SpaceMember
from app.services import person_identity as pid
from app.utils.timeutil import utcnow
from conftest import auth_header, create_user_with_pin, login

_SOLAR = {"cal_type": "solar", "date": "1948-03-12"}
_SOLAR_OTHER = {"cal_type": "solar", "date": "1950-01-01"}
# 农历 1948-03-12 == 公历 1948-04-20（canonical_birth 自行换算）
_LUNAR_SAME_DAY = {"cal_type": "lunar", "date": "1948-03-12"}
# 已 enrich 的农历行：mirror_date 由服务端写入，canonical_birth 应直接读它
_LUNAR_ENRICHED = {
    "cal_type": "lunar",
    "date": "1948-03-12",
    "is_leap_month": False,
    "mirror_date": "1948-04-20",
}
_SOLAR_OF_LUNAR = {"cal_type": "solar", "date": "1948-04-20"}


def _ctx(user) -> ActorContext:
    return ActorContext(
        user_id=user.id, account_id=user.account.id, account_status=user.account.status
    )


def _make_space(db_session, owner, *, kind: str = "lineage") -> FamilySpace:
    space = FamilySpace(
        name=f"{owner.name}的家族", owner_id=owner.id, kind=kind, created_at=utcnow()
    )
    db_session.add(space)
    db_session.flush()
    db_session.add(
        SpaceMember(
            space_id=space.id,
            user_id=owner.id,
            added_by=owner.id,
            role="space_admin",
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    db_session.commit()
    return space


def _api_code(exc: HTTPException) -> str | None:
    payload = extract_api_error(exc.detail)
    return None if payload is None else str(payload.get("code"))


# ---- 1. 纯函数口径 ----


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        ("李秀英", "李秀英", True),  # 繁简
        ("張三", "张三", True),
        ("李 秀英", "李·秀英", True),  # 空白与分隔符
        ("峯", "峰", True),  # zhconv 覆盖的异体字
        ("喆", "哲", False),  # 未覆盖的异体字：落到 weak 要求消歧，不静默合并
        ("李秀英", "王小明", False),
    ],
)
def test_name_key_normalization(left: str, right: str, same: bool) -> None:
    assert (pid.normalize_person_name(left) == pid.normalize_person_name(right)) is same


def test_canonical_birth_converts_lunar_to_solar() -> None:
    """农历读 mirror_date，缺失时回落现算，两条路径都得到同一公历键。"""
    assert pid.canonical_birth(_SOLAR) == "1948-03-12"
    assert pid.canonical_birth(_LUNAR_SAME_DAY) == "1948-04-20"  # 历史行：无 mirror_date
    assert pid.canonical_birth(_LUNAR_ENRICHED) == "1948-04-20"  # 已 enrich：读 mirror_date
    assert pid.canonical_birth({"cal_type": "none"}) is None
    assert pid.canonical_birth(None) is None


def test_canonical_birth_distinguishes_leap_month() -> None:
    """闰二月十五与平二月十五是不同的两天，比对键必须区分。"""
    leap = {"cal_type": "lunar", "date": "2023-02-15", "is_leap_month": True}
    plain = {"cal_type": "lunar", "date": "2023-02-15"}
    assert pid.canonical_birth(leap) == "2023-04-05"
    assert pid.canonical_birth(plain) == "2023-03-06"
    assert pid.canonical_birth(leap) != pid.canonical_birth(plain)


@pytest.mark.parametrize(
    ("birth_a", "birth_b", "expected"),
    [
        (_SOLAR, _SOLAR, pid.STRENGTH_STRONG),
        (_LUNAR_SAME_DAY, _SOLAR_OF_LUNAR, pid.STRENGTH_STRONG),  # 跨历同一天
        (_SOLAR, None, pid.STRENGTH_WEAK),
        (None, None, pid.STRENGTH_WEAK),
        (_SOLAR, _SOLAR_OTHER, pid.STRENGTH_NONE),  # 同名不同人
    ],
)
def test_match_strength_matrix(birth_a, birth_b, expected: str) -> None:
    key = pid.normalize_person_name("李秀英")
    assert (
        pid.match_strength(
            name_key=key,
            birth_key=pid.canonical_birth(birth_a),
            other_name_key=key,
            other_birth_key=pid.canonical_birth(birth_b),
        )
        == expected
    )


# ---- 2. 建档门禁 ----


def _create(db_session, creator, space, name: str, birth=None, *, allow: bool = False):
    return member_commands.create_member(
        db_session,
        _ctx(creator),
        name=name,
        birth=birth,
        space_membership_space_id=space.id,
        allow_duplicate_person=allow,
    )


def test_strong_match_refused_with_reference_path(db_session) -> None:
    """同名同生日：拒绝并给出既有档案 id，调用方应改为引用它。"""
    creator = create_user_with_pin(db_session, "建档人甲", "110011")
    space = _make_space(db_session, creator)
    existing, _ = _create(db_session, creator, space, "李秀英", _SOLAR)

    with pytest.raises(HTTPException) as exc:
        _create(db_session, creator, space, "李秀英", _SOLAR)
    assert exc.value.status_code == 409
    assert _api_code(exc.value) == PERSON_DUPLICATE_IN_SPACE
    payload = extract_api_error(exc.value.detail)
    assert payload is not None
    detail = payload["detail"]
    assert isinstance(detail, dict)
    assert detail["resolution"] == "reference_existing"
    assert [e["user_id"] for e in detail["existing"]] == [existing.id]


def test_strong_match_refused_even_with_disambiguation_flag(db_session) -> None:
    """消歧开关只放宽弱匹配；同名同生日不是"需要人来判断"的情况。"""
    creator = create_user_with_pin(db_session, "建档人乙", "120012")
    space = _make_space(db_session, creator)
    _create(db_session, creator, space, "李秀英", _SOLAR)

    with pytest.raises(HTTPException) as exc:
        _create(db_session, creator, space, "李秀英", _SOLAR, allow=True)
    assert _api_code(exc.value) == PERSON_DUPLICATE_IN_SPACE


def test_traditional_and_lunar_variants_still_match(db_session) -> None:
    """繁体录入 + 农历生日，与简体公历的同一人仍判为强匹配。"""
    creator = create_user_with_pin(db_session, "建档人丙", "130013")
    space = _make_space(db_session, creator)
    _create(db_session, creator, space, "李秀英", _SOLAR_OF_LUNAR)

    with pytest.raises(HTTPException) as exc:
        _create(db_session, creator, space, "李秀英", _LUNAR_SAME_DAY)
    assert _api_code(exc.value) == PERSON_DUPLICATE_IN_SPACE


def test_weak_match_requires_explicit_disambiguation(db_session) -> None:
    """同名但生日缺失：打断创建，要求创建者确认，而不是静默放行或静默合并。"""
    creator = create_user_with_pin(db_session, "建档人丁", "140014")
    space = _make_space(db_session, creator)
    first, _ = _create(db_session, creator, space, "李秀英", None)

    with pytest.raises(HTTPException) as exc:
        _create(db_session, creator, space, "李秀英", _SOLAR)
    assert exc.value.status_code == 409
    assert _api_code(exc.value) == PERSON_DUPLICATE_AMBIGUOUS
    payload = extract_api_error(exc.value.detail)
    assert payload is not None
    detail = payload["detail"]
    assert isinstance(detail, dict)
    assert [c["user_id"] for c in detail["candidates"]] == [first.id]

    # 确认是另一个人后放行
    second, _ = _create(db_session, creator, space, "李秀英", _SOLAR, allow=True)
    assert second.id != first.id


def test_same_name_different_birth_is_not_duplicate(db_session) -> None:
    """双方生日都在且不同 → 同名不同人，直接放行，不追问。"""
    creator = create_user_with_pin(db_session, "建档人戊", "150015")
    space = _make_space(db_session, creator)
    first, _ = _create(db_session, creator, space, "李秀英", _SOLAR)
    second, _ = _create(db_session, creator, space, "李秀英", _SOLAR_OTHER)
    assert second.id != first.id


def test_scope_is_per_space(db_session) -> None:
    """两个不相干家庭各有一个"李秀英 1948-03-12"必须都允许。"""
    creator = create_user_with_pin(db_session, "建档人己", "160016")
    space_a = _make_space(db_session, creator)
    space_b = _make_space(db_session, creator)
    first, _ = _create(db_session, creator, space_a, "李秀英", _SOLAR)
    second, _ = _create(db_session, creator, space_b, "李秀英", _SOLAR)
    assert second.id != first.id


def test_stored_name_is_never_overwritten_by_normalization(db_session) -> None:
    """归一只作用于比对键；用户填的写法原样保留（待本人确档时确认）。"""
    creator = create_user_with_pin(db_session, "建档人庚", "170017")
    space = _make_space(db_session, creator)
    member, _ = _create(db_session, creator, space, "李 秀英", _SOLAR)
    assert member.name == "李 秀英"


# ---- 3. 并发：BEGIN IMMEDIATE 是唯一索引缺位时的唯一保证 ----

# barrier/join 一律带超时：线程若在会合前死掉，超时让用例失败而不是挂死整套
# （tests/test_ownership_transfer.py 的无超时 barrier 就会挂死全量）。
_SYNC_TIMEOUT = 10.0


def test_concurrent_create_same_person_single_winner(db_session) -> None:
    """两个人同时建同一个人：恰好一个成功，另一个拿 409。

    没有 BEGIN IMMEDIATE 时两个事务会各自通过检查然后都插入——这正是
    service 层 SELECT 挡不住、必须靠写锁前置的情形。
    """
    creator = create_user_with_pin(db_session, "并发建档人", "180018")
    space = _make_space(db_session, creator)
    creator_id, account_id, space_id = creator.id, creator.account.id, space.id
    db_session.commit()

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        session = SessionLocal()
        try:
            ctx = ActorContext(user_id=creator_id, account_id=account_id, account_status="claimed")
            barrier.wait(timeout=_SYNC_TIMEOUT)
            member_commands.create_member(
                session,
                ctx,
                name="李秀英",
                birth=_SOLAR,
                space_membership_space_id=space_id,
            )
            result = "won"
        except HTTPException as exc:
            result = f"{exc.status_code}:{_api_code(exc)}"
        except Exception as exc:  # noqa: BLE001 - 任何其他异常都要可见而不是静默
            result = f"error:{type(exc).__name__}"
        finally:
            session.close()
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=_SYNC_TIMEOUT)
        assert not thread.is_alive(), "建档线程超时未结束（疑似死锁）"

    assert sorted(outcomes) == sorted(["won", f"409:{PERSON_DUPLICATE_IN_SPACE}"]), outcomes
    surviving = pid.find_duplicate_candidates(
        db_session, space_id=space_id, name="李秀英", birth=_SOLAR
    )
    assert len(surviving) == 1, f"空间内应只剩一份档案，实际 {len(surviving)}"


# ---- 4. HTTP 往返 ----


def test_http_ambiguous_then_confirm_reuses_idempotency_key(client, db_session) -> None:
    """弱匹配 409 后，用户确认「是另一个人」可复用同一 Idempotency-Key 重放。

    这条同时锁定幂等设计：allow_duplicate_person 刻意不进 request_hash，
    否则同键带标记重试会撞 IDEMPOTENCY_PAYLOAD_CONFLICT，把消歧流程堵死。
    """
    creator = create_user_with_pin(db_session, "接口建档人", "190019")
    space = _make_space(db_session, creator)
    space_id = space.id
    db_session.commit()
    headers = auth_header(login(client, "接口建档人", "190019").json())

    def _post(key: str, *, allow: bool | None = None):
        payload: dict = {
            "name": "李秀英",
            "relation_dir_class": "elder",
            "space_membership": {"space_id": space_id},
        }
        if allow is not None:
            payload["allow_duplicate_person"] = allow
        return client.post("/api/users", json=payload, headers={**headers, "Idempotency-Key": key})

    assert _post("dedupe-first").status_code == 201

    conflict = _post("dedupe-second")
    assert conflict.status_code == 409
    error = conflict.json()["error"]
    assert error["code"] == PERSON_DUPLICATE_AMBIGUOUS
    assert error["detail"]["resolution"] == "reference_existing_or_confirm_distinct"

    # 同键 + 确认标记 → 放行（而非 IDEMPOTENCY_PAYLOAD_CONFLICT）
    confirmed = _post("dedupe-second", allow=True)
    assert confirmed.status_code == 201, confirmed.json()
