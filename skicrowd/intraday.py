"""Time of day: how crowded each resort is hour by hour.

1. MEASURED: skiers on the mountain by hour.
   UDOT counts cars going up and down each canyon every hour. Up minus down,
   summed through the day, is the number of cars in the canyon at each hour.
   Dividing by the day's arrivals gives an occupancy curve (share of the day's
   skiers on the mountain) for each canyon, split by weekday vs weekend /
   holiday and powder vs normal day.

2. MODELLED: lift-line pressure from occupancy.
       pressure(h) = activity(h) * occupancy(h) + base_rush * arrivals(h)
   * activity(h) < 1 at lunch and in the afternoon: people in the lodge,
     taking breaks or tired aren't in lift lines.
   * arrivals(h) = share of the day's skiers arriving in hour h. They all
     queue at the base lifts, which is why 9-10am lines are worse than the
     headcount alone suggests.
   Both start as estimates (config `[intraday]`) and are re-fitted from the
   collector's webcam lift-line counts by `fit_from_webcams`.

The day's crowd index is treated as the peak hour; each hour is
crowd_index x pressure(h) / max(pressure).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .calendar_features import calendar_frame, day_type

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "data" / "intraday_profiles.csv"
WEBCAM_FIT_PATH = ROOT / "data" / "intraday_calibration.json"

HOURS = list(range(8, 21))          # blocks 8:00-9:00 ... 20:00-21:00
DAY_HOURS = list(range(9, 16))      # standard lift hours shown: 9a-3p (lifts close ~4pm)
NIGHT_HOURS = list(range(16, 20))   # Brighton night skiing 4-8pm

# which counter describes which resort's day
STATION_REGION = {317: "LCC", 322: "BCC", 822: "BRIGHTON", 609: "SNOWBASIN"}
RESORT_REGION = {"alta": "LCC", "snowbird": "LCC", "solitude": "BCC", "brighton": "BRIGHTON",
                 "snowbasin": "SNOWBASIN", "deer_valley": "POOLED", "park_city": "POOLED",
                 "powder_mountain": "SNOWBASIN"}
SNOW_STATION = {"LCC": "766:UT:SNTL", "BCC": "366:UT:SNTL", "BRIGHTON": "366:UT:SNTL", "SNOWBASIN": "332:UT:SNTL"}
MIN_DAYS = 6
LABEL = {h: (f"{h % 12 or 12}{'a' if h < 12 else 'p'}") for h in range(24)}


def _wk(daytypes) -> np.ndarray:
    return np.where(np.isin(daytypes, ["saturday", "sunday", "holiday"]), "weekend", "weekday")


# ------------------------------------------------------------- build curves
def build_profiles(hourly: pd.DataFrame, weather: pd.DataFrame, months=(12, 1, 2, 3)) -> pd.DataFrame:
    from .udot import up_direction

    H = [f"Z{i:02d}00" for i in range(24)]
    ups = up_direction(hourly)
    rows = []
    for st, region in STATION_REGION.items():
        g = hourly[hourly["station"] == st]
        if g.empty:
            continue
        up = g[g["direction"] == ups[st]].set_index("date")[H]
        dn = g[g["direction"] != ups[st]].set_index("date")[H]
        idx = up.index.intersection(dn.index)
        up, dn = up.loc[idx], dn.loc[idx]
        ok = up.notna().all(axis=1) & dn.notna().all(axis=1) & up.index.month.isin(months)
        up, dn = up[ok].to_numpy(float), dn[ok].to_numpy(float)
        dates = idx[ok]
        arrivals = up[:, 5:14].sum(axis=1)
        keep = arrivals > 200
        up, dn, dates, arrivals = up[keep], dn[keep], dates[keep], arrivals[keep]
        cum = np.cumsum(up[:, 5:] - dn[:, 5:], axis=1) / arrivals[:, None]   # cum[:, k] = in canyon at (k+6):00
        at = lambda hr: cum[:, hr - 6] if hr >= 6 else np.zeros(len(cum))   # noqa: E731
        cal = calendar_frame(pd.DatetimeIndex(dates))
        wk = _wk(cal.apply(day_type, axis=1).to_numpy())
        snow = weather[weather["station"] == SNOW_STATION[region]].set_index("date")["new_24"]
        pw = np.where(snow.reindex(dates).fillna(0).to_numpy() >= 8, "powder", "normal")
        for h in HOURS:
            occ = (at(h) + at(h + 1)) / 2                     # mean in-canyon during block h
            # cars pass the counter ~30 min before reaching the lifts
            arr = (up[:, h - 1] + up[:, h]) / 2 / arrivals
            for w in ("weekday", "weekend"):
                for s in ("normal", "powder"):
                    m = (wk == w) & (pw == s)
                    rows.append({"region": region, "daytype": w, "snow": s, "hour": h, "n": int(m.sum()),
                                 "occupancy": float(np.median(occ[m])) if m.any() else np.nan,
                                 "arrivals": float(np.median(arr[m])) if m.any() else np.nan})
    prof = pd.DataFrame(rows)
    # Snowbasin's counter is commuter traffic on weekdays: only weekends are usable
    prof.loc[(prof["region"] == "SNOWBASIN") & (prof["daytype"] == "weekday"), "n"] = 0
    pooled = (prof[prof["region"].isin(["LCC", "BCC"])]
              .groupby(["daytype", "snow", "hour"], as_index=False)
              .apply(lambda g: pd.Series({"n": g["n"].sum(),
                                          "occupancy": np.average(g["occupancy"], weights=g["n"].clip(lower=1)),
                                          "arrivals": np.average(g["arrivals"], weights=g["n"].clip(lower=1))}),
                     include_groups=False))
    pooled["region"] = "POOLED"
    prof = pd.concat([prof, pooled], ignore_index=True)
    prof["occupancy"] = prof["occupancy"].clip(lower=0)
    prof["arrivals"] = prof["arrivals"].clip(lower=0)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    prof.round(4).to_csv(PROFILE_PATH, index=False)
    return prof


def load_profiles() -> pd.DataFrame:
    return pd.read_csv(PROFILE_PATH)


def _curve(prof: pd.DataFrame, region: str, daytype: str, snow: str) -> pd.DataFrame:
    """Best available curve, falling back: same region normal snow -> pooled."""
    for reg, s in ((region, snow), (region, "normal"), ("POOLED", snow), ("POOLED", "normal")):
        c = prof[(prof["region"] == reg) & (prof["daytype"] == daytype) & (prof["snow"] == s)]
        if len(c) and c["n"].min() >= MIN_DAYS:
            return c.set_index("hour")
    return prof[(prof["region"] == "POOLED") & (prof["daytype"] == "weekend") & (prof["snow"] == "normal")].set_index("hour")


# ------------------------------------------------------------ apply to days
def _params(cfg: dict) -> tuple[dict, float]:
    p = cfg.get("intraday", {})
    lunch = {int(k): float(v) for k, v in p.get("activity", {}).items()}
    rush = float(p.get("base_rush", 1.0))
    if WEBCAM_FIT_PATH.exists():
        fit = json.loads(WEBCAM_FIT_PATH.read_text())
        lunch.update({int(k): float(v) for k, v in fit.get("activity", {}).items()})
        rush = float(fit.get("base_rush", rush))
    return lunch, rush


def shape(prof: pd.DataFrame, cfg: dict, resort: str, daytype: str, snow: str) -> pd.Series:
    c = _curve(prof, RESORT_REGION.get(resort, "POOLED"), daytype, snow)
    lunch, rush = _params(cfg)
    lf = np.array([lunch.get(h, 1.0) for h in c.index])
    p = lf * c["occupancy"].to_numpy() + rush * c["arrivals"].to_numpy()
    s = pd.Series(p / p.max(), index=c.index)
    if resort != "brighton":
        s[s.index >= 16] = np.nan   # lifts closed; only Brighton runs night skiing
    return s


def hourly(res: pd.DataFrame, cfg: dict, prof: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per resort-day crowd index for each hour block.

    `res` needs date, resort, crowd_index, new_24 (model output)."""
    prof = load_profiles() if prof is None else prof
    r = res[res["crowd_index"].notna()].copy()
    cal = calendar_frame(pd.DatetimeIndex(sorted(r["date"].unique())))
    wk = pd.Series(_wk(cal.apply(day_type, axis=1).to_numpy()), index=cal.index)
    r["daytype"] = r["date"].map(wk)
    srf = cfg["behaviour"].get("snow_report_factor", 1.0)
    r["snow"] = np.where(r["new_24"] * srf >= 8, "powder", "normal")
    cache, out = {}, []
    for row in r.itertuples():
        k = (row.resort, row.daytype, row.snow)
        if k not in cache:
            cache[k] = shape(prof, cfg, *k)
        s = cache[k]
        night = _night_open(cfg["resorts"].get(row.resort, {}), row.date)
        for h, v in s.items():
            if v == v and (h < 16 or night):
                out.append((row.date, row.resort, h, row.crowd_index * v))
    return pd.DataFrame(out, columns=["date", "resort", "hour", "crowd_index"])


def _night_open(rc: dict, date: pd.Timestamp) -> bool:
    ns = rc.get("night_skiing")
    if not ns:
        return False
    md = date.strftime("%m-%d")
    start, end = ns["start_mmdd"], ns["end_mmdd"]
    in_season = (md >= start or md <= end) if start > end else (start <= md <= end)
    return in_season and date.weekday() in ns.get("weekdays", range(7))


def best_windows(hr: pd.DataFrame, hours=DAY_HOURS) -> pd.DataFrame:
    """For each date: quietest resort-hour, and per resort the quietest
    2-hour window inside normal lift hours."""
    h = hr[hr["hour"].isin(hours)]
    rows = []
    for (d, r), g in h.groupby(["date", "resort"]):
        g = g.sort_values("hour").set_index("hour")["crowd_index"]
        two = g.rolling(2).mean().dropna()
        if two.empty:
            continue
        hb = int(two.idxmin())
        rows.append({"date": d, "resort": r, "best_start": hb - 1, "best_crowd": float(two.min()),
                     "peak_hour": int(g.idxmax()), "peak_crowd": float(g.max())})
    return pd.DataFrame(rows)


# ------------------------------------------------------- learn from webcams
def fit_from_webcams(data_dir: str | Path, cfg: dict, prof: pd.DataFrame | None = None,
                     min_days: int = 8, verbose: bool = True) -> dict | None:
    """Refit activity(h) and base_rush from lift-line webcam counts.

    For each lift-line camera-day, counts are normalised by that day's peak and
    compared with occupancy / arrival curves for the camera's resort and day
    type. A non-negative least-squares fit per hour gives the lunch dip and
    the morning rush weight; estimates are shrunk toward the priors when data
    is thin."""
    from scipy.optimize import nnls

    from .collect import MT
    prof = load_profiles() if prof is None else prof
    path = Path(data_dir) / "webcam_counts.csv"
    if not path.exists():
        return None
    w = pd.read_csv(path)
    w = w[(w["usable"] == 1) & (w["kind"] == "lift_line")].copy()
    if w.empty:
        return None
    t = pd.to_datetime(w["image_time"], utc=True).dt.tz_convert(MT)
    w["date"] = t.dt.tz_localize(None).dt.normalize()
    w["hour"] = t.dt.hour
    w = w[w["hour"].isin(DAY_HOURS)]
    g = w.groupby(["camera", "resort", "date", "hour"], as_index=False)["people"].mean()
    peak = g.groupby(["camera", "date"])["people"].transform("max")
    g = g[peak >= 5].assign(y=lambda d: d["people"] / peak[peak >= 5])
    n_days = g[["camera", "date"]].drop_duplicates().shape[0]
    if n_days < min_days:
        if verbose:
            print(f"only {n_days} usable lift-line camera-days; need {min_days}")
        return None
    cal = calendar_frame(pd.DatetimeIndex(sorted(g["date"].unique())))
    wk = pd.Series(_wk(cal.apply(day_type, axis=1).to_numpy()), index=cal.index)
    g["daytype"] = g["date"].map(wk)
    occ, arr = [], []
    for row in g.itertuples():
        c = _curve(prof, RESORT_REGION.get(row.resort, "POOLED"), row.daytype, "normal")
        occ.append(c.at[row.hour, "occupancy"] / c["occupancy"].max())
        arr.append(c.at[row.hour, "arrivals"] / c["occupancy"].max())
    g["occ"], g["arr"] = occ, arr
    # design: one occupancy coefficient per hour (activity) + shared rush
    X = np.zeros((len(g), len(DAY_HOURS) + 1))
    for i, h in enumerate(DAY_HOURS):
        X[:, i] = np.where(g["hour"] == h, g["occ"], 0)
    X[:, -1] = g["arr"]
    coef, _ = nnls(X, g["y"].to_numpy())
    lunch0, rush0 = _params(cfg)
    k = n_days / (n_days + 30)                     # shrink toward priors
    occ_scale = np.median(coef[:-1][coef[:-1] > 0]) if (coef[:-1] > 0).any() else 1.0
    lunch = {str(h): round((1 - k) * lunch0.get(h, 1.0) + k * float(coef[i] / occ_scale), 3)
             for i, h in enumerate(DAY_HOURS)}
    rush = round((1 - k) * rush0 + k * float(coef[-1] / occ_scale), 3)
    fit = {"activity": lunch, "base_rush": rush, "camera_days": int(n_days)}
    WEBCAM_FIT_PATH.write_text(json.dumps(fit, indent=2) + "\n")
    if verbose:
        print(f"intraday fit from {n_days} lift-line camera-days: base_rush={rush}, activity={lunch}")
    return fit
