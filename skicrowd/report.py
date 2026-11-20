"""Markdown backtest report + HTML crowd calendar."""
from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

import pandas as pd

from .config import ikon_resorts


def _md(df: pd.DataFrame) -> str:
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [" / ".join(map(str, c)) for c in df.columns]
    idx_name = df.index.name or ""
    cols = [idx_name] + [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for i, row in df.iterrows():
        label = i.strftime("%a %Y-%m-%d") if isinstance(i, pd.Timestamp) else str(i)
        vals = []
        for v in row:
            if isinstance(v, float):
                vals.append("" if v != v else f"{v:.0f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join([label] + vals) + " |")
    return "\n".join(lines)


def write_backtest_report(ins: dict, cfg: dict, seasons: list[int], path: Path) -> Path:
    s0, s1 = seasons[0], seasons[-1]
    parts = [
        f"# Utah Ikon crowd backtest: {s0 - 1}-{str(s0)[2:]} to {s1 - 1}-{str(s1)[2:]}",
        "",
        f"_Generated {dt.date.today():%Y-%m-%d}. Snow and temperature are **measured** SNOTEL history; "
        "the crowd numbers are **modelled** from those conditions plus the calendar, using the parameters in "
        "`skicrowd/config/model.toml` (or `data/calibrated_params.toml` if you've calibrated)._",
        "",
        "**Crowd index**: 100 = the resort is at its comfortable capacity. Below 50 is quiet, 50-80 moderate, "
        "80-110 busy, 110+ packed. 'Core season' means days when all six Ikon resorts were open.",
        "",
        "## Overall", _md(ins["overall"]), "",
        "## Average crowd index by day type", _md(ins["crowd_by_daytype"]), "",
        "## How often each resort was the least busy (% of days of that type)",
        _md(ins["least_busy_share_by_daytype"]), "",
        "## Powder effects (LCC new snow >= 8\" = powder day)", _md(ins["crowd_by_snow"]), "",
        "### Least-busy share on snow days (%)", _md(ins["least_busy_share_by_snow"]), "",
        "## By month", _md(ins["crowd_by_month"]), "",
        "## By day of week (non-holiday core season)", _md(ins["crowd_by_dow_nonholiday"]), "",
        "## By season (snowy winters vs. lean ones)", _md(ins["crowd_by_season"]), "",
        "## Worst system-wide days, last three seasons", _md(ins["worst_days_recent"]), "",
        "## Quietest core-season days, last three seasons", _md(ins["best_days_recent"]), "",
    ]
    path.write_text("\n".join(parts) + "\n")
    return path


def write_calendar_html(clim: pd.DataFrame, cfg: dict, season: int, path: Path) -> Path:
    """Self-contained month-grid calendar (also publishable as a web page)."""
    from .calendar_features import calendar_frame, day_type

    ikon = ikon_resorts(cfg)
    names = {r: cfg["resorts"][r]["name"] for r in ikon}
    rows = {}
    for d, g in clim.groupby("date"):
        rec = {}
        for r in g.itertuples():
            nan = r.crowd_mean != r.crowd_mean
            rec[r.resort] = [None if nan else round(r.crowd_mean), round(r.p_least_busy, 2), round(r.p_open, 2),
                             None if nan else round(r.crowd_p10), None if nan else round(r.crowd_p90)]
        rows[d.strftime("%Y-%m-%d")] = rec
    cal = calendar_frame(pd.DatetimeIndex(sorted(clim["date"].unique())))
    labels = {"holiday": "holiday", "holiday-week weekday": "school-break / holiday week"}
    flags = {d.strftime("%Y-%m-%d"): labels[t] for d, t in cal.apply(day_type, axis=1).items() if t in labels}
    st = cfg.get("operating")
    note = ""
    if st and int(st["season"]) == season:
        note = (f"Open/closed days follow the Ski Utah snow report as of "
                f"{dt.date.fromisoformat(st['as_of']):%b %-d, %Y}; later dates use typical opening and "
                f"closing dates and snow depth.")
    data = json.dumps({"names": names, "days": rows, "flags": flags, "statusNote": note}, separators=(",", ":"))
    title = f"Crowd calendar {season - 1}\u2013{str(season)[2:]}"
    tpl = (Path(__file__).parent / "templates" / "calendar.html").read_text()
    out = tpl.replace("__TITLE__", html.escape(title)).replace("__DATA__", data)
    path.write_text(out)
    return path
