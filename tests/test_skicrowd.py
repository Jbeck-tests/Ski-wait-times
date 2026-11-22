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


# ----------------------------------------------------------------- status
SNOWREPORT_SNIPPET = """
<li class="row"><div class="col title"><a href="https://www.skiutah.com/members/solitude/snowreport">Solitude</a></div>
  <div class="col-2 value bold ">8"</div><div class="col-2 value">29"</div></li>
<li class="row"><div class="col title"><a href="https://www.skiutah.com/members/snowbird/snowreport">Snowbird</a></div>
  <div class="col-4 col-md-6 col-lg-4 override">Opening Nov 27</div></li>
<li class="row"><div class="col title"><a href="https://www.skiutah.com/members/park-city-mountain/snowreport">Park City</a></div>
  <div class="col-4 override">Opening Day TBA</div></li>
<li class="row"><div class="col title"><a href="https://www.skiutah.com/members/deer-valley/snowreport">Deer Valley</a></div>
  <div class="col-4 override">Opening Jan 3</div></li>
"""


def test_parse_snowreport():
    from skicrowd.status import parse_snowreport
    st = parse_snowreport(SNOWREPORT_SNIPPET, dt.date(2026, 11, 20))
    assert st["solitude"]["status"] == "open"
    assert st["snowbird"] == {"status": "opening", "date": "2026-11-27", "raw": "Opening Nov 27"}
    assert st["park_city"]["status"] == "tba"
    assert st["deer_valley"]["date"] == "2027-01-03"  # rolls into next year


def test_operating_status_overrides_snow_guess(cfg):
    from skicrowd.status import parse_snowreport
    dates = pd.date_range("2026-11-15", "2026-12-10")
    w = synthetic_weather(cfg, dates, base=90.0)   # deep snow: snow model alone would open everything
    st = {"season": 2027, "as_of": "2026-11-20",
          "resorts": parse_snowreport(SNOWREPORT_SNIPPET, dt.date(2026, 11, 20))}
    c = dict(cfg, operating=st)
    res = run_model(c, build_inputs(c, dates, w)).set_index(["date", "resort"])["open"]
    assert res[(pd.Timestamp("2026-11-21"), "solitude")]
    assert not res[(pd.Timestamp("2026-11-26"), "snowbird")]
    assert res[(pd.Timestamp("2026-11-27"), "snowbird")]
    assert not res[(pd.Timestamp("2026-12-10"), "deer_valley")]   # opens Jan 3
    assert not res[(pd.Timestamp("2026-11-20"), "park_city")]     # TBA -> closed today


# ---------------------------------------------------------------- intraday
def _synthetic_canyon_hourly():
    """Cars go up 7-10am and come down 2-5pm."""
    rows = []
    for d in pd.date_range("2025-01-06", "2025-02-28"):
        weekend = d.weekday() >= 5
        for direction, active in (("PDir", range(7, 11)), ("NDir", range(14, 18))):
            r = {"station": 317, "date": d, "direction": direction, "holiday": None}
            for h in range(24):
                r[f"Z{h:02d}00"] = (500 if weekend else 250) if h in active else 10
            rows.append(r)
    return pd.DataFrame(rows)


def test_intraday_profiles_and_hourly(cfg, tmp_path, monkeypatch):
    from skicrowd import intraday
    monkeypatch.setattr(intraday, "PROFILE_PATH", tmp_path / "prof.csv")
    monkeypatch.setattr(intraday, "WEBCAM_FIT_PATH", tmp_path / "nofit.json")
    w = synthetic_weather(cfg, pd.date_range("2025-01-01", "2025-03-01"))
    prof = intraday.build_profiles(_synthetic_canyon_hourly(), w)
    lcc = prof[(prof.region == "LCC") & (prof.daytype == "weekday") & (prof.snow == "normal")].set_index("hour")
    assert lcc.loc[11, "occupancy"] > 0.9 and lcc.loc[19, "occupancy"] < 0.1     # all up by 11, gone by 7pm
    assert lcc.loc[8, "arrivals"] > lcc.loc[15, "arrivals"]
    res = pd.DataFrame({"date": pd.to_datetime(["2025-01-15", "2025-01-15"]), "resort": ["alta", "brighton"],
                        "crowd_index": [100.0, 100.0], "new_24": [0.0, 0.0]})
    hr = intraday.hourly(res, cfg, prof)
    alta = hr[hr.resort == "alta"].set_index("hour")["crowd_index"]
    assert abs(alta.max() - 100) < 1e-6 and alta.loc[15] < alta.loc[10]       # afternoon quieter than peak
    assert hr[(hr.resort == "alta")]["hour"].max() < 16                       # no night skiing at Alta
    assert hr[(hr.resort == "brighton")]["hour"].max() >= 16                  # Brighton night skiing in Jan
    bw = intraday.best_windows(hr)
    assert set(bw["resort"]) == {"alta", "brighton"}


def test_brighton_night_skiing_season(cfg):
    from skicrowd.intraday import _night_open
    b = cfg["resorts"]["brighton"]
    assert not _night_open(b, pd.Timestamp("2026-11-28"))   # before mid-December
    assert _night_open(b, pd.Timestamp("2027-01-13"))       # Wednesday in January
    assert not _night_open(b, pd.Timestamp("2027-01-17"))   # Sunday


def test_collector_flags_frozen_frames(tmp_path, monkeypatch):
    import io
    from PIL import Image
    from skicrowd import collect
    buf = io.BytesIO()
    Image.effect_noise((320, 180), 60).convert("RGB").save(buf, "JPEG")
    raw = buf.getvalue()
    page = ('<h2 class="cams-section-title"><a><span>Snowbird</span></a></h2>'
            '<img src="https://www.skiutah.com/files/blob/webcam/1?_ts={ts}" class="x" alt="Tram Maze"/>')
    state = {"ts": "20270109170000"}
    monkeypatch.setattr(collect, "load_cams", lambda: [{"resort": "snowbird", "section": "Snowbird",
                                                        "title": "Tram Maze", "kind": "lift_line"}])
    monkeypatch.setattr(collect, "_get", lambda url, timeout=30: page.format(**state).encode() if "webcams" in url else raw)
    monkeypatch.setattr(collect.udot, "fetch_live", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr("skicrowd.status.fetch_status", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(collect.lift_status, "fetch", lambda **kw: [])   # tests never hit live sites

    class Stub:
        def count(self, img, roi=None):
            return {"people": 3, "vehicles": 0}
    collect.run(tmp_path, detector=Stub(), verbose=False)
    state["ts"] = "20270109172000"               # new timestamp, identical picture
    collect.run(tmp_path, detector=Stub(), verbose=False)
    rows = pd.read_csv(tmp_path / "webcam_counts.csv")
    assert list(rows["usable"]) == [1, 0] and list(rows["frozen"]) == [0, 1]


# ------------------------------------------------------- collector schedule
def test_collector_chain_timing():
    from zoneinfo import ZoneInfo
    from skicrowd.schedule import MAX_WAIT_S, in_window, next_collection, wait_seconds
    mt = ZoneInfo("America/Denver")
    at = lambda *a: dt.datetime(*a, tzinfo=mt)  # noqa: E731
    assert in_window(at(2026, 11, 21, 9, 30)) and not in_window(at(2026, 11, 21, 17, 0))
    assert not in_window(at(2026, 7, 4, 10, 0))
    # mid-day: next 20-minute slot
    assert next_collection(at(2026, 11, 21, 9, 33)) == at(2026, 11, 21, 9, 40)
    assert wait_seconds(at(2026, 11, 21, 9, 33)) == 7 * 60
    # end of day hands off toward 7am, never longer than one job may run
    assert next_collection(at(2026, 11, 21, 16, 50)) == at(2026, 11, 22, 7, 0)
    assert wait_seconds(at(2026, 11, 21, 16, 50)) == MAX_WAIT_S
    assert next_collection(at(2026, 11, 22, 3, 0)) == at(2026, 11, 22, 7, 0)
    # season over after April 30
    assert next_collection(at(2027, 4, 30, 16, 55)) is None
    assert wait_seconds(at(2027, 5, 2, 10, 0)) is None


# --------------------------------------------------------------- lift status
FIX = __import__("pathlib").Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("resort,n_lifts,expect_open", [
    ("solitude", 10, {"Link", "Moonbeam Express", "Magic Carpet"}),   # Nov 22 2026: 3 of 10 running
    ("deer_valley", 33, set()),                                       # pre-season
    ("alta", 5, set()),
    ("snowbasin", 13, set()),
])
def test_lift_pages_parse(resort, n_lifts, expect_open):
    from skicrowd.lifts import parse
    lifts = parse(resort, (FIX / f"lifts_{resort}.txt").read_text())
    assert len(lifts) == n_lifts
    assert {x.lift for x in lifts if x.status == "open"} == expect_open
    assert all(x.status in {"open", "closed", "hold", "scheduled"} for x in lifts)


def test_lift_matching_and_closed_lift_counts_dropped(tmp_path):
    from skicrowd import collect, lifts
    rows = [{"resort": "solitude", "lift": "Moonbeam Express", "status": "open"},
            {"resort": "solitude", "lift": "Apex Express", "status": "closed"},
            {"resort": "snowbird", "lift": "Aerial Tram", "status": "open"}]
    assert lifts.is_open(rows, "solitude", "Moonbeam Express") == 1
    assert lifts.is_open(rows, "solitude", "Apex Express") == 0
    assert lifts.is_open(rows, "snowbird", "Tram") == 1          # whole-word match
    assert lifts.is_open(rows, "solitude", "Summit Express") == ""
    times = pd.date_range("2027-01-09 16:30", periods=4, freq="20min", tz="UTC")
    pd.DataFrame({"image_time": list(times.astype(str)) * 2, "image_ts": range(8),
                  "camera": ["solitude:Moonbeam Lift Line"] * 4 + ["solitude:Apex Express Lift"] * 4,
                  "resort": "solitude", "kind": "lift_line", "usable": 1, "people": [40] * 4 + [8] * 4,
                  "vehicles": 0, "lift_open": [1] * 4 + [0] * 4}).to_csv(tmp_path / "webcam_counts.csv", index=False)
    obs = collect.summarize(tmp_path)
    assert list(obs["source"]) == ["cam_solitude_moonbeam_lift_line"]   # Apex (closed) dropped
