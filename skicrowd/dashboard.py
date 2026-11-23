"""Build the live dashboard (GitHub Pages): data.json + static pages.

Runs in the collector after each collection, so the site refreshes every
~20 minutes during ski hours.

Today, per resort:
  * lifts open (resort lift-status pages)
  * cars in the canyon - MEASURED for Little / Big Cottonwood (UDOT live
    counts), then split between the canyon's resorts by the model's expected
    share among the resorts that are open (an ESTIMATE)
  * latest lift-line webcam counts, for lifts that are running
  * crowd now - the model's hourly crowd index, scaled by how the measured
    canyon count compares with what the model expected by this time of day
    ("nowcast"); Deer Valley / Snowbasin have no live counter, so model only
This week: the model's crowd index for the next 7 days.
Season: the climatological calendar on its own page.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import intraday
from .config import CALIBRATED_PATH, ikon_resorts
from .model import crowd_label

MT = ZoneInfo("America/Denver")
TEMPLATES = Path(__file__).resolve().parent / "templates"
CANYON_STATION = {"LCC": "udot_317", "BCC": "udot_322"}
CANYON_NAME = {"LCC": "Little Cottonwood", "BCC": "Big Cottonwood"}


def _read(path: Path) -> pd.DataFrame | None:
    return pd.read_csv(path) if path.exists() else None


def _mt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True).dt.tz_convert(MT)


def parse_now(s: str | None) -> dt.datetime:
    if not s:
        return dt.datetime.now(MT)
    t = dt.datetime.fromisoformat(s)
    return t.replace(tzinfo=MT) if t.tzinfo is None else t.astimezone(MT)


# ---------------------------------------------------------------- live data
def lift_summary(data_dir: Path, now: dt.datetime | None = None) -> dict:
    """Latest lift snapshot per resort. Outside lift hours, when the latest
    snapshot shows everything shut, the last snapshot from the past day that
    had lifts running is shown instead (labelled with its time)."""
    d = _read(data_dir / "lift_status.csv")
    if d is None or d.empty:
        return {}
    d["t"] = _mt(d["collected_at"])
    now = (now or dt.datetime.now(MT)).astimezone(MT)
    d = d[d["t"] <= now]
    out = {}
    for r, g in d.groupby("resort"):
        snaps = g.groupby("t")
        latest = g[g["t"] == g["t"].max()]
        pick = latest
        if not (9 <= now.hour < 16) and not (latest["status"] == "open").any():
            busy = [t for t, s in snaps if (s["status"] == "open").any() and t >= now - dt.timedelta(hours=24)]
            if busy:
                pick = g[g["t"] == max(busy)]
        t = pick["t"].iloc[0]
        out[r] = {"open": int((pick["status"] == "open").sum()), "total": int(len(pick)),
                  "unknown": int((pick["status"] == "unknown").sum()),
                  "open_lifts": pick.loc[pick["status"] == "open", "lift"].tolist(),
                  "as_of": (t.strftime("%-I:%M %p") if t.date() == now.date() else t.strftime("%a %-I:%M %p"))}
    return out


def canyon_today(data_dir: Path, now: dt.datetime) -> dict:
    d = _read(data_dir / "canyon_live.csv")
    if d is None or d.empty:
        return {}
    d["t"] = _mt(d["observed_at"])
    d = d[(d["t"].dt.date == now.date()) & (d["t"] <= now)].sort_values("t").drop_duplicates(["canyon", "t"])
    out = {}
    for c, g in d.groupby("canyon"):
        out[c] = {"name": CANYON_NAME.get(c, c),
                  "series": [[t.strftime("%H:%M"), int(v)] for t, v in zip(g["t"], g["net_vehicles_in_canyon"])],
                  "latest": int(g["net_vehicles_in_canyon"].iloc[-1]),
                  "latest_time": g["t"].iloc[-1].strftime("%-I:%M %p"),
                  "peak": int(g["net_vehicles_in_canyon"].max())}
    return out


def webcams_today(data_dir: Path, now: dt.datetime) -> dict:
    d = _read(data_dir / "webcam_counts.csv")
    if d is None or d.empty:
        return {}
    d["t"] = _mt(d["image_time"])
    d = d[(d["t"].dt.date == now.date()) & (d["t"] <= now) & (d["usable"] == 1) & d["kind"].isin(["lift_line", "parking"])]
    out = {}
    for cam, g in d.sort_values("t").groupby("camera"):
        g = g.dropna(subset=["people", "vehicles"])
        if g.empty:
            continue
        kind = g["kind"].iloc[0]
        val = g["vehicles"] if kind == "parking" else g["people"]
        lift_open = g["lift_open"].iloc[-1] if "lift_open" in g else np.nan
        out[cam] = {"resort": g["resort"].iloc[0], "title": cam.split(":", 1)[1], "kind": kind,
                    "latest": int(val.iloc[-1]), "time": g["t"].iloc[-1].strftime("%-I:%M %p"),
                    "lift_open": None if pd.isna(lift_open) else bool(int(lift_open)),
                    "series": [[t.strftime("%H:%M"), int(v)] for t, v in zip(g["t"], val)]}
    return out


# ------------------------------------------------------------------ nowcast
def _profile_share(region: str, daytype: str, snow: str, t_hours: float) -> float:
    """Share of the day's arrivals expected to be in the canyon at t."""
    c = intraday._curve(intraday.load_profiles(), region, daytype, snow)
    return float(np.interp(t_hours, c.index.to_numpy() + 0.5, c["occupancy"].to_numpy()))


def nowcast(canyon_cars: int, canyon_visitors: float, canyon: str, daytype: str, snow: str,
            t_hours: float) -> float | None:
    """Measured / model-implied cars in the canyon now (None if too early or
    too little to compare). The model's visitors -> daily up-canyon cars link
    is the one fitted on UDOT counts."""
    if not (8.0 <= t_hours <= 16.0) or not CALIBRATED_PATH.exists():
        return None
    fit = tomllib.loads(CALIBRATED_PATH.read_text()).get("fit", {}).get(f"canyon_vehicles|{CANYON_STATION[canyon]}")
    if not fit:
        return None
    implied = (fit["intercept"] + fit["slope"] * canyon_visitors) * _profile_share(canyon, daytype, snow, t_hours)
    if implied < 150:
        return None
    return float(np.clip(canyon_cars / implied, 0.5, 1.8))


# -------------------------------------------------------------------- build
def build(cfg: dict, weather: pd.DataFrame, data_dir: Path, now: dt.datetime | None = None,
          forecast: pd.DataFrame | None = None, week_days: int = 7) -> dict:
    from .analysis import predict_day
    from .calendar_features import calendar_frame, day_type

    now = (now or dt.datetime.now(MT)).astimezone(MT)
    today = now.date()
    ikon = ikon_resorts(cfg)
    names = {r: cfg["resorts"][r]["name"] for r in ikon}
    status = (cfg.get("operating") or {}).get("resorts", {})
    lifts = lift_summary(data_dir, now)
    canyons = canyon_today(data_dir, now)
    cams = webcams_today(data_dir, now)

    # model for today and the week
    week, today_rows, today_hourly, mode_today = [], None, None, None
    for k in range(week_days):
        d = today + dt.timedelta(days=k)
        day, mode, hr = predict_day(cfg, weather, d, forecast=forecast)
        day = day[day["resort"].isin(ikon)]
        if k == 0:
            today_rows, today_hourly, mode_today = day.set_index("resort"), hr, mode
        entry = {"date": d.isoformat(), "dow": d.strftime("%a"), "label": d.strftime("%b %-d"), "mode": mode,
                 "resorts": {}}
        for row in day.itertuples():
            ci = None if row.crowd_index != row.crowd_index else round(float(row.crowd_index))
            hrs = hr[hr["resort"] == row.resort].set_index("hour")["crowd_index"].reindex(intraday.DAY_HOURS)
            entry["resorts"][row.resort] = {"crowd": ci, "level": crowd_label(row.crowd_index),
                                            "hourly": [None if v != v else round(float(v)) for v in hrs]}
        open_ = {r: v["crowd"] for r, v in entry["resorts"].items() if v["crowd"] is not None}
        entry["best"] = min(open_, key=open_.get) if open_ else None
        week.append(entry)

    # live estimates for today
    cal = calendar_frame(pd.DatetimeIndex([pd.Timestamp(today)]))
    dtype = "weekend" if day_type(cal.iloc[0]) in ("saturday", "sunday", "holiday") else "weekday"
    t_hours = now.hour + now.minute / 60
    block = int(min(max(now.hour, intraday.DAY_HOURS[0]), intraday.DAY_HOURS[-1]))
    resorts = {}
    for r in ikon:
        rc = cfg["resorts"][r]
        row = today_rows.loc[r] if today_rows is not None and r in today_rows.index else None
        is_open = row is not None and row["crowd_index"] == row["crowd_index"]
        model_now = None
        if is_open and today_hourly is not None:
            h = today_hourly[(today_hourly["resort"] == r) & (today_hourly["hour"] == block)]
            model_now = float(h["crowd_index"].iloc[0]) if len(h) else float(row["crowd_index"])
        resorts[r] = {"name": names[r], "canyon": rc["canyon"], "open_today": bool(is_open),
                      "status": status.get(r, {}), "lifts": lifts.get(r),
                      "model_now": None if model_now is None else round(model_now),
                      "crowd_now": None if model_now is None else round(model_now), "nowcast_ratio": None,
                      "est_cars": None, "cams": [c for c in cams.values() if c["resort"] == r]}

    for c, live in canyons.items():
        members = [r for r in ikon if cfg["resorts"][r]["canyon"] == c and resorts[r]["open_today"]]
        if not members or today_rows is None:
            continue
        vis = {r: float(today_rows.loc[r, "visitors"]) for r in members if "visitors" in today_rows}
        total = sum(vis.values())
        snow = "powder" if any(float(today_rows.loc[r, "new_24"]) * cfg["behaviour"].get("snow_report_factor", 1)
                               >= 8 for r in members if "new_24" in today_rows) else "normal"
        ratio = nowcast(live["latest"], total, c, dtype, snow, t_hours) if total else None
        for r in members:
            resorts[r]["est_cars"] = round(live["latest"] * vis[r] / total) if total else None
            if ratio is not None and resorts[r]["model_now"] is not None:
                resorts[r]["nowcast_ratio"] = round(ratio, 2)
                resorts[r]["crowd_now"] = round(resorts[r]["model_now"] * ratio)
    for r in resorts.values():
        r["level_now"] = crowd_label(r["crowd_now"]) if r["crowd_now"] is not None else "closed"

    open_now = {r: v["crowd_now"] for r, v in resorts.items() if v["crowd_now"] is not None}
    return {
        "generated_at": now.isoformat(timespec="minutes"),
        "generated_label": now.strftime("%a %b %-d, %-I:%M %p"),
        "today": today.isoformat(), "model_mode_today": mode_today,
        "hours": intraday.DAY_HOURS, "now_hour": block if 7 <= now.hour < 17 else None,
        "best_now": min(open_now, key=open_now.get) if open_now else None,
        "resorts": resorts, "canyons": canyons, "week": week,
        "order": sorted(ikon, key=lambda r: (open_now.get(r) is None, open_now.get(r, 0))),
    }


def write_site(data: dict, out: Path, calendar_html: str | None = None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(data, separators=(",", ":")))
    shutil.copy(TEMPLATES / "dashboard.html", out / "index.html")
    (out / ".nojekyll").write_text("")
    if calendar_html is not None:
        (out / "calendar.html").write_text(
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            "</head><body>" + calendar_html + "</body></html>")
    return out
