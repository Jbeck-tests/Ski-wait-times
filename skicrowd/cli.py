"""Command line interface:  python -m skicrowd <command> --help"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

from .calendar_features import season_of
from .config import ikon_resorts, load_config
from .model import crowd_label

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"


def _seasons(cfg, args_first=None, args_last=None):
    a = args_first or cfg["meta"]["history_first_season"]
    b = args_last or cfg["meta"]["history_last_season"]
    return list(range(a, b + 1))


def cmd_fetch(args):
    from .weather import build_daily_history
    cfg = load_config()
    seasons = _seasons(cfg, args.first, args.last)
    cur = season_of(dt.date.today())
    if cur not in seasons and not args.last:
        seasons.append(cur)
    print(f"Downloading SNOTEL history for seasons {seasons[0] - 1}-{seasons[-1]} ...")
    d = build_daily_history(cfg, seasons, refresh=args.refresh)
    print(f"Saved {len(d):,} station-days to data/weather_daily.csv")


def cmd_backtest(args):
    from .analysis import insights, run_history
    from .report import write_backtest_report
    from .weather import load_daily_history
    cfg = load_config(use_calibrated=not args.priors)
    w = load_daily_history()
    seasons = _seasons(cfg, args.first, args.last)
    print(f"Simulating {len(seasons)} seasons ...")
    hist = run_history(cfg, w, seasons)
    REPORTS.mkdir(exist_ok=True)
    hist.to_csv(REPORTS / "history_daily.csv", index=False)
    ins = insights(hist, cfg)
    path = write_backtest_report(ins, cfg, seasons, REPORTS / "backtest_report.md")
    print(ins["overall"].to_string())
    print(f"\nFull report: {path.relative_to(ROOT)}")


def cmd_calendar(args):
    from .analysis import climatology
    from .report import write_calendar_html
    from .weather import load_daily_history
    cfg = load_config()
    w = load_daily_history()
    target = args.season or season_of(dt.date.today())
    analogs = _seasons(cfg, args.first, args.last)
    print(f"Running {target - 1}-{str(target)[2:]} calendar against {len(analogs)} past winters ...")
    clim = climatology(cfg, w, target, analogs)
    REPORTS.mkdir(exist_ok=True)
    csv = REPORTS / f"crowd_calendar_{target - 1}-{str(target)[2:]}.csv"
    clim.to_csv(csv, index=False)
    html = write_calendar_html(clim, cfg, target, REPORTS / f"crowd_calendar_{target - 1}-{str(target)[2:]}.html")
    print(f"Wrote {csv.relative_to(ROOT)} and {html.relative_to(ROOT)}")


def cmd_predict(args):
    from .analysis import predict_day
    from .weather import fetch_forecast, load_daily_history
    cfg = load_config()
    w = load_daily_history()
    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today() + dt.timedelta(days=1)
    fc = None
    if not args.no_forecast and 0 <= (date - dt.date.today()).days <= 15:
        try:
            fc = fetch_forecast(list(cfg["resorts"]))
        except Exception as e:  # noqa: BLE001
            print(f"(forecast unavailable: {e}; using climatology)")
    day, mode = predict_day(cfg, w, date, forecast=fc)
    day = day.assign(_closed=day["crowd_index"].isna()).sort_values(["_closed", "crowd_index"])
    names = {r: c["name"] for r, c in cfg["resorts"].items()}
    print(f"\n{date:%a %d %b %Y}  — based on {mode}\n")
    print(f"{'Resort':<13}{'Crowd':>7}  {'Level':<9}{'P(least busy)':>14}{'P(open)':>9}")
    for row in day.itertuples():
        ci = "" if row.crowd_index != row.crowd_index else f"{row.crowd_index:.0f}"
        print(f"{names[row.resort]:<13}{ci:>7}  {crowd_label(row.crowd_index):<9}"
              f"{row.p_least_busy:>13.0%}{row.p_open:>9.0%}")
    if mode == "climatology":
        print("\n(Crowd = average over past winters in which that resort was open on this date.)")
    if mode == "forecast":
        snow = day.set_index("resort")[["new_24", "road_delay_min"]]
        print("\nNew snow (in) / expected canyon delay (min):")
        for r, s in snow.iterrows():
            print(f"  {names[r]:<13}{s.new_24:>5.1f}  {s.road_delay_min:>5.0f}")
    print("\nCrowd index: 100 = at comfortable capacity. Ranking matters more than the absolute number.")


def cmd_calibrate(args):
    from .calibrate import OBS_PATH, fit, load_observations, save_calibration, synthetic_recovery
    from .weather import load_daily_history
    w = load_daily_history()
    if args.synthetic_demo:
        out = synthetic_recovery(w)
        print("\nHeld-out accuracy vs. the hidden 'true' world:")
        print(f"  priors : {out['prior']}")
        print(f"  fitted : {out['fitted']}")
        print("\nParameter recovery (prior -> fitted | truth):")
        for k, (p, f, t) in out["params"].items():
            print(f"  {k:<48} {p:>7} -> {f:>7} | {t}")
        return
    obs = load_observations(args.obs or OBS_PATH)
    if len(obs) < 10:
        sys.exit(f"Only {len(obs)} usable observations in {args.obs or OBS_PATH}; log at least ~10 first.")
    fitted, info, _ = fit(obs, w)
    save_calibration(fitted, info)
    print("Fit quality:", info)
    print("Saved data/calibrated_params.toml — all commands now use it (pass --priors to backtest to compare).")


def cmd_log(args):
    from .calibrate import OBS_PATH
    OBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    new = not OBS_PATH.exists()
    with open(OBS_PATH, "a") as f:
        if new:
            f.write("date,resort,kind,value,weight,notes\n")
        notes = (args.notes or "").replace(",", ";")
        f.write(f"{args.date},{args.resort},{args.kind},{args.value},{args.weight},{notes}\n")
    print(f"Logged to {OBS_PATH.relative_to(ROOT)}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="skicrowd", description="Utah Ikon resort crowd model")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download SNOTEL snow/temperature history")
    f.add_argument("--first", type=int)
    f.add_argument("--last", type=int)
    f.add_argument("--refresh", action="store_true", help="re-download cached seasons")
    f.set_defaults(fn=cmd_fetch)

    b = sub.add_parser("backtest", help="simulate past seasons and write crowd-avoidance insights")
    b.add_argument("--first", type=int)
    b.add_argument("--last", type=int)
    b.add_argument("--priors", action="store_true", help="ignore calibrated parameters")
    b.set_defaults(fn=cmd_backtest)

    c = sub.add_parser("calendar", help="crowd calendar for a season using every past winter as a scenario")
    c.add_argument("--season", type=int, help="season end year, e.g. 2027 for 2026-27")
    c.add_argument("--first", type=int)
    c.add_argument("--last", type=int)
    c.set_defaults(fn=cmd_calendar)

    pr = sub.add_parser("predict", help="rank resorts for a date (forecast within 16 days)")
    pr.add_argument("date", nargs="?", help="YYYY-MM-DD (default tomorrow)")
    pr.add_argument("--no-forecast", action="store_true")
    pr.set_defaults(fn=cmd_predict)

    ca = sub.add_parser("calibrate", help="fit the model to logged observations")
    ca.add_argument("--obs", help="observations CSV (default data/observations/observations.csv)")
    ca.add_argument("--synthetic-demo", action="store_true", help="prove the fitter recovers a known truth")
    ca.set_defaults(fn=cmd_calibrate)

    lg = sub.add_parser("log", help="record a crowd observation for training")
    lg.add_argument("date")
    lg.add_argument("resort", help="resort key (alta, snowbird, ...) or LCC/BCC for canyon_vehicles")
    lg.add_argument("kind", choices=["crowd_rating", "wait_min", "parking_full_hour", "canyon_vehicles", "visitors"])
    lg.add_argument("value", type=float)
    lg.add_argument("--weight", type=float, default=1.0)
    lg.add_argument("--notes")
    lg.set_defaults(fn=cmd_log)

    args = p.parse_args(argv)
    pd.set_option("display.width", 140)
    args.fn(args)


if __name__ == "__main__":
    main()
