"""UDOT traffic counts -> crowd observations for calibration.

Source: UDOT's public ArcGIS feature services (no key needed)
  * CCS_Data / CCS_Hourly  - hourly vehicle counts by direction from the
    permanent Continuous Count Stations (currently Jan 2022 - Mar 2025).
  * CC_Volume              - live "net vehicles currently in the canyon" for
    Little and Big Cottonwood (logged going forward by the GitHub Action).

The ski signal we extract is the *up-canyon (toward the resort) volume
between 5am and 2pm*: that is mostly skiers arriving, and including the late
morning means skiers who waited out an avalanche closure are still counted.
"""
from __future__ import annotations

import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
UDOT_DIR = ROOT / "data" / "udot"
HOURLY_PATH = UDOT_DIR / "ccs_hourly.csv"
DAILY_PATH = UDOT_DIR / "ccs_ski_daily.csv"
LIVE_PATH = UDOT_DIR / "canyon_live.csv"

SERVICES = "https://services.arcgis.com/pA2nEVnB6tquxgOW/arcgis/rest/services"
HOURLY_LAYERS = [f"{SERVICES}/CCS_Data/FeatureServer/5", f"{SERVICES}/CCS_Data_v2/FeatureServer/7"]
LIVE_LAYER = f"{SERVICES}/CC_Volume/FeatureServer/0"

# station -> what it measures. `target` is a canyon key or resort key from
# config; the calibration compares against modelled visitors there.
STATIONS = {
    317: {"target": "LCC", "label": "SR-210 mouth of Little Cottonwood (Alta + Snowbird)"},
    322: {"target": "BCC", "label": "SR-190 mouth of Big Cottonwood (Brighton + Solitude)"},
    822: {"target": "brighton", "label": "SR-190 end of Brighton Loop (Brighton only)"},
    824: {"target": "deer_valley", "label": "SR-224 Deer Valley Dr (Deer Valley + Park City town)"},
    609: {"target": "OGDEN", "label": "SR-167 Trappers Loop (Snowbasin from the south)"},
    320: {"target": "OGDEN", "label": "SR-39 Ogden Canyon (Ogden Valley: Snowbasin, Powder Mtn)"},
    605: {"target": "park_city", "label": "SR-224 at Canyons Resort Dr (Park City Mountain)"},
}
AM_HOURS = [f"Z{h:02d}00" for h in range(5, 14)]  # 05:00-13:59
ALL_HOURS = [f"Z{h:02d}00" for h in range(24)]


def _get_json(url: str, params: dict, tries: int = 4) -> dict:
    q = urllib.parse.urlencode(params)
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(f"{url}?{q}", timeout=120) as r:
                js = json.loads(r.read())
            if "error" in js:
                raise RuntimeError(js["error"])
            return js
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 ** i)
    raise RuntimeError(f"UDOT query failed: {last}")


def fetch_hourly(stations=tuple(STATIONS), verbose: bool = True) -> pd.DataFrame:
    """Download every hourly row for the stations (both service versions;
    v1 preferred where both have the day, v2 fills gaps)."""
    frames = []
    for prio, layer in enumerate(HOURLY_LAYERS):
        for st in stations:
            offset, rows = 0, []
            while True:
                js = _get_json(f"{layer}/query", {
                    "where": f"STATION = {-abs(st)}", "outFields": ",".join(["STATION", "DATE", "DIRECTION", "HOLIDAY"] + ALL_HOURS),
                    "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": 2000,
                    "returnGeometry": "false", "f": "json",
                })
                feats = [f["attributes"] for f in js.get("features", [])]
                rows += feats
                if not js.get("exceededTransferLimit") and len(feats) < 2000:
                    break
                offset += len(feats)
            if verbose:
                print(f"  station {st}: {len(rows):,} rows from {layer.split('/')[-3]}")
            if rows:
                df = pd.DataFrame(rows)
                df["priority"] = prio
                frames.append(df)
    raw = pd.concat(frames, ignore_index=True)
    raw["station"] = raw["STATION"].abs().astype(int)
    raw["date"] = pd.to_datetime(raw["DATE"], unit="ms").dt.normalize()
    raw = raw.rename(columns={"DIRECTION": "direction", "HOLIDAY": "holiday"})
    # v2 has scattered missing hours; prefer complete v1 rows
    raw["n_missing"] = raw[ALL_HOURS].isna().sum(axis=1)
    raw = (raw.sort_values(["station", "date", "direction", "n_missing", "priority"])
              .drop_duplicates(["station", "date", "direction"], keep="first"))
    out = raw[["station", "date", "direction", "holiday"] + ALL_HOURS].sort_values(["station", "date", "direction"])
    UDOT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(HOURLY_PATH, index=False)
    return out


def up_direction(hourly: pd.DataFrame) -> dict[int, str]:
    """Which direction heads toward the resorts: on winter weekend days, the
    up-canyon direction carries far more traffic in the morning than in the
    late afternoon (and vice versa)."""
    h = hourly[hourly["date"].dt.month.isin([1, 2, 3, 12]) & (hourly["date"].dt.weekday >= 5)]
    out = {}
    for st, g in h.groupby("station"):
        am = g.groupby("direction")[["Z0700", "Z0800", "Z0900"]].sum().sum(axis=1)
        pm = g.groupby("direction")[["Z1500", "Z1600", "Z1700"]].sum().sum(axis=1)
        out[int(st)] = (am / pm.clip(lower=1)).idxmax()
    return out


def ski_daily(hourly: pd.DataFrame | None = None, out_path: Path | None = DAILY_PATH) -> pd.DataFrame:
    """One row per station-day: up-direction vehicles 05:00-13:59, with
    quality flags (sensor gaps, suspected all-morning road closures)."""
    if hourly is None:
        hourly = pd.read_csv(HOURLY_PATH, parse_dates=["date"])
    ups = up_direction(hourly)
    rows = []
    for st, g in hourly.groupby("station"):
        u = g[g["direction"] == ups[int(st)]].set_index("date")
        am = u[AM_HOURS]
        d = pd.DataFrame({
            "station": int(st), "target": STATIONS[int(st)]["target"], "up_dir": ups[int(st)],
            "am_up": am.sum(axis=1, min_count=len(AM_HOURS)),
            "complete": am.notna().all(axis=1) & (am.sum(axis=1) > 0),
        })
        rows.append(d)
    daily = pd.concat(rows).reset_index()
    # flag likely closures/outages: far below the typical value for that
    # station, month and weekday-vs-weekend
    daily["wk"] = daily["date"].dt.weekday >= 5
    daily["m"] = daily["date"].dt.month
    typ = daily[daily["complete"]].groupby(["station", "m", "wk"])["am_up"].median().rename("typical")
    daily = daily.join(typ, on=["station", "m", "wk"])
    daily["low_outlier"] = daily["am_up"] < 0.35 * daily["typical"]
    daily = daily.drop(columns=["wk", "m"])
    if out_path is not None:
        daily.to_csv(out_path, index=False)
    return daily


def to_observations(daily: pd.DataFrame | None = None, months=(11, 12, 1, 2, 3, 4)) -> pd.DataFrame:
    """Calibration rows. Canyon stations -> kind `canyon_vehicles`; stations
    that serve one resort -> kind `resort_vehicles`. Each station is its own
    `source`, so it gets its own intercept (non-ski traffic) and slope (car
    occupancy, share of skiers)."""
    if daily is None:
        daily = pd.read_csv(DAILY_PATH, parse_dates=["date"])
    d = daily[daily["complete"] & ~daily["low_outlier"] & daily["date"].dt.month.isin(months)].copy()
    d["kind"] = np.where(d["target"].str.isupper(), "canyon_vehicles", "resort_vehicles")
    # Park City stations (605, 824) carry mostly town/commuter traffic - lower
    # on weekends than weekdays - so they don't measure skiing. Kept in the
    # data for reference, excluded from training (weight 0).
    weights = {317: 1.0, 322: 1.0, 822: 1.0, 609: 0.6, 320: 0.4, 824: 0.0, 605: 0.0}
    d = d[d["station"].map(weights) > 0]
    return pd.DataFrame({
        "date": d["date"], "resort": d["target"], "kind": d["kind"], "value": d["am_up"],
        "weight": d["station"].map(weights), "source": "udot_" + d["station"].astype(str),
        "notes": "UDOT CCS up-direction vehicles 05:00-13:59",
    }).reset_index(drop=True)


# --------------------------------------------------------------- live feed
def fetch_live() -> pd.DataFrame:
    """Current net vehicles in Little / Big Cottonwood (refreshes every few min)."""
    js = _get_json(f"{LIVE_LAYER}/query", {"where": "1=1", "outFields": "*", "returnGeometry": "false", "f": "json"})
    rows = []
    for f in js.get("features", []):
        a = f["attributes"]
        rows.append({
            "observed_at": dt.datetime.fromtimestamp(a["LATEST_DATETIME"] / 1000, dt.timezone.utc).isoformat(),
            "canyon": {"210": "LCC", "190": "BCC"}.get(str(a["FREEWAY_ID"]).strip(), a["FREEWAY_ID"]),
            "net_vehicles_in_canyon": a["NET_VOLUME_ON_ROUTE"],
        })
    return pd.DataFrame(rows)
