"""Calendar-driven demand features.

Everything here is deterministic from the date, so it is known perfectly
in advance — which is why the calendar is the backbone of long-range
planning, with weather layered on top.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

import numpy as np
import pandas as pd


def season_of(d: dt.date) -> int:
    """Season label = calendar year the season ends in (Nov 2026 -> 2027)."""
    return d.year + 1 if d.month >= 8 else d.year


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def easter(year: int) -> dt.date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


@lru_cache(maxsize=None)
def key_dates(season: int) -> dict[str, dt.date]:
    y0, y1 = season - 1, season
    thanksgiving = _nth_weekday(y0, 11, 3, 4)
    mlk = _nth_weekday(y1, 1, 0, 3)
    presidents = _nth_weekday(y1, 2, 0, 3)
    conf_sun = _nth_weekday(y1, 4, 6, 1)  # LDS General Conference (1st Sun of April)
    return {
        "thanksgiving": thanksgiving,
        "christmas": dt.date(y0, 12, 25),
        "new_year": dt.date(y1, 1, 1),
        "mlk": mlk,
        "presidents": presidents,
        "easter": easter(y1),
        "conference_sun": conf_sun,
        # Utah districts mostly take a week of spring break in late March /
        # early April; we centre a window on the last Monday of March.
        "ut_spring_break_start": _last_weekday(y1, 3, 0),
    }


def _in(d: dt.date, start: dt.date, end: dt.date) -> bool:
    return start <= d <= end


def day_flags(d: dt.date) -> dict:
    """Named calendar flags for one date (used for features and reporting)."""
    k = key_dates(season_of(d))
    td = dt.timedelta
    xmas_break = _in(d, dt.date(k["christmas"].year, 12, 21), dt.date(k["new_year"].year, 1, 2))
    flags = {
        "dow": d.weekday(),
        "is_saturday": d.weekday() == 5,
        "is_sunday": d.weekday() == 6,
        "thanksgiving_wknd": _in(d, k["thanksgiving"], k["thanksgiving"] + td(days=3)),
        "christmas_break": xmas_break,
        "peak_holiday_week": _in(d, dt.date(k["christmas"].year, 12, 26), dt.date(k["new_year"].year, 1, 1)),
        "christmas_day": d == k["christmas"],
        "mlk_wknd": _in(d, k["mlk"] - td(days=3), k["mlk"]),
        "presidents_wknd": _in(d, k["presidents"] - td(days=3), k["presidents"]),
        "presidents_week": _in(d, k["presidents"] - td(days=2), k["presidents"] + td(days=6)),
        "dest_spring_break": _in(d, dt.date(d.year, 3, 7), dt.date(d.year, 4, 5)) if d.month in (3, 4) else False,
        "ut_spring_break": _in(d, k["ut_spring_break_start"], k["ut_spring_break_start"] + td(days=4)),
        "easter_sunday": d == k["easter"],
        "conference_wknd": _in(d, k["conference_sun"] - td(days=1), k["conference_sun"]),
        "holiday_monday": d in (k["mlk"], k["presidents"]),
    }
    flags["holiday_period"] = any(
        flags[f] for f in ("thanksgiving_wknd", "christmas_break", "mlk_wknd", "presidents_wknd")
    )
    return flags


def destination_multiplier(d: dt.date) -> float:
    """Relative volume of out-of-state visitors on the mountain on date d."""
    f = day_flags(d)
    m, day = d.month, d.day
    if f["peak_holiday_week"]:
        return 2.3
    if f["christmas_break"]:
        return 1.6 if (m == 12) else 1.45
    if f["thanksgiving_wknd"]:
        return 1.2
    if f["presidents_wknd"]:
        return 1.8
    if f["presidents_week"]:
        return 1.5  # many East-coast schools take the whole week
    if f["mlk_wknd"]:
        return 1.4
    if f["dest_spring_break"]:
        return 1.35
    if m == 11 or (m == 12 and day < 13):
        return 0.45
    if m == 12:
        return 0.7
    if m == 1:
        return 0.75 if day < 16 else 0.95
    if m == 2:
        return 1.05
    if m == 3:
        return 1.2
    if m == 4:
        return 0.5 if day < 15 else 0.3
    return 0.15


def local_multiplier(cal: pd.DataFrame, seg: dict) -> np.ndarray:
    """Relative volume of local skiers (before weather) for each row of a
    calendar_frame()."""
    sat, sun = cal["is_saturday"].to_numpy(), cal["is_sunday"].to_numpy()
    brk = (cal["christmas_break"] | cal["thanksgiving_wknd"] | cal["presidents_wknd"]
           | cal["ut_spring_break"]).to_numpy()
    m = np.where(sat, seg["saturday"],
        np.where(sun, seg["sunday"],
        np.where(cal["holiday_monday"].to_numpy(), seg["sunday"] * 1.1,
        np.where(brk, seg["school_break"],
                 np.take(np.array(seg.get("weekday_factors", [1.0] * 5) + [1.0, 1.0]), cal["dow"].to_numpy())))))
    m = m * np.where(cal["christmas_break"].to_numpy() & (sat | sun), 1.1, 1.0)
    m = m * np.where(cal["christmas_day"].to_numpy(), 0.55, 1.0)
    m = m * np.where(cal["easter_sunday"].to_numpy(), 0.6, 1.0)      # Utah: family/church day
    m = m * np.where(cal["conference_wknd"].to_numpy(), 0.8, 1.0)    # LDS General Conference
    return m


def calendar_frame(dates: pd.DatetimeIndex) -> pd.DataFrame:
    rows = [day_flags(d.date()) for d in dates]
    df = pd.DataFrame(rows, index=dates)
    df["season"] = [season_of(d.date()) for d in dates]
    df["dest_mult"] = [destination_multiplier(d.date()) for d in dates]
    df["month"] = dates.month
    return df


def day_type(row) -> str:
    """Coarse label used for reporting / rules of thumb."""
    if row["peak_holiday_week"] or row["presidents_wknd"] or row["mlk_wknd"] or row["thanksgiving_wknd"]:
        return "holiday"
    if row["christmas_break"] and (row["is_saturday"] or row["is_sunday"]):
        return "holiday"
    if row["christmas_break"] or row["presidents_week"]:
        return "holiday-week weekday"
    if row["is_saturday"]:
        return "saturday"
    if row["is_sunday"]:
        return "sunday"
    return "weekday"


def season_dates(season: int, start_mmdd: str = "11-10", end_mmdd: str = "05-31") -> pd.DatetimeIndex:
    s = dt.date(season - 1, *map(int, start_mmdd.split("-")))
    e = dt.date(season, *map(int, end_mmdd.split("-")))
    return pd.date_range(s, e, freq="D")


__all__ = [
    "season_of", "key_dates", "day_flags", "destination_multiplier", "local_multiplier",
    "calendar_frame", "day_type", "season_dates", "easter",
]
