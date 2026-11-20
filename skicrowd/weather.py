"""Snow & weather inputs.

History  : USDA NRCS SNOTEL hourly data (measured on the mountain).
Forecast : Open-Meteo (free, no key) for the next ~16 days.

Everything is reduced to the same daily features, measured as of 6 am
(what a skier sees when deciding where to go):

    new_24   inches of new snow in the previous 24 h
    new_48   inches in the previous 48 h
    base     snow depth (in)
    temp_ski mean air temp 9am-3pm (F)
    rain     1 if meaningful precipitation fell above freezing during the day
    wind     forecast max gust (mph) -- not in SNOTEL, so 0 for history
"""
from __future__ import annotations

import io
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DAILY_PATH = ROOT / "data" / "weather_daily.csv"

SNOTEL_URL = (
    "https://wcc.sc.egov.usda.gov/reportGenerator/view_csv/customSingleStationReport/"
    "hourly/start_of_period/{triplet}%7Cid=%22%22%7Cname/{start},{end}/"
    "WTEQ::value,SNWD::value,PREC::value,TOBS::value"
)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Resort summit-ish coordinates for forecasts (lat, lon, elevation m)
RESORT_POINTS = {
    "alta": (40.588, -111.638, 2900),
    "snowbird": (40.581, -111.657, 2950),
    "brighton": (40.598, -111.583, 2750),
    "solitude": (40.620, -111.592, 2800),
    "deer_valley": (40.637, -111.478, 2600),
    "snowbasin": (41.216, -111.857, 2400),
    "park_city": (40.651, -111.508, 2600),
    "powder_mountain": (41.379, -111.781, 2600),
}


def _get(url: str, timeout: int = 90, tries: int = 4) -> bytes:
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # network hiccup -> back off
            last = e
            time.sleep(2 ** i)
    raise RuntimeError(f"fetch failed: {url}\n{last}")


# --------------------------------------------------------------------- SNOTEL
def fetch_snotel_hourly(triplet: str, season: int, refresh: bool = False) -> pd.DataFrame:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"snotel_{triplet.replace(':', '_')}_{season}.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, parse_dates=["time"])
    start, end = f"{season - 1}-10-01", f"{season}-06-10"
    raw = _get(SNOTEL_URL.format(triplet=triplet, start=start, end=end)).decode()
    lines = [ln for ln in raw.splitlines() if ln and not ln.startswith("#")]
    df = pd.read_csv(io.StringIO("\n".join(lines)))
    df.columns = ["time", "swe", "depth", "precip", "temp"]
    df["time"] = pd.to_datetime(df["time"])
    df.to_csv(path, index=False)
    return df


def _clean(series: pd.Series, max_jump: float) -> pd.Series:
    """Remove sensor spikes: values far from a rolling median are dropped."""
    med = series.rolling(7, center=True, min_periods=1).median()
    s = series.where((series - med).abs() <= max_jump)
    return s.interpolate(limit=6).ffill().bfill()


def hourly_to_daily(h: pd.DataFrame) -> pd.DataFrame:
    """Hourly SNOTEL -> 6am daily features.

    New snow is estimated from the precipitation gauge (water) x a
    temperature-dependent snow:liquid ratio, cross-checked against the
    rise in snow depth. Both sensors jitter by an inch or so, so small
    changes are treated as noise.
    """
    h = h.set_index("time").sort_index()
    h = h[~h.index.duplicated()].asfreq("h")
    swe = _clean(h["swe"], 1.0).clip(lower=0)
    # Depth sensors sometimes stick at an error value for weeks. A snowpack
    # can't be much fluffier than ~9% density, so reject impossible depths
    # and fall back to SWE x 3.3 (typical settled density) where needed.
    raw_depth = h["depth"].where((h["depth"] <= swe * 11 + 15) & (h["depth"] <= 200) & (h["depth"] >= 0))
    depth = _clean(raw_depth.interpolate(limit=48), 6)
    depth = depth.where(raw_depth.interpolate(limit=48).notna(), swe * 3.3)
    depth = depth.rolling(5, center=True, min_periods=1).median()
    prec = _clean(h["precip"], 1.0).cummax()   # accumulation gauge never decreases
    temp = _clean(h["temp"], 15)

    depth_gain = depth - depth.rolling(25, min_periods=1).min()
    water = (prec - prec.shift(24)).clip(lower=0)
    swe_gain = (swe - swe.shift(24)).clip(lower=0)
    t24 = temp.rolling(25, min_periods=1).mean()

    six = h.index[h.index.hour == 6]
    f = pd.DataFrame({
        "depth": depth.reindex(six), "depth_gain": depth_gain.reindex(six),
        "water": water.reindex(six), "swe_gain": swe_gain.reindex(six), "t24": t24.reindex(six),
    })
    f.index = f.index.normalize()
    ratio = np.select([f["t24"] < 20, f["t24"] < 27, f["t24"] < 33], [14.0, 12.0, 9.0], 0.0)
    water_used = f["water"].where(f["water"].notna(), f["swe_gain"]).fillna(0)
    water_used = water_used.where(water_used >= 0.05, 0.0)
    dg = f["depth_gain"].fillna(0)
    dg = dg.where(dg >= 2.5, 0.0)
    from_water = water_used * ratio
    # if the gauge says it snowed, trust the larger estimate; a depth rise
    # with no water is only believed when it's large (wind loading / gauge undercatch)
    new = np.where(water_used > 0, np.maximum(from_water, dg), np.where(dg >= 5, dg, 0.0))

    hrs = temp.index.hour
    ski_t = temp[(hrs >= 9) & (hrs <= 15)].groupby(lambda t: t.normalize()).mean()
    day_p = prec[(hrs >= 7) & (hrs <= 16)].groupby(lambda t: t.normalize()).agg(lambda s: s.iloc[-1] - s.iloc[0])
    df = pd.DataFrame({
        "new_24": np.clip(new, 0, 40).round(1),
        "base": f["depth"].round(1),
        "swe_24": water_used.round(2),
        "temp_ski": ski_t.reindex(f.index).fillna(f["t24"]).round(1),
    }, index=f.index)
    df["rain"] = ((day_p.reindex(f.index).fillna(0) >= 0.15) & (df["temp_ski"] >= 34)).astype(int)
    df = df[df["base"].notna()]
    df.index.name = "date"
    df["new_48"] = (df["new_24"] + df["new_24"].shift(1).fillna(0)).round(1)
    return df


def build_daily_history(cfg: dict, seasons: list[int], refresh: bool = False, verbose: bool = True) -> pd.DataFrame:
    stations = sorted({r["station"] for r in cfg["resorts"].values()} |
                      {c["station"] for c in cfg["canyons"].values()})
    frames = []
    for st in stations:
        for s in seasons:
            if verbose:
                print(f"  SNOTEL {st} season {s - 1}-{str(s)[2:]}", flush=True)
            try:
                h = fetch_snotel_hourly(st, s, refresh=refresh)
            except RuntimeError as e:
                print(f"    ! {e}")
                continue
            if h.empty:
                continue
            d = hourly_to_daily(h)
            d["station"] = st
            frames.append(d.reset_index())
    daily = pd.concat(frames, ignore_index=True)
    daily = daily.sort_values(["station", "date"]).drop_duplicates(["station", "date"], keep="last")
    DAILY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DAILY_PATH.exists():
        old = pd.read_csv(DAILY_PATH, parse_dates=["date"])
        daily = (pd.concat([old, daily]).sort_values(["station", "date"])
                 .drop_duplicates(["station", "date"], keep="last"))
    daily.to_csv(DAILY_PATH, index=False)
    return daily


def load_daily_history() -> pd.DataFrame:
    if not DAILY_PATH.exists():
        raise FileNotFoundError("No weather history yet - run `python -m skicrowd fetch` first.")
    return pd.read_csv(DAILY_PATH, parse_dates=["date"])


# ------------------------------------------------------------------ Forecast
def fetch_forecast(resorts: list[str], days: int = 16) -> pd.DataFrame:
    """Open-Meteo hourly forecast -> same daily features as history (per resort)."""
    rows = []
    for r in resorts:
        lat, lon, elev = RESORT_POINTS[r]
        q = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "elevation": elev,
            "hourly": "snowfall,temperature_2m,precipitation,wind_gusts_10m",
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
            "precipitation_unit": "inch", "timezone": "America/Denver",
            "forecast_days": days, "past_days": 2,
        })
        js = json.loads(_get(f"{OPEN_METEO_URL}?{q}", timeout=30, tries=2))
        if js.get("error"):
            raise RuntimeError(js.get("reason"))
        h = pd.DataFrame(js["hourly"])
        h["time"] = pd.to_datetime(h["time"])
        h = h.set_index("time")
        snow_in = h["snowfall"] / 2.54  # cm -> in
        for d in pd.date_range(h.index.min().normalize() + pd.Timedelta(days=1), h.index.max().normalize()):
            t6 = d + pd.Timedelta(hours=6)
            n24 = float(snow_in[t6 - pd.Timedelta(hours=23): t6].sum())
            n48 = float(snow_in[t6 - pd.Timedelta(hours=47): t6].sum())
            ski = h.loc[d + pd.Timedelta(hours=9): d + pd.Timedelta(hours=15)]
            rows.append({
                "date": d, "resort": r, "new_24": round(n24, 1), "new_48": round(n48, 1),
                "temp_ski": round(float(ski["temperature_2m"].mean()), 1),
                "rain": int(ski["precipitation"].sum() >= 0.15 and ski["temperature_2m"].mean() >= 34),
                "wind": round(float(ski["wind_gusts_10m"].max()), 0),
            })
    return pd.DataFrame(rows)
