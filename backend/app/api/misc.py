"""杂项端点：lunar 镜像、统计、搜索（可见性口径统一走 visibility.py v2）。"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.models.account import Account
from app.models.relation import Relation
from app.models.user import User
from app.services.lunar import lunar_to_solar, solar_to_lunar
from app.services.visibility import (
    FIELD_CLEAR,
    PURPOSE_SEARCH,
    PURPOSE_STATISTICS,
    evaluate,
    visible_user_ids,
)
from app.utils.timeutil import utcnow

router = APIRouter(tags=["misc"])


@router.get("/lunar/mirror")
def lunar_mirror(
    cal_type: str = Query(pattern="^(solar|lunar)$"),
    date: str = Query(min_length=8, max_length=10),
    _identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> dict[str, str | None]:
    """公农历互转预览（m3b 前端历别切换自动互填）。"""
    mirror = solar_to_lunar(date) if cal_type == "solar" else lunar_to_solar(date)
    return {"mirror": mirror}


# ---- m3c 统计 ----


@router.get("/stats")
def stats(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> dict[str, Any]:
    """可见范围内家族统计（矩阵：可见者计入，其余不计入）。"""
    actor, _account = identity
    visible = visible_user_ids(session, actor)
    users = session.query(User).filter(User.id.in_(visible)).all() if visible else []

    total = len(users)
    by_gender: dict[str, int] = {"m": 0, "f": 0, "unknown": 0}
    generation: dict[int, int] = {}
    now = utcnow()
    this_month = now.month
    birthdays: list[dict[str, Any]] = []
    for u in users:
        # F2：统计必须消费字段级投影；被遮蔽的 gender/birth 不得进入聚合。
        decision = evaluate(session, actor, u, purpose=PURPOSE_STATISTICS)
        if not decision.visible:  # 冗余防线，理论上 visible 集已过滤
            continue
        if decision.fields.get("gender") == FIELD_CLEAR:
            by_gender[u.gender] = by_gender.get(u.gender, 0) + 1
        else:
            by_gender["unknown"] += 1
        birth_clear = decision.fields.get("birth") == FIELD_CLEAR
        if not birth_clear:
            continue
        birth = u.birth if isinstance(u.birth, dict) else {}
        date_str = birth.get("date") or birth.get("mirror_date")
        if date_str:
            try:
                month = int(str(date_str).split("-")[1].lstrip("0") or 0)
                gen_year = int(str(date_str).split("-")[0])
                gen = now.year - gen_year
                bucket = min(gen // 20 * 20, 120) if gen > 0 else 0
                generation[bucket] = generation.get(bucket, 0) + 1
                if month == this_month:
                    birthdays.append({"id": u.id, "name": u.name, "date": date_str})
            except (ValueError, IndexError):
                continue
    return {
        "total": total,
        "by_gender": by_gender,
        "generation_histogram": [{"bucket": k, "count": v} for k, v in sorted(generation.items())],
        "birthdays_this_month": birthdays,
    }


# ---- m3d 搜索 ----


@router.get("/search")
def search(
    q: str = Query(min_length=1, max_length=64),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[dict[str, Any]]:
    """名字/称谓标签前缀匹配；范围=可见性策略命中集合（none 永不返回）。"""
    actor, _account = identity
    visible = visible_user_ids(session, actor)
    if not visible:
        return []
    users = (
        session.query(User)
        .filter(User.id.in_(visible), or_(User.name.like(f"{q}%"), User.name.like(f"%{q}%")))
        .order_by(User.name)
        .limit(20)
        .all()
    )
    # 称谓标签匹配：relation.label 前缀命中 → 返回对端用户
    label_edges = (
        session.query(Relation)
        .filter(Relation.status == "active", Relation.label.like(f"{q}%"))
        .all()
    )
    extra_ids = {
        e.to_user if e.from_user == actor.id else e.from_user
        for e in label_edges
        if actor.id in (e.from_user, e.to_user)
    } - {u.id for u in users}
    if extra_ids:
        users.extend(session.query(User).filter(User.id.in_(extra_ids)).all())

    out: list[dict[str, Any]] = []
    for u in users:
        decision = evaluate(session, actor, u, purpose=PURPOSE_SEARCH)
        if not decision.visible:
            continue
        out.append({"id": u.id, "name": u.name, "level": decision.level})
    return out


void_cast = cast
