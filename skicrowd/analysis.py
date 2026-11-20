"""Backtests over past seasons, climatological planning and point predictions."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .calendar_features import calendar_frame, day_type, season_dates, season_of
from .config import ikon_resorts
from . import intraday
from .model import add_rankings, build_inputs, run_model

LCC_STATION = "766:UT:SNTL"


# --------------------------------------------------------------- history
def run_history(cfg: dict, weather: pd.DataFrame, seasons: list[int]) -> pd.DataFrame:
    frames = []
    for s in seasons:
        dates = season_dates(s)
        res = run_model(cfg, build_inputs(cfg, dates, weather))
        frames.append(res)
    res = add_rankings(pd.concat(frames, ignore_index=True), ikon_resorts(cfg))
    cal = calendar_frame(pd.DatetimeIndex(res["date"].unique()))
    cal["day_type"] = cal.apply(day_type, axis=1)
    lcc = weather[weather["station"] == LCC_STATION].set_index("date")
    cal["lcc_new_24"] = lcc["new_24"].reindex(cal.index).fillna(0)
    cal["lcc_new_48"] = lcc["new_48"].reindex(cal.index).fillna(0)
    weekend = cal["is_saturday"] | cal["is_sunday"] | cal["holiday_period"]
    cal["snow_type"] = np.select(
        [cal["lcc_new_24"] >= 8, (cal["lcc_new_48"] - cal["lcc_new_24"] >= 8) & (cal["lcc_new_24"] < 3)],
        ["powder day", "day after storm"], "no new snow")
    cal["weekend_like"] = weekend
    return res.merge(cal[["season", "day_type", "snow_type", "weekend_like", "dow", "month"]],
                     left_on="date", right_index=True)


def insights(hist: pd.DataFrame, cfg: dict) -> dict[str, pd.DataFrame]:
    ikon = ikon_resorts(cfg)
    names = {r: cfg["resorts"][r]["name"] for r in cfg["resorts"]}
    h = hist[hist["resort"].isin(ikon) & hist["open"]].copy()
    h["Resort"] = h["resort"].map(names)
    # "core season" = all six Ikon resorts open; avoids early/late-season artefacts
    n_open = h.groupby("date")["resort"].transform("count")
    core = h[n_open == len(ikon)].copy()
    winners = core[core["rank"] == 1]
    out = {}

    order = ["weekday", "saturday", "sunday", "holiday-week weekday", "holiday"]
    out["crowd_by_daytype"] = (core.pivot_table(index="Resort", columns="day_type", values="crowd_index", aggfunc="mean")
                               .reindex(columns=order).round(0))
    out["least_busy_share_by_daytype"] = (pd.crosstab(winners["Resort"], winners["day_type"], normalize="columns")
                                          .reindex(columns=order).mul(100).round(0))
    core["wk"] = np.where(core["weekend_like"], "weekend/holiday", "weekday")
    out["crowd_by_snow"] = (core.pivot_table(index="Resort", columns=["wk", "snow_type"], values="crowd_index",
                                             aggfunc="mean").round(0))
    winners = winners.assign(wk=np.where(winners["weekend_like"], "weekend/holiday", "weekday"))
    out["least_busy_share_by_snow"] = (pd.crosstab(winners["Resort"], [winners["wk"], winners["snow_type"]],
                                                   normalize="columns").mul(100).round(0))
    mnames = {11: "Nov", 12: "Dec", 1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr"}
    hm = h[h["month"].isin(mnames)].assign(Month=lambda d: d["month"].map(mnames))
    out["crowd_by_month"] = (hm.pivot_table(index="Resort", columns="Month", values="crowd_index", aggfunc="mean")
                             .reindex(columns=list(mnames.values())).round(0))
    dn = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    plain = core[core["day_type"].isin(["weekday", "saturday", "sunday"])]
    out["crowd_by_dow_nonholiday"] = (plain.pivot_table(index="Resort", columns="dow", values="crowd_index",
                                                        aggfunc="mean").rename(columns=dict(enumerate(dn))).round(0))
    out["crowd_by_season"] = (h.pivot_table(index="Resort", columns="season", values="crowd_index", aggfunc="mean")
                              .rename(columns=lambda s: f"{s - 1}-{str(s)[2:]}").round(0))
    sysm = core.groupby("date").agg(mean_crowd=("crowd_index", "mean"), day_type=("day_type", "first"),
                                    snow=("snow_type", "first"))
    best = winners.set_index("date")[["Resort", "crowd_index"]].rename(
        columns={"Resort": "least busy", "crowd_index": "its crowd"})
    worst = core.loc[core.groupby("date")["crowd_index"].idxmax()].set_index("date")[["Resort", "crowd_index"]]
    worst.columns = ["busiest", "busiest crowd"]
    days = sysm.join(best).join(worst)
    recent = days[days.index >= days.index.max() - pd.Timedelta(days=3 * 365)]
    out["worst_days_recent"] = recent.sort_values("mean_crowd", ascending=False).head(20).round(0)
    out["best_days_recent"] = recent.sort_values("mean_crowd").head(10).round(0)
    hr = intraday.hourly(core, cfg)
    hr = hr[hr["hour"].isin(intraday.DAY_HOURS)].merge(core[["date", "resort", "day_type"]].drop_duplicates(),
                                                        on=["date", "resort"])
    hr["Resort"] = hr["resort"].map(names)
    hr["Hour"] = hr["hour"].map(intraday.LABEL)
    cols = [intraday.LABEL[h] for h in intraday.DAY_HOURS]
    for label, types in [("crowd_by_hour_weekday", ["weekday"]), ("crowd_by_hour_saturday", ["saturday"]),
                         ("crowd_by_hour_holiday", ["holiday"])]:
        out[label] = (hr[hr["day_type"].isin(types)].pivot_table(index="Resort", columns="Hour",
                      values="crowd_index", aggfunc="mean").reindex(columns=cols).round(0))
    out["overall"] = pd.DataFrame({
        "avg crowd (core season)": core.groupby("Resort")["crowd_index"].mean().round(0),
        "% days least busy": (winners["Resort"].value_counts(normalize=True) * 100).round(0),
        "% days busiest": (core.loc[core.groupby("date")["crowd_index"].idxmax(), "Resort"]
                           .value_counts(normalize=True) * 100).round(0),
    }).fillna(0).sort_values("avg crowd (core season)")
    return out


# ------------------------------------------------------- climatology
def analog_weather(weather: pd.DataFrame, target_dates: pd.DatetimeIndex, analog_season: int) -> pd.DataFrame:
    """Re-label one past season's weather onto the target season's dates."""
    target_season = season_of(target_dates[0].date())
    shift = target_season - analog_season
    frames = []
    for st, g in weather.groupby("station"):
        g = g[(g["date"] >= f"{analog_season - 1}-08-01") & (g["date"] < f"{analog_season}-08-01")].copy()
        g = g[~((g["date"].dt.month == 2) & (g["date"].dt.day == 29))]
        g["date"] = g["date"].apply(lambda d: d.replace(year=d.year + shift))
        frames.append(g)
    w = pd.concat(frames)
    return w[w["date"].isin(target_dates)]


def climatology(cfg: dict, weather: pd.DataFrame, target_season: int, analogs: list[int],
                with_hourly: bool = False):
    """Run the target season's calendar against every past season's weather.
    With `with_hourly`, also returns the mean crowd index by hour block."""
    dates = season_dates(target_season)
    ikon = ikon_resorts(cfg)
    runs = []
    for a in analogs:
        w = analog_weather(weather, dates, a)
        if w.empty:
            continue
        r = add_rankings(run_model(cfg, build_inputs(cfg, dates, w)), ikon)
        r["analog"] = a
        runs.append(r)
    allr = pd.concat(runs, ignore_index=True)
    allr = allr[allr["resort"].isin(ikon)]
    hourly_mean = None
    if with_hourly:
        hr = pd.concat([intraday.hourly(g, cfg) for _, g in allr.groupby("analog")], ignore_index=True)
        hourly_mean = hr.groupby(["date", "resort", "hour"], as_index=False)["crowd_index"].mean()
    g = allr.groupby(["date", "resort"])
    out = pd.DataFrame({
        "p_open": g["open"].mean(),
        "crowd_mean": g["crowd_index"].mean(),
        "crowd_p10": g["crowd_index"].quantile(0.1),
        "crowd_p90": g["crowd_index"].quantile(0.9),
        "p_least_busy": g["rank"].apply(lambda s: float((s == 1).sum()) / len(analogs)),
    }).reset_index()
    return (out, hourly_mean) if with_hourly else out


# ------------------------------------------------------------ predict
def predict_day(cfg: dict, weather: pd.DataFrame, date: dt.date, forecast: pd.DataFrame | None = None,
                analogs: list[int] | None = None) -> tuple[pd.DataFrame, str, pd.DataFrame]:
    """Rank Ikon resorts for one date. Uses the forecast when available,
    otherwise the climatological ensemble. Also returns crowd by hour."""
    ikon = ikon_resorts(cfg)
    season = season_of(date)
    ts = pd.Timestamp(date)
    if forecast is not None and ts in set(forecast["date"]):
        dates = pd.date_range(f"{season - 1}-10-15", ts, freq="D")
        # base depth after the last observation: carry forward + 35% of new snow
        last_obs = weather.groupby("station")["date"].max()
        ov = forecast[forecast["date"] <= ts].copy()
        ov["base"] = np.nan
        for r in ikon + [k for k in cfg["resorts"] if k not in ikon]:
            st = cfg["resorts"][r]["station"]
            wst = weather[weather["station"] == st]
            if wst.empty:
                continue
            last = wst.loc[wst["date"].idxmax()]
            m = (ov["resort"] == r) & (ov["date"] > last_obs[st])
            ov.loc[m, "base"] = last["base"] + 0.35 * ov.loc[m, "new_24"].cumsum()
        res = add_rankings(run_model(cfg, build_inputs(cfg, dates, weather, resort_overrides=ov)), ikon)
        day = res[(res["date"] == ts) & res["resort"].isin(ikon)].copy()
        day["p_least_busy"] = (day["rank"] == 1).astype(float)
        day["p_open"] = day["open"].astype(float)
        return day.sort_values("crowd_index"), "forecast", intraday.hourly(day, cfg)
    last_obs = weather["date"].max()
    if season_of(last_obs.date()) == season and 0 <= (ts - last_obs).days <= 15:
        # No forecast, but this season's snowpack is measured: carry it
        # forward assuming no new snow (a conservative persistence forecast).
        dates = pd.date_range(f"{season - 1}-10-15", ts, freq="D")
        ahead = pd.date_range(last_obs + pd.Timedelta(days=1), ts, freq="D")
        rows = []
        for st, g in weather[weather["date"] > last_obs - pd.Timedelta(days=7)].groupby("station"):
            last = g.loc[g["date"].idxmax()]
            for d in ahead:
                rows.append({"date": d, "station": st, "new_24": 0.0, "new_48": 0.0, "base": last["base"],
                             "temp_ski": round(g["temp_ski"].mean(), 1), "rain": 0, "swe_24": 0.0})
        w = pd.concat([weather, pd.DataFrame(rows)], ignore_index=True)
        res = add_rankings(run_model(cfg, build_inputs(cfg, dates, w)), ikon)
        day = res[(res["date"] == ts) & res["resort"].isin(ikon)].copy()
        day["p_least_busy"] = (day["rank"] == 1).astype(float)
        day["p_open"] = day["open"].astype(float)
        return day.sort_values("crowd_index"), "measured snowpack to date, assuming no new snow", intraday.hourly(day, cfg)
    analogs = analogs or sorted(weather["date"].map(lambda d: season_of(d.date())).unique())[:-1]
    clim, hr = climatology(cfg, weather, season, analogs, with_hourly=True)
    day = clim[clim["date"] == ts].rename(columns={"crowd_mean": "crowd_index"})
    return day.sort_values("crowd_index"), "climatology", hr[hr["date"] == ts]
