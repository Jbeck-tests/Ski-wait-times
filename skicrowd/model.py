"""The crowd model: a daily demand -> resort-choice equilibrium.

For every day:

1. Each skier *segment* (SLC locals, Ogden locals, Park City locals, two
   destination-visitor groups) has a pool of intending skiers set by the
   calendar (weekend, school breaks, destination travel seasons) and the
   weather (powder pulls locals out, cold / rain / thin base keeps them home).

2. Every skier picks a resort - or stays home - with a multinomial-logit
   choice. Utility rises with pass/brand affinity and fresh snow, and falls
   with drive time, canyon-road delays (congestion + avalanche closures) and
   with how crowded the resort is expected to be.

3. Because crowding feeds back into choice (people avoid the zoo), the model
   iterates to an equilibrium. Parking-reservation / ticket caps are enforced
   with a shadow price that pushes excess demand to other resorts.

Output per resort-day: visitors, load (= visitors / effective capacity), and
crowd index (= 100 x load; 100 == at comfortable capacity).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .calendar_features import calendar_frame, local_multiplier, season_of
from .config import season_value

WEATHER_COLS = ["new_24", "new_48", "base", "temp_ski", "rain", "wind"]


@dataclass
class ModelInputs:
    dates: pd.DatetimeIndex
    cal: pd.DataFrame
    resorts: list[str]
    segments: list[str]
    canyons: list[str]
    wx: dict[str, np.ndarray]           # each [D, R]
    canyon_new24: np.ndarray            # [D, C]
    region_wx: dict[str, np.ndarray]    # each [D, S]


def _station_frame(weather: pd.DataFrame, station: str, dates: pd.DatetimeIndex) -> pd.DataFrame:
    w = weather[weather["station"] == station].set_index("date").reindex(dates)
    for c in WEATHER_COLS:
        if c not in w:
            w[c] = np.nan
    w["base"] = w["base"].ffill().bfill().fillna(0)
    w["temp_ski"] = w["temp_ski"].fillna(25)
    return w[WEATHER_COLS].fillna(0)


def build_inputs(cfg: dict, dates: pd.DatetimeIndex, weather: pd.DataFrame,
                 resort_overrides: pd.DataFrame | None = None) -> ModelInputs:
    """weather: station-level daily history (date, station, features...).
    resort_overrides: optional per-resort rows (date, resort, features) that
    replace station values - used for forecasts."""
    resorts = list(cfg["resorts"])
    segments = list(cfg["segments"])
    canyons = list(cfg["canyons"])
    D, R = len(dates), len(resorts)
    wx = {c: np.zeros((D, R)) for c in WEATHER_COLS}
    cache: dict[str, pd.DataFrame] = {}
    for j, r in enumerate(resorts):
        st = cfg["resorts"][r]["station"]
        if st not in cache:
            cache[st] = _station_frame(weather, st, dates)
        w = cache[st].copy()
        if resort_overrides is not None and len(resort_overrides):
            o = resort_overrides[resort_overrides["resort"] == r].set_index("date").reindex(dates)
            for c in WEATHER_COLS:
                if c in o:
                    w[c] = o[c].where(o[c].notna(), w[c])
        for c in WEATHER_COLS:
            wx[c][:, j] = w[c].to_numpy(dtype=float)

    canyon_new24 = np.zeros((D, len(canyons)))
    canyon_frames = {}
    for k, c in enumerate(canyons):
        members = [j for j, r in enumerate(resorts) if cfg["resorts"][r]["canyon"] == c]
        canyon_new24[:, k] = wx["new_24"][:, members].max(axis=1) if members else 0
        canyon_frames[c] = members

    # Weather each segment "sees" when deciding whether to ski at all.
    region_wx = {k: np.zeros((D, len(segments))) for k in ("powder", "temp", "base", "rain", "wind")}
    for i, s in enumerate(segments):
        members = sorted({j for c in cfg["segments"][s]["powder_canyons"] for j in canyon_frames[c]})
        pw = wx["new_24"][:, members] + 0.5 * (wx["new_48"][:, members] - wx["new_24"][:, members])
        region_wx["powder"][:, i] = pw.max(axis=1)
        region_wx["temp"][:, i] = wx["temp_ski"][:, members].mean(axis=1)
        region_wx["base"][:, i] = wx["base"][:, members].max(axis=1)
        region_wx["rain"][:, i] = wx["rain"][:, members].max(axis=1)
        region_wx["wind"][:, i] = wx["wind"][:, members].max(axis=1)

    return ModelInputs(dates, calendar_frame(dates), resorts, segments, canyons, wx, canyon_new24, region_wx)


def _open_and_terrain(cfg: dict, inp: ModelInputs) -> tuple[np.ndarray, np.ndarray]:
    D, R = len(inp.dates), len(inp.resorts)
    is_open = np.zeros((D, R), bool)
    terrain = np.ones((D, R))
    seasons = inp.cal["season"].to_numpy()
    mmdd = inp.dates.strftime("%m-%d").to_numpy()
    # month-day order within a season (Aug..Jul)
    order = np.where(inp.dates.month >= 8, 0, 1).astype(str).astype(object) + mmdd

    def key(md: str) -> str:
        return ("0" if int(md[:2]) >= 8 else "1") + md

    for j, r in enumerate(inp.resorts):
        rc = cfg["resorts"][r]
        thr = rc["open_depth_in"] * (0.6 if rc.get("snowmaking") else 1.0)
        base = inp.wx["base"][:, j]
        in_window = (order >= key(rc["open_mmdd"])) & (order <= key(rc["close_mmdd"]))
        for s in np.unique(seasons):
            m = (seasons == s) & in_window
            idx = np.where(m)[0]
            if not len(idx):
                continue
            ok = np.maximum.accumulate(base[idx] >= thr)  # once open, stays open
            ok &= base[idx] >= 4
            is_open[idx, j] = ok
        full = max(70.0, thr + 30)
        terrain[:, j] = np.clip(0.35 + 0.65 * (base - thr) / (full - thr), 0.35, 1.0)
    _apply_operating_status(cfg, inp, is_open, before_close={
        r: order <= key(cfg["resorts"][r]["close_mmdd"]) for r in inp.resorts})
    return is_open, terrain


def _apply_operating_status(cfg: dict, inp: ModelInputs, is_open: np.ndarray, before_close: dict) -> None:
    """Override snow-based open/closed guesses with announced status
    (skicrowd.status) for the season it was observed in."""
    st = cfg.get("operating")
    if not st:
        return
    season = int(st["season"])
    as_of = np.datetime64(st["as_of"])
    dates = inp.dates.to_numpy().astype("datetime64[D]")
    in_season = inp.cal["season"].to_numpy() == season
    if not in_season.any():
        return
    for j, r in enumerate(inp.resorts):
        info = st["resorts"].get(r)
        if not info:
            continue
        window = before_close[r]
        col = is_open[:, j]
        kind = info["status"]
        if kind == "open":
            m = in_season & (dates >= as_of)
            col[m] = window[m]
        elif kind == "opening" and info.get("date"):
            od = np.datetime64(info["date"])
            col[in_season & (dates < od)] = False
            m = in_season & (dates >= od)
            col[m] = window[m]
        elif kind == "tba":
            col[in_season & (dates <= as_of)] = False
        elif kind == "closed":
            col[in_season & (dates >= as_of)] = False
        if info.get("closing"):
            col[in_season & (dates > np.datetime64(info["closing"]))] = False
        is_open[:, j] = col


def run_model(cfg: dict, inp: ModelInputs, iters: int = 200) -> pd.DataFrame:
    beh = cfg["behaviour"]
    R, S, C = len(inp.resorts), len(inp.segments), len(inp.canyons)
    D = len(inp.dates)
    seasons = inp.cal["season"].to_numpy()

    # ---- capacities ------------------------------------------------------
    is_open, terrain = _open_and_terrain(cfg, inp)
    cap = np.zeros((D, R))
    hard = np.zeros((D, R))
    for j, r in enumerate(inp.resorts):
        rc = cfg["resorts"][r]
        for s in np.unique(seasons):
            m = seasons == s
            cap[m, j] = season_value(rc, "capacity", int(s)) * rc.get("capacity_scale", 1.0)
            hard[m, j] = season_value(rc, "hard_cap", int(s)) * rc.get("capacity_scale", 1.0)
    windy = inp.wx["wind"] >= beh["wind_gust_mph"]
    eff_cap = cap * terrain ** 0.8 * np.where(windy, beh["wind_capacity_factor"], 1.0)
    eff_cap = np.maximum(eff_cap, 1.0)
    hard_eff = hard * np.clip(terrain, 0.55, 1.0)

    # ---- intending skiers per segment -----------------------------------
    N = np.zeros((D, S))
    month = inp.cal["month"].to_numpy()
    mfac = np.array([beh["local_month_factor"].get(str(m), 0.6) for m in month])
    dest = inp.cal["dest_mult"].to_numpy()
    sat = inp.cal["is_saturday"].to_numpy()
    sun = inp.cal["is_sunday"].to_numpy()
    for i, s in enumerate(inp.segments):
        sc = cfg["segments"][s]
        if sc["kind"] == "local":
            calm = local_multiplier(inp.cal, sc) * mfac
        else:
            calm = dest * np.where(sat, sc["saturday"], np.where(sun, sc["sunday"], 1.0))
        pw = inp.region_wx["powder"][:, i] * beh.get("snow_report_factor", 1.0)
        pfac = np.minimum(1 + sc["powder_participation"] * pw, sc["powder_participation_cap"])
        t = inp.region_wx["temp"][:, i]
        wfac = np.where(t < beh["very_cold_temp_f"], beh["very_cold_factor"],
                        np.where(t < beh["cold_temp_f"], beh["cold_factor"], 1.0))
        if sc["kind"] != "local":  # visitors on vacation ski anyway, mostly
            wfac = 1 - 0.35 * (1 - wfac)
        wfac = wfac * np.where(inp.region_wx["rain"][:, i] > 0, beh["rain_factor"], 1.0)
        wfac = wfac * np.where(inp.region_wx["wind"][:, i] >= beh["wind_gust_mph"],
                               beh["wind_participation_factor"], 1.0)
        if sc["kind"] == "local":
            wfac = wfac * np.where(inp.region_wx["base"][:, i] < beh["thin_base_in"], beh["thin_base_factor"], 1.0)
        N[:, i] = sc["base"] * calm * pfac * wfac

    # ---- static utility pieces ------------------------------------------
    aff = np.zeros((S, R))
    travel = np.zeros((S, R))
    b_pow = np.zeros(S)
    b_tt = np.zeros(S)
    home = np.zeros(S)
    for i, s in enumerate(inp.segments):
        sc = cfg["segments"][s]
        b_pow[i], b_tt[i], home[i] = sc["beta_powder"], sc["beta_travel"], sc["home_utility"]
        for j, r in enumerate(inp.resorts):
            aff[i, j] = sc.get("affinity", {}).get(r, sc["affinity_default"])
            travel[i, j] = sc["travel_min"][r]
    srf = beh.get("snow_report_factor", 1.0)
    powder = srf * (inp.wx["new_24"] + 0.5 * (inp.wx["new_48"] - inp.wx["new_24"]))

    # Pass lock-in: most people ski where their pass is. When resorts a group
    # normally uses are closed (early/late season), that part of the group
    # mostly stays home rather than buying tickets elsewhere.
    base_u = aff - b_tt[:, None] * travel / 60
    base_share = np.exp(base_u) / np.exp(base_u).sum(axis=1, keepdims=True)   # [S,R]
    availability = is_open.astype(float) @ base_share.T                         # [D,S]
    lock = np.array([beh.get("pass_lockin_local", 1.0) if cfg["segments"][s]["kind"] == "local"
                     else beh.get("pass_lockin_destination", 0.5) for s in inp.segments])
    N = N * availability ** lock[None, :]
    bonus = np.array([cfg["resorts"][r]["powder_bonus"] for r in inp.resorts])
    snow_u = (b_pow[None, :, None] * np.minimum(powder, 24)[:, None, :]
              + (bonus[None, :] * np.minimum(powder / 12, 1))[:, None, :])
    static_u = aff[None] + snow_u - b_tt[None, :, None] * travel[None] / 60  # [D,S,R]
    static_u = np.where(is_open[:, None, :], static_u, -np.inf)

    # canyon membership + closure delay (exogenous, weather-driven)
    can_idx = np.array([inp.canyons.index(cfg["resorts"][r]["canyon"]) for r in inp.resorts])
    member = np.zeros((C, R))
    member[can_idx, np.arange(R)] = 1
    cc = [cfg["canyons"][c] for c in inp.canyons]
    closure = np.stack([
        k["closure_delay_min"] / (1 + np.exp(-(inp.canyon_new24[:, ci] * beh.get("snow_report_factor", 1.0) - k["closure_snow_in"]) / k["closure_spread_in"]))
        for ci, k in enumerate(cc)], axis=1) if C else np.zeros((D, 0))  # [D,C]
    car_share = np.array([k["car_share"] for k in cc])
    pcap = np.array([k["person_capacity"] for k in cc])
    ff = np.array([k["free_flow_min"] for k in cc])
    alpha = np.array([k["bpr_alpha"] for k in cc])
    beta = np.array([k["bpr_beta"] for k in cc])

    # ---- equilibrium iteration ------------------------------------------
    load = np.zeros((D, R))
    cong = np.zeros((D, C))
    lam = np.zeros((D, R))
    bc = beh["beta_crowd"]
    for k in range(iters):
        delay = cong + closure                                     # [D,C]
        u = static_u - (b_tt[None, :, None] * delay[:, can_idx][:, None, :] / 60) \
            - bc * load[:, None, :] - lam[:, None, :]
        logits = np.concatenate([u, np.broadcast_to(home[None, :, None], (D, S, 1))], axis=2)
        mx = logits.max(axis=2, keepdims=True)
        e = np.exp(logits - mx)
        p = e / e.sum(axis=2, keepdims=True)
        visitors = (N[:, :, None] * p[:, :, :R]).sum(axis=1)     # [D,R]
        new_load = visitors / eff_cap
        flow = visitors @ member.T * car_share                    # [D,C]
        new_cong = np.minimum(ff * alpha * (flow / pcap) ** beta, 180)
        # method of successive averages: converges for congestion games
        step = 1.0 if k == 0 else max(1.0 / (k + 1), 0.02)
        load = load + step * (new_load - load)
        cong = cong + step * (new_cong - cong)
        over = np.where(is_open, visitors / np.maximum(hard_eff, 1) - 1, 0)
        lam = np.maximum(0, lam + beh["cap_shadow_step"] * step * 4 * over)

    delay = cong + closure
    out = []
    for j, r in enumerate(inp.resorts):
        out.append(pd.DataFrame({
            "date": inp.dates, "resort": r, "open": is_open[:, j],
            "visitors": np.round(visitors[:, j]), "capacity": np.round(eff_cap[:, j]),
            "load": load[:, j], "crowd_index": np.round(100 * load[:, j], 1),
            "road_delay_min": np.round(delay[:, can_idx[j]], 1),
            "new_24": inp.wx["new_24"][:, j], "new_48": inp.wx["new_48"][:, j],
            "base": inp.wx["base"][:, j], "temp_ski": inp.wx["temp_ski"][:, j],
        }))
    res = pd.concat(out, ignore_index=True)
    res.loc[~res["open"], ["visitors", "load", "crowd_index"]] = np.nan
    return res


def add_rankings(res: pd.DataFrame, ikon: list[str]) -> pd.DataFrame:
    res = res.copy()
    m = res["resort"].isin(ikon) & res["open"]
    res["rank"] = np.nan
    res.loc[m, "rank"] = res[m].groupby("date")["crowd_index"].rank(method="first")
    return res


def crowd_label(ci: float) -> str:
    if ci != ci:
        return "closed"
    if ci < 50:
        return "quiet"
    if ci < 80:
        return "moderate"
    if ci < 110:
        return "busy"
    return "packed"


__all__ = ["build_inputs", "run_model", "add_rankings", "crowd_label", "season_of"]
