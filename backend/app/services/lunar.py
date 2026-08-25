"""公农历互转封装（D7）：lunar-python 唯一出口，异常归一为 None。

结构化日期 JSON（architecture §5）：
    {"cal_type": "solar|lunar|none", "date": "...", "mirror_date": "...|null",
     "original_text": str?}

- 公历 date 为 ISO 'YYYY-MM-DD'。
- 农历 date 为 'YYYY:M:D'（冒号分隔，M 允许负数表示闰月，如 -2 = 闰二月）；
  人读文本由前端经 lunar 库渲染，original_text 存录入原文。
"""

from __future__ import annotations

from typing import Any

from lunar_python import Lunar, Solar  # type: ignore[import-untyped]


def solar_to_lunar(date_iso: str) -> str | None:
    """ISO 公历 → 农历 'YYYY:M:D'（M<0 为闰月）。失败返回 None。"""
    try:
        y, m, d = (int(x) for x in date_iso.split("-"))
        lunar = Solar.fromYmd(y, m, d).getLunar()
        return f"{lunar.getYear()}:{lunar.getMonth()}:{lunar.getDay()}"
    except Exception:  # noqa: BLE001 - 超范围/非法日期统一视为不可换算
        return None


def lunar_to_solar(date_lunar: str) -> str | None:
    """'YYYY:M:D'（农历）→ ISO 公历。失败返回 None。"""
    try:
        parts = date_lunar.split(":")
        if len(parts) != 3:
            return None
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        return str(Lunar.fromYmd(y, m, d).getSolar().toYmd())
    except Exception:  # noqa: BLE001
        return None


def enrich_structured_date(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """为 birth/death 结构化值补 mirror_date（另一历），失败置 None 不报错。"""
    if not payload or payload.get("cal_type") == "none":
        return payload
    date = payload.get("date")
    if not date:
        return payload
    mirror = solar_to_lunar(date) if payload["cal_type"] == "solar" else lunar_to_solar(date)
    return {**payload, "mirror_date": mirror}


__all__ = ["enrich_structured_date", "lunar_to_solar", "solar_to_lunar"]
