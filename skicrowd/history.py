"""Past days, measured, so upcoming days can be compared with them.

Every day the collector has seen (Nov 2026 onward) gets:
  * cars in Little / Big Cottonwood through the day (UDOT live counts)
  * a measured crowd index per resort: the model's index for that day,
    scaled by how the canyon's measured peak compared with what the model
    expected (the same adjustment the live "crowd now" uses)
  * lifts running and the day's lift-line webcam counts

Days you skied (data/my_days.csv) are highlighted. Older ones (Jan 2022 to
Mar 2025) are rebuilt from UDOT's hourly counts at the canyon mouths:
cars in the canyon = up-canyon minus down-canyon cars since midnight.

Upcoming days get the same measures from the model (expected peak cars, crowd
index) plus the closest of your days.
"""
from __future__ import annotations

import datetime as dt
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from . import intraday
from .config import CALIBRATED_PATH, ikon_resorts
from .dashboard import CANYON_STATION, MT, _mt, _profile_share, _read, cam_rows, open_days

ROOT = Path(__file__).resolve().parent.parent
MY_DAYS = ROOT / "data" / "my_days.csv"
UDOT_HOURLY = ROOT / "data" / "udot" / "ccs_hourly.csv"
STATION_CANYON = {317: "LCC", 322: "BCC"}
PEAK_HOURS = (8, 14)          # the morning peak window used to compare days


def load_my_days(path: Path | None = None) -> pd.DataFrame:
    path = path or MY_DAYS
    if not path.exists():
        return pd.DataFrame(columns=["date", "resort", "arrive", "leave", "notes"])
    d = pd.read_csv(path, dtype=str).fillna("")
    d["date"] = pd.to_datetime(d["date"]).dt.date
    return d


# ------------------------------------------------------------ canyon curves
def curves_live(data_dir: Path) -> dict[dt.date, dict[str, list]]:
    d = _read(data_dir / "canyon_live.csv")
    if d is None or d.empty:
        return {}
    d["t"] = _mt(d["observed_at"])
    d = d.sort_values("t").drop_duplicates(["canyon", "t"])
    out: dict = {}
    for (day, c), g in d.groupby([d["t"].dt.date, "canyon"]):
        g = g[(g["t"].dt.hour >= 6) & (g["t"].dt.hour < 18)]
        if len(g) >= 6:
            out.setdefault(day, {})[c] = [[t.strftime("%H:%M"), int(v)] for t, v in zip(g["t"], g["net_vehicles_in_canyon"])]
    return out


def curves_udot(dates: list[dt.date], path: Path = UDOT_HOURLY) -> dict[dt.date, dict[str, list]]:
    """Cars in the canyon by hour from UDOT's historical up/down counts."""
    if not dates or not path.exists():
        return {}
    from .udot import up_direction
    h = pd.read_csv(path, parse_dates=["date"])
    h = h[h["station"].isin(STATION_CANYON)]
    up = up_direction(h)
    hours = [f"Z{k:02d}00" for k in range(24)]
    out: dict = {}
    for day in dates:
        g = h[h["date"].dt.date == day]
        for st, gs in g.groupby("station"):
            if len(gs) != 2:
                continue
            u = gs[gs["direction"] == up[int(st)]][hours].to_numpy(float)[0]
            dn = gs[gs["direction"] != up[int(st)]][hours].to_numpy(float)[0]
            if np.isnan(u).any() or np.isnan(dn).any():
                continue
            net = np.cumsum(u - dn)            # value at the end of each hour
            out.setdefault(day, {})[STATION_CANYON[int(st)]] = [
                [f"{k + 1:02d}:00", int(max(net[k], 0))] for k in range(5, 18)]
    return out


def _peak(series: list) -> tuple[int, str] | None:
    pts = [(v, t) for t, v in series if PEAK_HOURS[0] <= int(t[:2]) <= PEAK_HOURS[1]]
    if not pts:
        return None
    v, t = max(pts)
    return v, t


# ------------------------------------------------------------ model per day
def logged_openings(data_dir: Path | None) -> dict[str, str]:
    """First day each resort was reported open (collector's Ski Utah log)."""
    st = _read(data_dir / "resort_status.csv") if data_dir else None
    if st is None or st.empty:
        return {}
    return st[st["status"] == "open"].groupby("resort")["as_of"].min().to_dict()


def model_days(cfg: dict, weather: pd.DataFrame, dates: list[dt.date], data_dir: Path | None = None) -> pd.DataFrame:
    """Model crowd index and visitors per resort for each date (one run per season)."""
    from .calendar_features import season_of
    from .config import load_config
    from .model import build_inputs, run_model
    out = []
    for season in sorted({season_of(d) for d in dates}):
        want = [d for d in dates if season_of(d) == season]
        c = cfg if (cfg.get("operating") or {}).get("season") in (season, str(season)) else load_config(use_status=False)
        if c is cfg and cfg.get("operating"):
            # past days: resorts open from the day they were actually reported open
            import copy
            c = copy.deepcopy(cfg)
            for r, first in logged_openings(data_dir).items():
                if r in c["operating"]["resorts"]:
                    c["operating"]["resorts"][r] = {**c["operating"]["resorts"][r], "status": "opening", "date": first}
        end = pd.Timestamp(max(want))
        w = _extend(weather, end)
        res = run_model(c, build_inputs(c, pd.date_range(f"{season - 1}-10-15", end, freq="D"), w))
        res = res[res["date"].dt.date.isin(want)]
        out.append(res[["date", "resort", "crowd_index", "visitors", "new_24"]])
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _extend(weather: pd.DataFrame, end: pd.Timestamp) -> pd.DataFrame:
    """Weather through `end`: days not yet measured carry the last snowpack
    forward with no new snow (as the forecast fallback does)."""
    last = weather["date"].max()
    if last >= end:
        return weather
    rows = []
    for st, g in weather[weather["date"] > last - pd.Timedelta(days=7)].groupby("station"):
        lr = g.loc[g["date"].idxmax()]
        for d in pd.date_range(last + pd.Timedelta(days=1), end, freq="D"):
            rows.append({"date": d, "station": st, "new_24": 0.0, "new_48": 0.0, "base": lr["base"],
                         "temp_ski": round(g["temp_ski"].mean(), 1), "rain": 0, "swe_24": 0.0})
    return pd.concat([weather, pd.DataFrame(rows)], ignore_index=True)


def _fit(canyon: str) -> dict | None:
    if not CALIBRATED_PATH.exists():
        return None
    return tomllib.loads(CALIBRATED_PATH.read_text()).get("fit", {}).get(f"canyon_vehicles|{CANYON_STATION[canyon]}")


def expected_peak(canyon_visitors: float, canyon: str, daytype: str, snow: str) -> float | None:
    """Model's expected peak cars in the canyon over the morning window."""
    f = _fit(canyon)
    if not f or not canyon_visitors:
        return None
    share = max(_profile_share(canyon, daytype, snow, h) for h in np.arange(PEAK_HOURS[0], PEAK_HOURS[1] + 0.01, 0.25))
    return (f["intercept"] + f["slope"] * canyon_visitors) * share


def _daytype(day: dt.date) -> str:
    from .calendar_features import calendar_frame, day_type
    cal = calendar_frame(pd.DatetimeIndex([pd.Timestamp(day)]))
    return "weekend" if day_type(cal.iloc[0]) in ("saturday", "sunday", "holiday") else "weekday"


# ------------------------------------------------------------------ build
def build(cfg: dict, weather: pd.DataFrame, data_dir: Path, now: dt.datetime | None = None,
          week: list[dict] | None = None, model_week: pd.DataFrame | None = None) -> dict:
    now = (now or dt.datetime.now(MT)).astimezone(MT)
    ikon = ikon_resorts(cfg)
    names = {r: cfg["resorts"][r]["name"] for r in ikon}
    mine = load_my_days()
    live = curves_live(data_dir)
    live = {d: v for d, v in live.items() if d < now.date()}           # finished days only
    older = [d for d in mine["date"] if d not in live]
    curves = {**curves_udot(older), **live}
    days = sorted(curves)
    model = model_days(cfg, weather, days, data_dir) if days else pd.DataFrame()
    od = open_days(data_dir)
    cams = cam_rows(data_dir)
    ls = _read(data_dir / "lift_status.csv")
    if ls is not None and len(ls):
        ls["t"] = _mt(ls["collected_at"]); ls["date"] = ls["t"].dt.date
        ls = ls[(ls["t"].dt.hour >= 10) & (ls["t"].dt.hour < 15)]
    lift_days = set(ls["date"]) if ls is not None and len(ls) else set()

    def lifts_running(day: dt.date) -> list[dict]:
        """Lift rows for a day; before lift logging began, the next logged day stands in."""
        if not lift_days:
            return []
        d = day if day in lift_days else min((x for x in lift_days if x > day), default=None)
        if d is None:
            return []
        g = ls[ls["date"] == d]
        g = g[g["t"] == (g["t"].min() if d != day else g["t"].max())]    # stand-in day: its morning
        return g[["resort", "lift", "status"]].to_dict("records")

    records = []
    for day in days:
        m = model[model["date"].dt.date == day].set_index("resort") if len(model) else pd.DataFrame()
        rec = {"date": day.isoformat(), "dow": day.strftime("%a"), "label": day.strftime("%b %-d, %Y" if day.year != now.year else "%b %-d"),
               "source": "collector" if day in live else "udot", "canyons": {}, "resorts": {}, "mine": None}
        for c, series in curves[day].items():
            pk = _peak(series)
            rec["canyons"][c] = {"series": series, "peak": pk[0] if pk else None, "peak_time": pk[1] if pk else None}
        dtype = _daytype(day)
        for r in ikon:
            if r not in m.index or m.loc[r, "crowd_index"] != m.loc[r, "crowd_index"]:
                continue
            if day in live and day in lift_days and r not in [x for x, ds in od.items() if day in ds]:
                continue                                          # lifts weren't running that day
            rec["resorts"][r] = {"model": round(float(m.loc[r, "crowd_index"])), "crowd": round(float(m.loc[r, "crowd_index"])),
                                 "measured": False}
        for c in ("LCC", "BCC"):
            members = [r for r in rec["resorts"] if cfg["resorts"][r]["canyon"] == c]
            pk = rec["canyons"].get(c, {}).get("peak")
            if not members or not pk:
                continue
            vis = sum(float(m.loc[r, "visitors"]) for r in members)
            snow = "powder" if any(float(m.loc[r, "new_24"]) >= 8 for r in members) else "normal"
            exp = expected_peak(vis, c, dtype, snow)
            if exp and exp > 150:
                ratio = float(np.clip(pk / exp, 0.5, 1.8))
                for r in members:
                    rec["resorts"][r]["crowd"] = round(rec["resorts"][r]["model"] * ratio)
                    rec["resorts"][r]["measured"] = True
                rec["canyons"][c]["expected"] = round(exp)
        if cams is not None and day in live:
            from .lifts import is_open
            from .collect import load_cams
            lift_of = {f"{c['resort']}:{c['title']}": c.get("lift") for c in load_cams()}
            g = cams[(cams["date"] == day) & (cams["kind"] == "lift_line")].copy()
            lo = pd.to_numeric(g["lift_open"], errors="coerce") if "lift_open" in g else pd.Series(np.nan, index=g.index)
            if lo.isna().all():                   # no lift status logged that day
                rows = lifts_running(day)
                ok = {cam for cam in g["camera"].unique() if is_open(rows, cam.split(":")[0], lift_of.get(cam)) == 1}
                g = g[g["camera"].isin(ok) & g["resort"].isin(rec["resorts"])]
            else:
                g = g[lo == 1]
            rec["lines"] = {cam.split(":", 1)[1]: {"resort": cam.split(":", 1)[0], "peak": int(x["value"].max()),
                                                     "at_11": int(x.loc[(x["t"].dt.hour == 11), "value"].median())
                                                     if (x["t"].dt.hour == 11).any() else None}
                            for cam, x in g.groupby("camera")}
        md = mine[mine["date"] == day]
        if len(md):
            row = md.iloc[0]
            rec["mine"] = {"resort": row["resort"], "arrive": row["arrive"], "leave": row["leave"], "notes": row["notes"]}
        records.append(rec)

    # upcoming days: expected peak cars, and the closest of your days
    upcoming = []
    if week:
        for w in week:
            day = dt.date.fromisoformat(w["date"])
            mw = model_week[model_week["date"].dt.date == day].set_index("resort") if model_week is not None and len(model_week) else None
            item = {"date": w["date"], "canyons": {}, "like": {}}
            for c in ("LCC", "BCC"):
                members = [r for r, v in w["resorts"].items() if v["crowd"] is not None and cfg["resorts"][r]["canyon"] == c]
                if mw is None or not members:
                    continue
                vis = sum(float(mw.loc[r, "visitors"]) for r in members if r in mw.index)
                snow = "powder" if any(float(mw.loc[r, "new_24"]) >= 8 for r in members if r in mw.index) else "normal"
                exp = expected_peak(vis, c, _daytype(day), snow)
                if exp:
                    item["canyons"][c] = round(exp)
            for r, v in w["resorts"].items():
                if v["crowd"] is None:
                    continue
                cands = [x for x in records if r in x["resorts"]]
                pref = [x for x in cands if x["mine"] and x["mine"]["resort"] == r] or [x for x in cands if x["mine"]]
                for key, pool in (("mine", pref), ("any", cands)):
                    if pool:
                        best = min(pool, key=lambda x: abs(x["resorts"][r]["crowd"] - v["crowd"]))
                        item["like"].setdefault(r, {})[key] = {"date": best["date"], "crowd": best["resorts"][r]["crowd"]}
            upcoming.append(item)
    return {"names": names, "days": records[::-1], "upcoming": upcoming}
