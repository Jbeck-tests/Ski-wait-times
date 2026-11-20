import datetime as dt

import numpy as np
import pandas as pd
import pytest

from skicrowd.calendar_features import calendar_frame, day_flags, key_dates, season_dates, season_of
from skicrowd.calibrate import ParamSpec, data_nll, fit
from skicrowd.config import apply_overrides, ikon_resorts, load_config
from skicrowd.model import add_rankings, build_inputs, run_model
from skicrowd.weather import hourly_to_daily


# ------------------------------------------------------------------ helpers
def synthetic_weather(cfg, dates, snow_days=(), base=80.0):
    stations = {r["station"] for r in cfg["resorts"].values()} | {c["station"] for c in cfg["canyons"].values()}
    rows = []
    for st in stations:
        for d in dates:
            n = 14.0 if d in snow_days else 0.0
            rows.append({"date": d, "station": st, "new_24": n, "new_48": n, "base": base,
                         "temp_ski": 25.0, "rain": 0, "swe_24": n / 12, "wind": 0})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def cfg():
    return load_config(use_calibrated=False)


# ----------------------------------------------------------------- calendar
def test_key_dates_2026_27():
    k = key_dates(2027)
    assert k["thanksgiving"] == dt.date(2026, 11, 26)
    assert k["mlk"] == dt.date(2027, 1, 18)
    assert k["presidents"] == dt.date(2027, 2, 15)
    assert k["easter"] == dt.date(2027, 3, 28)
    assert season_of(dt.date(2026, 11, 20)) == 2027


def test_holiday_flags():
    assert day_flags(dt.date(2026, 12, 28))["peak_holiday_week"]
    assert day_flags(dt.date(2027, 2, 13))["presidents_wknd"]
    assert not day_flags(dt.date(2027, 2, 2))["holiday_period"]


# ------------------------------------------------------------------ weather
def test_hourly_to_daily_detects_storm_and_rejects_stuck_sensor():
    t = pd.date_range("2025-01-01", "2025-01-06 23:00", freq="h")
    depth = np.full(len(t), 50.0)
    swe = np.full(len(t), 15.0)
    precip = np.full(len(t), 20.0)
    storm = (t >= "2025-01-03 18:00") & (t < "2025-01-04 06:00")
    k = storm.cumsum()
    depth = depth + np.where(t >= "2025-01-03 18:00", np.minimum(k, 12), 0) * 1.0      # +12"
    precip = precip + np.where(t >= "2025-01-03 18:00", np.minimum(k, 12), 0) * 0.08   # +0.96" water
    swe = swe + np.where(t >= "2025-01-03 18:00", np.minimum(k, 12), 0) * 0.08
    depth[(t >= "2025-01-05")] = 285.0  # sensor sticks at an error value
    h = pd.DataFrame({"time": t, "swe": swe, "depth": depth, "precip": precip, "temp": 20.0})
    d = hourly_to_daily(h)
    assert d.loc["2025-01-04", "new_24"] >= 10
    assert d.loc["2025-01-02", "new_24"] == 0
    assert d.loc["2025-01-06", "base"] < 100          # stuck value rejected


# -------------------------------------------------------------------- model
def test_saturday_busier_than_tuesday_and_closed_is_nan(cfg):
    dates = pd.date_range("2025-01-20", "2025-02-02")  # after MLK, no holidays
    w = synthetic_weather(cfg, dates)
    res = run_model(cfg, build_inputs(cfg, dates, w))
    ik = res[res["resort"].isin(ikon_resorts(cfg))]
    tue = ik[ik["date"] == "2025-01-28"]["crowd_index"].mean()
    sat = ik[ik["date"] == "2025-02-01"]["crowd_index"].mean()
    assert sat > tue * 1.3
    early = pd.date_range("2024-11-01", "2024-11-05")
    r2 = run_model(cfg, build_inputs(cfg, early, synthetic_weather(cfg, early)))
    assert r2["crowd_index"].isna().all()


def test_powder_pulls_people_to_little_cottonwood(cfg):
    dates = pd.date_range("2025-02-03", "2025-02-07")
    calm = run_model(cfg, build_inputs(cfg, dates, synthetic_weather(cfg, dates)))
    pow_ = run_model(cfg, build_inputs(cfg, dates, synthetic_weather(cfg, dates, snow_days={dates[2]})))
    get = lambda r, x: r[(r["date"] == dates[2]) & (r["resort"] == x)]["visitors"].item()
    assert get(pow_, "alta") > get(calm, "alta")
    assert pow_[pow_["date"] == dates[2]]["visitors"].sum() > calm[calm["date"] == dates[2]]["visitors"].sum()


def test_rankings_cover_open_ikon(cfg):
    dates = pd.date_range("2025-02-03", "2025-02-04")
    res = add_rankings(run_model(cfg, build_inputs(cfg, dates, synthetic_weather(cfg, dates))), ikon_resorts(cfg))
    day = res[(res["date"] == dates[0]) & res["rank"].notna()]
    assert sorted(day["rank"]) == list(range(1, len(ikon_resorts(cfg)) + 1))


def test_equilibrium_converged(cfg):
    dates = season_dates(2025, "12-15", "03-15")
    w = synthetic_weather(cfg, dates, snow_days=set(dates[::9]))
    inp = build_inputs(cfg, dates, w)
    a = run_model(cfg, inp)["crowd_index"]
    b = run_model(cfg, inp, iters=600)["crowd_index"]
    assert (a - b).abs().max() < 8


# --------------------------------------------------------------- calibrate
def test_affine_nll_is_scale_invariant():
    obs = pd.DataFrame({"kind": ["crowd_rating"] * 5, "value": [2, 4, 6, 8, 9], "weight": 1.0})
    x = np.array([0.2, 0.4, 0.6, 0.8, 0.95])
    n1, _ = data_nll(obs, x)
    n2, _ = data_nll(obs.assign(value=obs["value"] * 10 + 3), x)
    assert abs((n2 - n1) - 5 * np.log(10)) < 1e-6  # only the units change


def test_fit_moves_toward_truth(cfg):
    dates = season_dates(2025, "12-20", "03-15")
    w = synthetic_weather(cfg, dates, snow_days=set(dates[::6]))
    truth = apply_overrides(cfg, {"resorts.snowbasin.capacity_scale": 0.6})
    tr = run_model(truth, build_inputs(truth, dates, w))
    tr = tr[tr["resort"].isin(ikon_resorts(cfg)) & tr["open"]].sample(250, random_state=1)
    obs = pd.DataFrame({"date": tr["date"].to_numpy(), "resort": tr["resort"].to_numpy(),
                        "kind": "crowd_rating", "value": 1 + 6 * tr["load"].to_numpy(), "weight": 1.0})
    spec = [ParamSpec("resorts.snowbasin.capacity_scale", "log", 0.5)]
    fitted, _, _ = fit(obs, w, cfg=cfg, specs=spec, verbose=False)
    assert fitted["resorts.snowbasin.capacity_scale"] < 0.8


# ------------------------------------------------------------- udot / collect
def test_udot_up_direction_and_observations():
    from skicrowd import udot
    dates = pd.date_range("2024-01-06", "2024-02-25")
    rows = []
    for d in dates:
        for direction, am, pm in (("PDir", 400, 60), ("NDir", 50, 380)):
            r = {"station": 317, "date": d, "direction": direction, "holiday": None}
            for h in range(24):
                r[f"Z{h:02d}00"] = am if 7 <= h <= 10 else pm if 15 <= h <= 17 else 20
            rows.append(r)
    h = pd.DataFrame(rows)
    assert udot.up_direction(h) == {317: "PDir"}
    d = udot.ski_daily(h, out_path=None)
    obs = udot.to_observations(d)
    assert (obs["kind"] == "canyon_vehicles").all() and (obs["resort"] == "LCC").all()
    assert obs["value"].iloc[0] == 4 * 400 + 5 * 20


def test_collect_parsing_roi_and_summary(tmp_path):
    from skicrowd import collect
    page = ('<h2 class="cams-section-title"><a><span>Snowbird</span></a></h2>'
            '<img src="https://www.skiutah.com/files/blob/webcam/894457031?_ts=20270109170000" class="x" alt="Tram Maze"/>')
    found = collect.resolve_skiutah(page)
    hit = collect.match_cam({"section": "Snowbird", "title": "Tram Maze"}, found)
    assert hit["url"].endswith("/894457031") and hit["ts"] == "20270109170000"
    assert collect.in_roi(0.5, 0.5, [[0, 0], [1, 0], [1, 1], [0, 1]])
    assert not collect.in_roi(0.5, 0.5, [[0, 0], [0.4, 0], [0.4, 0.4], [0, 0.4]])
    times = pd.date_range("2027-01-09 16:30", periods=6, freq="20min", tz="UTC")  # 9:30-11:10 MST
    pd.DataFrame({"image_time": times.astype(str), "image_ts": range(6), "camera": "snowbird:Tram Maze",
                  "resort": "snowbird", "kind": "lift_line", "usable": 1, "people": [10, 20, 30, 40, 50, 60],
                  "vehicles": 0}).to_csv(tmp_path / "webcam_counts.csv", index=False)
    obs = collect.summarize(tmp_path)
    assert len(obs) == 1 and obs["value"].iloc[0] == 35 and obs["kind"].iloc[0] == "webcam_count"
