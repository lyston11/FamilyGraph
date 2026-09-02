"""公农历互转封装（D7）：lunar-python 唯一出口，异常归一为 None。

结构化日期 JSON（architecture §5）：
    {"cal_type": "solar|lunar|none", "date": "...", "mirror_date": "...|null",
     "original_text": str?}

- 公历与农历的 date 同为 ISO 'YYYY-MM-DD'；农历时数字含义是农历年-月-日，
  月份恒为 1..12 正数，闰月由独立的 is_leap_month 布尔键承载。
- is_leap_month 恒描述**农历那一侧**：cal_type=lunar 时指 date，cal_type=solar 时指
  mirror_date。date/mirror_date 有且仅有一个是农历，故无歧义。
- lunar-python 用负数月份标闰月（-2 = 闰二月）。该约定**只存在于本模块内部**，
  向上一律暴露 (ISO 日期, is_leap_month) 对，不外泄到 schema、存储与前端。
- 人读文本由前端经 lunar 库渲染，original_text 存录入原文。
"""

from __future__ import annotations

from typing import Any

from lunar_python import Lunar, Solar  # type: ignore[import-untyped]


def solar_to_lunar(date_iso: str) -> tuple[str, bool] | None:
    """ISO 公历 → (农历 ISO 'YYYY-MM-DD', 是否闰月)。失败返回 None。

    lunar-python 的负数月份在此归一为正月份 + is_leap_month 标记。
    """
    try:
        y, m, d = (int(x) for x in date_iso.split("-"))
        lunar = Solar.fromYmd(y, m, d).getLunar()
        month = int(lunar.getMonth())
        is_leap = month < 0
        return (
            f"{int(lunar.getYear()):04d}-{abs(month):02d}-{int(lunar.getDay()):02d}",
            is_leap,
        )
    except Exception:  # noqa: BLE001 - 超范围/非法日期统一视为不可换算
        return None


def lunar_to_solar(date_iso: str, *, is_leap_month: bool = False) -> str | None:
    """农历 ISO 'YYYY-MM-DD' → ISO 公历。失败返回 None。

    is_leap_month 为真时按闰月换算（内部转成 lunar-python 的负数月份）。
    """
    try:
        parts = date_iso.split("-")
        if len(parts) != 3:
            return None
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        month = -m if is_leap_month else m
        return str(Lunar.fromYmd(y, month, d).getSolar().toYmd())
    except Exception:  # noqa: BLE001
        return None


def enrich_structured_date(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """为 birth/death 结构化值补 mirror_date（另一历），失败置 None 不报错。

    公历入：mirror_date = 农历 ISO，is_leap_month 由换算结果决定（描述 mirror_date）。
    农历入：mirror_date = 公历 ISO，is_leap_month 取调用方给的值（录入者裁定，描述 date）。
    """
    if not payload or payload.get("cal_type") == "none":
        return payload
    date = payload.get("date")
    if not date:
        return payload
    if payload["cal_type"] == "solar":
        converted = solar_to_lunar(date)
        if converted is None:
            return {**payload, "mirror_date": None}
        mirror, is_leap = converted
        return {**payload, "mirror_date": mirror, "is_leap_month": is_leap}
    is_leap_month = bool(payload.get("is_leap_month"))
    return {
        **payload,
        "mirror_date": lunar_to_solar(date, is_leap_month=is_leap_month),
        "is_leap_month": is_leap_month,
    }


__all__ = ["enrich_structured_date", "lunar_to_solar", "solar_to_lunar"]
