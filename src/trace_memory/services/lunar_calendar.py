"""Zero-dependency Chinese Lunar <-> Solar calendar conversion, 24 Solar Terms,
Traditional/International Festivals, and Statutory Holiday Auto-Sync engine (1900-2100).

Bitmask table verified day-by-day against ICU Chinese astronomical calendar (`Asia/Shanghai`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import json
import math
from pathlib import Path
import threading
from typing import Any
import urllib.error
import urllib.request


LUNAR_START_YEAR = 1900
LUNAR_END_YEAR = 2100
SOLAR_ANCHOR = date(1900, 1, 31)  # Lunar 1900-01-01

# 201 entries (1900..2100 inclusive):
# - bits 0..3   (info & 0xF): leap month (0 if no leap month, else 1..12)
# - bit 16      (info & 0x10000): leap month has 30 days if 1 else 29 days
# - bits 4..15  (0x8000 >> (m - 1)): regular month m (1..12) has 30 days if 1 else 29
LUNAR_INFO: tuple[int, ...] = (
    0x04bd8, 0x04ae0, 0x0a570, 0x054d5, 0x0d260, 0x0d950, 0x16554, 0x056a0, 0x09ad0, 0x055d2,
    0x04ae0, 0x0a5b6, 0x0a4d0, 0x0d250, 0x1d295, 0x0b550, 0x056a0, 0x0ada2, 0x095b0, 0x14977,
    0x049b0, 0x0a4b0, 0x0b4b5, 0x06a50, 0x06d40, 0x1ab54, 0x02b60, 0x09570, 0x052f2, 0x04970,
    0x06566, 0x0d4a0, 0x0ea50, 0x16a95, 0x05ad0, 0x02b60, 0x186e3, 0x092e0, 0x1c8d7, 0x0c950,
    0x0d4a0, 0x1d8a6, 0x0b550, 0x056a0, 0x1a5b4, 0x025d0, 0x092d0, 0x0d2b2, 0x0a950, 0x0b557,
    0x06ca0, 0x0b550, 0x15355, 0x04da0, 0x0a5b0, 0x14573, 0x052b0, 0x0a9a8, 0x0e950, 0x06aa0,
    0x0aea6, 0x0ab50, 0x04b60, 0x0aae4, 0x0a570, 0x05260, 0x0f263, 0x0d950, 0x05b57, 0x056a0,
    0x096d0, 0x04dd5, 0x04ad0, 0x0a4d0, 0x0d4d4, 0x0d250, 0x0d558, 0x0b540, 0x0b6a0, 0x195a6,
    0x095b0, 0x049b0, 0x0a974, 0x0a4b0, 0x0b27a, 0x06a50, 0x06d40, 0x0af46, 0x0ab60, 0x09570,
    0x04af5, 0x04970, 0x064b0, 0x074a3, 0x0ea50, 0x06b58, 0x05ac0, 0x0ab60, 0x096d5, 0x092e0,
    0x0c960, 0x0d954, 0x0d4a0, 0x0da50, 0x07552, 0x056a0, 0x0abb7, 0x025d0, 0x092d0, 0x0cab5,
    0x0a950, 0x0b4a0, 0x0baa4, 0x0ad50, 0x055d9, 0x04ba0, 0x0a5b0, 0x15176, 0x052b0, 0x0a930,
    0x07954, 0x06aa0, 0x0ad50, 0x05b52, 0x04b60, 0x0a6e6, 0x0a4e0, 0x0d260, 0x0ea65, 0x0d530,
    0x05aa0, 0x076a3, 0x096d0, 0x04afb, 0x04ad0, 0x0a4d0, 0x1d0b6, 0x0d250, 0x0d520, 0x0dd45,
    0x0b5a0, 0x056d0, 0x055b2, 0x049b0, 0x0a577, 0x0a4b0, 0x0aa50, 0x1b255, 0x06d20, 0x0ada0,
    0x14b63, 0x09370, 0x049f8, 0x04970, 0x064b0, 0x168a6, 0x0ea50, 0x06aa0, 0x1a6c4, 0x0aae0,
    0x092e0, 0x0d2e3, 0x0c960, 0x0d557, 0x0d4a0, 0x0da50, 0x05d55, 0x056a0, 0x0a6d0, 0x055d4,
    0x052d0, 0x0a9b8, 0x0a950, 0x0b4a0, 0x0b6a6, 0x0ad50, 0x055a0, 0x0aba4, 0x0a5b0, 0x052b0,
    0x0b273, 0x06930, 0x07337, 0x06aa0, 0x0ad50, 0x14b55, 0x04b60, 0x0a570, 0x054e4, 0x0d160,
    0x0e968, 0x0d520, 0x0daa0, 0x16aa6, 0x056d0, 0x04ae0, 0x0a9d4, 0x0a2d0, 0x0d150, 0x0f252,
    0x0d520,
)

LUNAR_MONTH_NAMES = (
    "正月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "冬月", "腊月",
)

LUNAR_DAY_NAMES = (
    "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
)

HEAVENLY_STEMS = ("甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸")
EARTHLY_BRANCHES = ("子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥")
ZODIAC_ANIMALS = ("鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪")


@dataclass(frozen=True)
class LunarDate:
    year: int
    month: int
    day: int
    is_leap_month: bool = False

    @property
    def month_cn(self) -> str:
        base = LUNAR_MONTH_NAMES[max(1, min(12, self.month)) - 1]
        return f"闰{base}" if self.is_leap_month else base

    @property
    def day_cn(self) -> str:
        return LUNAR_DAY_NAMES[max(1, min(30, self.day)) - 1]

    @property
    def ganzhi_year(self) -> str:
        idx = (self.year - 4) % 60
        return f"{HEAVENLY_STEMS[idx % 10]}{EARTHLY_BRANCHES[idx % 12]}"

    @property
    def zodiac(self) -> str:
        return ZODIAC_ANIMALS[(self.year - 4) % 12]

    @property
    def short_label(self) -> str:
        return f"农历{self.month_cn}{self.day_cn}"

    @property
    def full_label(self) -> str:
        return f"{self.ganzhi_year}{self.zodiac}年 · 农历{self.month_cn}{self.day_cn}"


def _leap_month(year: int) -> int:
    return LUNAR_INFO[year - LUNAR_START_YEAR] & 0xF


def _leap_days(year: int) -> int:
    if _leap_month(year):
        return 30 if (LUNAR_INFO[year - LUNAR_START_YEAR] & 0x10000) else 29
    return 0


def _month_days(year: int, month: int) -> int:
    return 30 if (LUNAR_INFO[year - LUNAR_START_YEAR] & (0x8000 >> (month - 1))) else 29


def _year_days(year: int) -> int:
    info = LUNAR_INFO[year - LUNAR_START_YEAR]
    total = 29 * 12
    mask = 0x8000
    while mask >= 0x8:
        if mask >= 0x10 and (info & mask):
            total += 1
        mask >>= 1
    return total + _leap_days(year)


def solar_to_lunar(solar: date) -> LunarDate:
    """Convert Gregorian `date` (1900-01-31 .. 2100-12-31) to `LunarDate`."""
    offset = (solar - SOLAR_ANCHOR).days
    if offset < 0:
        return LunarDate(year=solar.year, month=solar.month, day=min(30, solar.day), is_leap_month=False)

    year = LUNAR_START_YEAR
    while year <= LUNAR_END_YEAR:
        yd = _year_days(year)
        if offset < yd:
            break
        offset -= yd
        year += 1

    if year > LUNAR_END_YEAR:
        return LunarDate(year=solar.year, month=solar.month, day=min(30, solar.day), is_leap_month=False)

    leap = _leap_month(year)
    is_leap = False
    month = 1
    while month <= 12:
        md = _leap_days(year) if (is_leap and leap == month) else _month_days(year, month)
        if offset < md:
            break
        offset -= md
        if leap == month and not is_leap:
            is_leap = True
        else:
            if is_leap:
                is_leap = False
            month += 1

    return LunarDate(year=year, month=min(12, month), day=offset + 1, is_leap_month=is_leap)


def lunar_to_solar(
    lunar_year: int,
    lunar_month: int,
    lunar_day: int,
    is_leap_month: bool = False,
) -> date:
    """Convert `LunarDate` to Gregorian `date` (gracefully clamping 30->29 or missing leap month)."""
    yr = max(LUNAR_START_YEAR, min(LUNAR_END_YEAR, int(lunar_year)))
    mo = max(1, min(12, int(lunar_month)))
    dy = max(1, min(30, int(lunar_day)))

    offset = 0
    for y in range(LUNAR_START_YEAR, yr):
        offset += _year_days(y)

    leap = _leap_month(yr)
    eff_leap = bool(is_leap_month and leap == mo)

    for m in range(1, mo):
        offset += _month_days(yr, m)
        if leap == m:
            offset += _leap_days(yr)

    if eff_leap:
        offset += _month_days(yr, mo)
        max_d = _leap_days(yr)
    else:
        max_d = _month_days(yr, mo)

    clamped_day = min(dy, max_d)
    offset += clamped_day - 1
    return SOLAR_ANCHOR + timedelta(days=offset)


def next_lunar_occurrence(
    lunar_month: int,
    lunar_day: int,
    ref_today: date,
    is_leap_month: bool = False,
) -> date:
    """Find the earliest solar date >= `ref_today` matching `(lunar_month, lunar_day)`."""
    candidates: list[date] = []
    for cand_year in (ref_today.year - 1, ref_today.year, ref_today.year + 1, ref_today.year + 2):
        if LUNAR_START_YEAR <= cand_year <= LUNAR_END_YEAR:
            s_date = lunar_to_solar(cand_year, lunar_month, lunar_day, is_leap_month=is_leap_month)
            if s_date >= ref_today:
                candidates.append(s_date)
    if not candidates:
        return lunar_to_solar(min(LUNAR_END_YEAR, ref_today.year), lunar_month, lunar_day, is_leap_month=False)
    return min(candidates)


# ----------------------------------------------------------------------
# 24 Solar Terms (Jean Meeus astronomical solar ecliptic longitude, UTC+8)
# ----------------------------------------------------------------------

BEIJING_TZ_OFFSET = timezone(timedelta(hours=8))

SOLAR_TERMS: tuple[str, ...] = (
    "小寒", "大寒", "立春", "雨水", "惊蛰", "春分",
    "清明", "谷雨", "立夏", "小满", "芒种", "夏至",
    "小暑", "大暑", "立秋", "处暑", "白露", "秋分",
    "寒露", "霜降", "立冬", "小雪", "大雪", "冬至",
)

# Apparent ecliptic longitudes: 小寒=285°, 大寒=300°, ..., 春分=0°, ..., 冬至=270°
_SOLAR_TERM_LONGITUDES: tuple[int, ...] = tuple((285 + 15 * i) % 360 for i in range(24))


def _sun_apparent_longitude_deg(dt_bj: datetime) -> float:
    """Compute Sun's apparent geocentric ecliptic longitude (degrees, accuracy ~0.01 deg)."""
    dt_utc = dt_bj.astimezone(timezone.utc)
    y, m, d = dt_utc.year, dt_utc.month, dt_utc.day
    day_frac = d + (dt_utc.hour + (dt_utc.minute + dt_utc.second / 60.0) / 60.0) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a_century = y // 100
    b_greg = 2 - a_century + a_century // 4
    jd = int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + day_frac + b_greg - 1524.5
    # Julian centuries from J2000.0 in Dynamical Time (TT approx UTC + 69s for modern era)
    t = (jd + 69.0 / 86400.0 - 2451545.0) / 36525.0
    l0 = (280.46646 + 36000.76983 * t + 0.0003032 * t * t) % 360.0
    m_anom = math.radians((357.52911 + 35999.05029 * t - 0.0001537 * t * t) % 360.0)
    center = (
        (1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(m_anom)
        + (0.019993 - 0.000101 * t) * math.sin(2.0 * m_anom)
        + 0.000289 * math.sin(3.0 * m_anom)
    )
    true_lon = l0 + center
    omega = math.radians(125.04 - 1934.136 * t)
    app_lon = (true_lon - 0.00569 - 0.00478 * math.sin(omega)) % 360.0
    return app_lon


@lru_cache(maxsize=64)
def get_solar_terms_for_year(year: int) -> dict[date, str]:
    """Return mapping `{solar_date: term_name}` for all 24 solar terms in `year` (Beijing Time)."""
    out: dict[date, str] = {}
    for idx, term_name in enumerate(SOLAR_TERMS):
        target = _SOLAR_TERM_LONGITUDES[idx]
        month = (idx // 2) + 1
        d_guess = 5 if idx % 2 == 0 else 20
        center_dt = datetime(year, month, d_guess, 12, 0, tzinfo=BEIJING_TZ_OFFSET)
        lo = center_dt - timedelta(days=5)
        hi = center_dt + timedelta(days=5)
        for _ in range(32):
            mid = lo + (hi - lo) / 2
            diff = (_sun_apparent_longitude_deg(mid) - target + 180.0) % 360.0 - 180.0
            if diff < 0.0:
                lo = mid
            else:
                hi = mid
        out[hi.date()] = term_name
    return out


def get_solar_term(solar: date) -> str:
    """Return the 24 Solar Term name on `solar` (e.g. `'清明'`, `'冬至'`), or `''`."""
    if solar.year < 1850 or solar.year > 2150:
        return ""
    return get_solar_terms_for_year(solar.year).get(solar, "")


# ----------------------------------------------------------------------
# Traditional Lunar & Solar / International Festivals
# ----------------------------------------------------------------------

TRADITIONAL_LUNAR_FESTIVALS: dict[tuple[int, int], str] = {
    (1, 1): "春节",
    (1, 15): "元宵节",
    (2, 2): "龙抬头",
    (5, 5): "端午节",
    (7, 7): "七夕",
    (7, 15): "中元节",
    (8, 15): "中秋节",
    (9, 9): "重阳节",
    (12, 8): "腊八节",
    (12, 23): "小年",
}

MAJOR_LUNAR_SET = frozenset({"除夕", "春节", "元宵节", "端午节", "中秋节", "重阳节"})

MAJOR_SOLAR_FESTIVALS: dict[tuple[int, int], str] = {
    (1, 1): "元旦",
    (5, 1): "劳动节",
    (10, 1): "国庆节",
}

MINOR_SOLAR_FESTIVALS: dict[tuple[int, int], str] = {
    (2, 14): "情人节",
    (3, 8): "妇女节",
    (3, 12): "植树节",
    (4, 1): "愚人节",
    (5, 4): "青年节",
    (6, 1): "儿童节",
    (7, 1): "建党节",
    (8, 1): "建军节",
    (9, 10): "教师节",
    (10, 31): "万圣夜",
    (12, 24): "平安夜",
    (12, 25): "圣诞节",
}


def get_festivals(
    solar: date,
    lunar: LunarDate | None = None,
    solar_term: str | None = None,
) -> tuple[list[str], list[str]]:
    """Return `(major_festivals, minor_festivals)` for Gregorian `solar`."""
    lu = lunar if lunar is not None else solar_to_lunar(solar)
    st = solar_term if solar_term is not None else get_solar_term(solar)

    major: list[str] = []
    minor: list[str] = []

    if (solar.month, solar.day) in MAJOR_SOLAR_FESTIVALS:
        major.append(MAJOR_SOLAR_FESTIVALS[(solar.month, solar.day)])

    if st == "清明":
        major.append("清明节")

    if not lu.is_leap_month:
        lu_name = TRADITIONAL_LUNAR_FESTIVALS.get((lu.month, lu.day))
        if lu_name:
            if lu_name in MAJOR_LUNAR_SET:
                major.append(lu_name)
            else:
                minor.append(lu_name)
        # Dynamic Chinese New Year's Eve (supports both 29-day and 30-day 12th lunar month)
        if lu.month == 12 and lu.day >= 29:
            tom_lu = solar_to_lunar(solar + timedelta(days=1))
            if tom_lu.month == 1 and tom_lu.day == 1 and not tom_lu.is_leap_month:
                major.append("除夕")

    if (solar.month, solar.day) in MINOR_SOLAR_FESTIVALS:
        minor.append(MINOR_SOLAR_FESTIVALS[(solar.month, solar.day)])

    # Dynamic weekday-based international observances
    if solar.month == 5 and solar.weekday() == 6 and 8 <= solar.day <= 14:
        minor.append("母亲节")
    elif solar.month == 6 and solar.weekday() == 6 and 15 <= solar.day <= 21:
        minor.append("父亲节")
    elif solar.month == 11 and solar.weekday() == 3 and 22 <= solar.day <= 28:
        minor.append("感恩节")

    return major, minor


# ----------------------------------------------------------------------
# Statutory Chinese Holiday & Make-up Workday Schedule + Monthly Auto-Sync
# ----------------------------------------------------------------------

BUILTIN_CN_HOLIDAYS: dict[str, tuple[str, str]] = {
    # === 2024 State Council Official Schedule ===
    "2024-01-01": ("rest", "元旦"),
    "2024-02-04": ("work", "春节"),
    "2024-02-10": ("rest", "春节"),
    "2024-02-11": ("rest", "春节"),
    "2024-02-12": ("rest", "春节"),
    "2024-02-13": ("rest", "春节"),
    "2024-02-14": ("rest", "春节"),
    "2024-02-15": ("rest", "春节"),
    "2024-02-16": ("rest", "春节"),
    "2024-02-17": ("rest", "春节"),
    "2024-02-18": ("work", "春节"),
    "2024-04-04": ("rest", "清明节"),
    "2024-04-05": ("rest", "清明节"),
    "2024-04-06": ("rest", "清明节"),
    "2024-04-07": ("work", "清明节"),
    "2024-04-28": ("work", "劳动节"),
    "2024-05-01": ("rest", "劳动节"),
    "2024-05-02": ("rest", "劳动节"),
    "2024-05-03": ("rest", "劳动节"),
    "2024-05-04": ("rest", "劳动节"),
    "2024-05-05": ("rest", "劳动节"),
    "2024-05-11": ("work", "劳动节"),
    "2024-06-10": ("rest", "端午节"),
    "2024-09-14": ("work", "中秋节"),
    "2024-09-15": ("rest", "中秋节"),
    "2024-09-16": ("rest", "中秋节"),
    "2024-09-17": ("rest", "中秋节"),
    "2024-09-29": ("work", "国庆节"),
    "2024-10-01": ("rest", "国庆节"),
    "2024-10-02": ("rest", "国庆节"),
    "2024-10-03": ("rest", "国庆节"),
    "2024-10-04": ("rest", "国庆节"),
    "2024-10-05": ("rest", "国庆节"),
    "2024-10-06": ("rest", "国庆节"),
    "2024-10-07": ("rest", "国庆节"),
    "2024-10-12": ("work", "国庆节"),
    # === 2025 State Council Official Schedule ===
    "2025-01-01": ("rest", "元旦"),
    "2025-01-26": ("work", "春节"),
    "2025-01-28": ("rest", "春节"),
    "2025-01-29": ("rest", "春节"),
    "2025-01-30": ("rest", "春节"),
    "2025-01-31": ("rest", "春节"),
    "2025-02-01": ("rest", "春节"),
    "2025-02-02": ("rest", "春节"),
    "2025-02-03": ("rest", "春节"),
    "2025-02-04": ("rest", "春节"),
    "2025-02-08": ("work", "春节"),
    "2025-04-04": ("rest", "清明节"),
    "2025-04-05": ("rest", "清明节"),
    "2025-04-06": ("rest", "清明节"),
    "2025-04-27": ("work", "劳动节"),
    "2025-05-01": ("rest", "劳动节"),
    "2025-05-02": ("rest", "劳动节"),
    "2025-05-03": ("rest", "劳动节"),
    "2025-05-04": ("rest", "劳动节"),
    "2025-05-05": ("rest", "劳动节"),
    "2025-05-31": ("rest", "端午节"),
    "2025-06-01": ("rest", "端午节"),
    "2025-06-02": ("rest", "端午节"),
    "2025-09-28": ("work", "国庆节、中秋节"),
    "2025-10-01": ("rest", "国庆节、中秋节"),
    "2025-10-02": ("rest", "国庆节、中秋节"),
    "2025-10-03": ("rest", "国庆节、中秋节"),
    "2025-10-04": ("rest", "国庆节、中秋节"),
    "2025-10-05": ("rest", "国庆节、中秋节"),
    "2025-10-06": ("rest", "国庆节、中秋节"),
    "2025-10-07": ("rest", "国庆节、中秋节"),
    "2025-10-08": ("rest", "国庆节、中秋节"),
    "2025-10-11": ("work", "国庆节、中秋节"),
    # === 2026 State Council Official Schedule ===
    "2026-01-01": ("rest", "元旦"),
    "2026-01-02": ("rest", "元旦"),
    "2026-01-03": ("rest", "元旦"),
    "2026-01-04": ("work", "元旦"),
    "2026-02-14": ("work", "春节"),
    "2026-02-15": ("rest", "春节"),
    "2026-02-16": ("rest", "春节"),
    "2026-02-17": ("rest", "春节"),
    "2026-02-18": ("rest", "春节"),
    "2026-02-19": ("rest", "春节"),
    "2026-02-20": ("rest", "春节"),
    "2026-02-21": ("rest", "春节"),
    "2026-02-22": ("rest", "春节"),
    "2026-02-23": ("rest", "春节"),
    "2026-02-28": ("work", "春节"),
    "2026-04-04": ("rest", "清明节"),
    "2026-04-05": ("rest", "清明节"),
    "2026-04-06": ("rest", "清明节"),
    "2026-05-01": ("rest", "劳动节"),
    "2026-05-02": ("rest", "劳动节"),
    "2026-05-03": ("rest", "劳动节"),
    "2026-05-04": ("rest", "劳动节"),
    "2026-05-05": ("rest", "劳动节"),
    "2026-05-09": ("work", "劳动节"),
    "2026-06-19": ("rest", "端午节"),
    "2026-06-20": ("rest", "端午节"),
    "2026-06-21": ("rest", "端午节"),
    "2026-09-20": ("work", "国庆节"),
    "2026-09-25": ("rest", "中秋节"),
    "2026-09-26": ("rest", "中秋节"),
    "2026-09-27": ("rest", "中秋节"),
    "2026-10-01": ("rest", "国庆节"),
    "2026-10-02": ("rest", "国庆节"),
    "2026-10-03": ("rest", "国庆节"),
    "2026-10-04": ("rest", "国庆节"),
    "2026-10-05": ("rest", "国庆节"),
    "2026-10-06": ("rest", "国庆节"),
    "2026-10-07": ("rest", "国庆节"),
    "2026-10-10": ("work", "国庆节"),
}

_BUILTIN_YEARS: frozenset[int] = frozenset(int(k[:4]) for k in BUILTIN_CN_HOLIDAYS)

DEFAULT_HOLIDAY_SOURCES: tuple[tuple[str, str], ...] = (
    ("holiday-cn-jsdelivr", "https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json"),
    ("holiday-cn-fastly", "https://fastly.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json"),
    ("holiday-cn-github", "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"),
    ("timor-holiday", "https://timor.tech/api/holiday/year/{year}"),
)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{threading.get_ident()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _parse_remote_holiday_payload(source_name: str, year: int, raw_text: str) -> dict[str, dict[str, str]]:
    """Parse either `NateScarlet/holiday-cn` or `timor.tech` JSON into `{YYYY-MM-DD: {status, name}}`."""
    data = json.loads(raw_text)
    entries: dict[str, dict[str, str]] = {}

    if isinstance(data, dict) and isinstance(data.get("days"), list):
        for item in data["days"]:
            if not isinstance(item, dict):
                continue
            ymd = str(item.get("date") or "").strip()[:10]
            if len(ymd) != 10 or not ymd.startswith(f"{year}-"):
                continue
            status = "rest" if bool(item.get("isOffDay")) else "work"
            name = str(item.get("name") or "").strip()
            entries[ymd] = {"status": status, "name": name}
        return entries

    if isinstance(data, dict) and isinstance(data.get("holiday"), dict):
        for key, item in data["holiday"].items():
            if not isinstance(item, dict):
                continue
            raw_d = str(item.get("date") or "").strip()[:10]
            if not raw_d and len(str(key)) == 5:
                raw_d = f"{year}-{key}"
            if len(raw_d) != 10 or not raw_d.startswith(f"{year}-"):
                continue
            status = "rest" if bool(item.get("holiday")) else "work"
            name = str(item.get("name") or "").strip()
            entries[raw_d] = {"status": status, "name": name}
        return entries

    raise ValueError(f"Unrecognized holiday JSON schema from {source_name} for {year}")


class HolidayScheduleManager:
    """Thread-safe statutory holiday & make-up workday store with monthly remote auto-sync."""

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        sources: tuple[tuple[str, str], ...] = DEFAULT_HOLIDAY_SOURCES,
        http_fetcher: Any = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.cache_path = (self.data_dir / "holidays_cache.json") if self.data_dir is not None else None
        self.override_path = (self.data_dir / "holidays_override.json") if self.data_dir is not None else None
        self.sources = sources
        self._http_fetcher = http_fetcher
        self._lock = threading.Lock()
        self._cache_years: dict[str, dict[str, dict[str, str]]] = {}
        self._last_synced_at: str = ""
        self._last_synced_month: str = ""
        self._last_source: str = "builtin"
        self._last_unpublished_years: list[int] = []
        self._override_entries: dict[str, tuple[str, str]] = {}
        self._override_mtime: float = -1.0
        self._load_disk_files()

    def _load_disk_files(self) -> None:
        if self.cache_path is not None and self.cache_path.is_file():
            try:
                raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    yrs = raw.get("years")
                    if isinstance(yrs, dict):
                        self._cache_years = {
                            str(y): {
                                str(d): {
                                    "status": str(v.get("status") or ""),
                                    "name": str(v.get("name") or ""),
                                }
                                for d, v in m.items()
                                if isinstance(v, dict) and v.get("status") in ("rest", "work")
                            }
                            for y, m in yrs.items()
                            if isinstance(m, dict)
                        }
                    self._last_synced_at = str(raw.get("last_synced_at") or "")
                    self._last_synced_month = str(raw.get("last_synced_month") or "")
                    self._last_source = str(raw.get("source") or "cache")
                    unpub = raw.get("unpublished_years") or []
                    self._last_unpublished_years = [int(x) for x in unpub if str(x).isdigit()]
            except Exception:
                pass
        self._reload_override_if_changed()

    def _reload_override_if_changed(self) -> None:
        if self.override_path is None:
            return
        if not self.override_path.is_file():
            self._override_entries = {}
            self._override_mtime = -1.0
            return
        try:
            mtime = self.override_path.stat().st_mtime
            if mtime == self._override_mtime:
                return
            raw = json.loads(self.override_path.read_text(encoding="utf-8"))
            parsed: dict[str, tuple[str, str]] = {}
            if isinstance(raw, dict):
                source_map = raw.get("days") if isinstance(raw.get("days"), dict) else raw
                if isinstance(source_map, dict):
                    for k, v in source_map.items():
                        ymd = str(k).strip()[:10]
                        if len(ymd) != 10 or not isinstance(v, dict):
                            continue
                        st = str(v.get("status") or ("rest" if v.get("isOffDay") else "")).strip()
                        if st not in ("rest", "work"):
                            continue
                        parsed[ymd] = (st, str(v.get("name") or "").strip())
            self._override_entries = parsed
            self._override_mtime = mtime
        except Exception:
            pass

    def _default_fetch(self, url: str, timeout: float = 8.0) -> tuple[int, str]:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 TraceTodo-HolidaySync/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return int(getattr(resp, "status", 200)), resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return int(exc.code), ""

    def get_holiday_info(self, solar: date) -> tuple[str, str]:
        """Return `(status, holiday_name)` where status is `'' | 'rest' | 'work'`."""
        ymd = solar.isoformat()
        yr_str = str(solar.year)

        with self._lock:
            self._reload_override_if_changed()
            if ymd in self._override_entries:
                return self._override_entries[ymd]
            year_map = self._cache_years.get(yr_str)
            if year_map is not None:
                hit = year_map.get(ymd)
                if hit is not None:
                    return hit["status"], hit["name"]
                return "", ""

        if solar.year in _BUILTIN_YEARS:
            return BUILTIN_CN_HOLIDAYS.get(ymd, ("", ""))

        # Graceful statutory anchor fallback for far-future unannounced years
        lu = solar_to_lunar(solar)
        st = get_solar_term(solar)
        if (solar.month, solar.day) == (1, 1):
            return "rest", "元旦"
        if (solar.month, solar.day) == (5, 1):
            return "rest", "劳动节"
        if solar.month == 10 and solar.day in (1, 2, 3):
            return "rest", "国庆节"
        if st == "清明":
            return "rest", "清明节"
        if not lu.is_leap_month:
            if lu.month == 1 and lu.day in (1, 2, 3):
                return "rest", "春节"
            if (lu.month, lu.day) == (5, 5):
                return "rest", "端午节"
            if (lu.month, lu.day) == (8, 15):
                return "rest", "中秋节"
        return "", ""

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            cached_years = sorted(int(y) for y in self._cache_years.keys() if y.isdigit())
            return {
                "last_synced_at": self._last_synced_at,
                "last_synced_month": self._last_synced_month,
                "source": self._last_source,
                "cached_years": cached_years,
                "builtin_years": sorted(_BUILTIN_YEARS),
                "unpublished_years": list(self._last_unpublished_years),
                "has_override": bool(self._override_entries),
            }

    def sync_remote_years(
        self,
        years: list[int] | None = None,
        *,
        force: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Fetch latest official CN holiday schedules from configured mirrors and persist locally."""
        ref_now = (now or datetime.now(BEIJING_TZ_OFFSET)).astimezone(BEIJING_TZ_OFFSET)
        cur_ym = ref_now.strftime("%Y-%m")
        target_years = sorted(set(years or [ref_now.year, ref_now.year + 1]))
        fetcher = self._http_fetcher or self._default_fetch

        if not force:
            with self._lock:
                already_month = self._last_synced_month == cur_ym
                has_cur = str(ref_now.year) in self._cache_years
                if already_month and has_cur:
                    return {
                        "skipped": True,
                        "updated": False,
                        "reason": "already_synced_this_month",
                        "last_synced_month": self._last_synced_month,
                        "cached_years": sorted(int(y) for y in self._cache_years.keys() if y.isdigit()),
                    }

        fetched_years: dict[str, dict[str, dict[str, str]]] = {}
        unpublished: list[int] = []
        errors: list[str] = []
        used_sources: list[str] = []

        for yr in target_years:
            yr_parsed: dict[str, dict[str, str]] | None = None
            yr_src = ""
            saw_404 = False
            for src_name, url_tpl in self.sources:
                url = url_tpl.format(year=yr)
                try:
                    status_code, body = fetcher(url, 8.0)
                    if status_code == 404:
                        saw_404 = True
                        continue
                    if 200 <= status_code < 300 and body:
                        parsed = _parse_remote_holiday_payload(src_name, yr, body)
                        if parsed:
                            yr_parsed = parsed
                            yr_src = src_name
                            break
                except Exception as exc:
                    errors.append(f"{yr}@{src_name}: {str(exc)[:80]}")
                    continue
            if yr_parsed is not None:
                fetched_years[str(yr)] = yr_parsed
                used_sources.append(f"{yr}:{yr_src}")
            elif saw_404:
                unpublished.append(yr)
            else:
                errors.append(f"{yr}: all mirrors unavailable")

        updated_changed = False
        now_iso = ref_now.replace(microsecond=0).isoformat()
        with self._lock:
            for yr_k, entries in fetched_years.items():
                if self._cache_years.get(yr_k) != entries:
                    updated_changed = True
                self._cache_years[yr_k] = entries
            self._last_synced_at = now_iso
            if fetched_years:
                self._last_synced_month = cur_ym
            if used_sources:
                self._last_source = ", ".join(used_sources)
            self._last_unpublished_years = unpublished

            if self.cache_path is not None and fetched_years:
                payload = {
                    "last_synced_at": self._last_synced_at,
                    "last_synced_month": self._last_synced_month,
                    "source": self._last_source,
                    "unpublished_years": self._last_unpublished_years,
                    "years": self._cache_years,
                }
                try:
                    _atomic_write_json(self.cache_path, payload)
                except Exception as exc:
                    errors.append(f"write_cache: {str(exc)[:80]}")

            cached_years = sorted(int(y) for y in self._cache_years.keys() if y.isdigit())

        return {
            "skipped": False,
            "updated": updated_changed,
            "synced_years": sorted(int(y) for y in fetched_years.keys()),
            "unpublished_years": unpublished,
            "cached_years": cached_years,
            "last_synced_month": cur_ym,
            "last_synced_at": now_iso,
            "source": ", ".join(used_sources) if used_sources else self._last_source,
            "errors": errors,
        }

    def maybe_monthly_sync(self, now: datetime | None = None) -> dict[str, Any]:
        """Trigger remote holiday sync once per Beijing calendar month (and weekly in Q4 if next year missing)."""
        ref_now = (now or datetime.now(BEIJING_TZ_OFFSET)).astimezone(BEIJING_TZ_OFFSET)
        cur_ym = ref_now.strftime("%Y-%m")
        with self._lock:
            last_ym = self._last_synced_month
            last_at_raw = self._last_synced_at
            has_cur = str(ref_now.year) in self._cache_years
            has_next = str(ref_now.year + 1) in self._cache_years
            cache_missing = self.cache_path is not None and not self.cache_path.is_file()

        due_monthly = (last_ym != cur_ym) or not has_cur or cache_missing
        due_q4_retry = False
        if not due_monthly and ref_now.month >= 10 and not has_next:
            try:
                last_dt = datetime.fromisoformat(last_at_raw) if last_at_raw else None
            except ValueError:
                last_dt = None
            if last_dt is None or (ref_now - last_dt) >= timedelta(days=7):
                due_q4_retry = True

        if not due_monthly and not due_q4_retry:
            return {
                "skipped": True,
                "updated": False,
                "last_synced_month": last_ym,
                "cached_years": sorted(int(y) for y in self._cache_years.keys() if y.isdigit()),
            }

        return self.sync_remote_years(
            years=[ref_now.year, ref_now.year + 1],
            force=True,
            now=ref_now,
        )


def _compact_festival_label(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    cleaned: list[str] = []
    for n in names[:2]:
        short = n[:-1] if len(n) >= 3 and n.endswith("节") else n
        if short not in cleaned:
            cleaned.append(short)
    return "·".join(cleaned)


def describe_calendar_day(
    solar: date,
    holiday_mgr: HolidayScheduleManager | None = None,
) -> dict[str, Any]:
    """Build complete Lunar + 24 Solar Term + Festival + Statutory Holiday metadata for `solar`."""
    lu = solar_to_lunar(solar)
    st = get_solar_term(solar)
    major_fests, minor_fests = get_festivals(solar, lunar=lu, solar_term=st)

    all_festivals: list[str] = []
    for item in [*major_fests, *([st] if st and st not in major_fests and st != "清明" else []), *minor_fests]:
        if item and item not in all_festivals:
            all_festivals.append(item)

    if major_fests:
        sub_label = _compact_festival_label(major_fests)
        sub_kind = "festival"
    elif st:
        sub_label = st
        sub_kind = "solar_term"
    elif minor_fests:
        sub_label = _compact_festival_label(minor_fests)
        sub_kind = "festival_minor"
    elif lu.day == 1:
        sub_label = lu.month_cn
        sub_kind = "lunar_month"
    else:
        sub_label = lu.day_cn
        sub_kind = "lunar_day"

    hol_mgr = holiday_mgr if holiday_mgr is not None else HolidayScheduleManager()
    hol_status, hol_name = hol_mgr.get_holiday_info(solar)

    return {
        "lunar_month_cn": lu.month_cn,
        "lunar_day_cn": lu.day_cn,
        "lunar_short": f"{lu.month_cn}{lu.day_cn}",
        "lunar_full": lu.full_label,
        "solar_term": st,
        "festivals": major_fests + minor_fests,
        "calendar_sub_label": sub_label,
        "calendar_sub_kind": sub_kind,
        "holiday_status": hol_status,
        "holiday_name": hol_name,
    }
