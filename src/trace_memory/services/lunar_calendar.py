"""Zero-dependency Chinese Lunar <-> Solar calendar conversion engine (1900-2100).

Bitmask table verified day-by-day against ICU Chinese astronomical calendar (`Asia/Shanghai`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


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
