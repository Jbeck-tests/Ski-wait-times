"""Command line interface:  python -m skicrowd <command> --help"""
from __future__ import annotations

import argparse
import math
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
    from .status import ensure_fresh
    from .weather import load_daily_history
    ensure_fresh()
    cfg = load_config()
    w = load_daily_history()
    target = args.season or season_of(dt.date.today())
    analogs = _seasons(cfg, args.first, args.last)
    print(f"Running {target - 1}-{str(target)[2:]} calendar against {len(analogs)} past winters ...")
    clim, hr = climatology(cfg, w, target, analogs, with_hourly=True)
    REPORTS.mkdir(exist_ok=True)
    csv = REPORTS / f"crowd_calendar_{target - 1}-{str(target)[2:]}.csv"
    clim.to_csv(csv, index=False)
    html = write_calendar_html(clim, cfg, target, hourly=hr, path=REPORTS / f"crowd_calendar_{target - 1}-{str(target)[2:]}.html")
    print(f"Wrote {csv.relative_to(ROOT)} and {html.relative_to(ROOT)}")


def cmd_predict(args):
    from .analysis import predict_day
    from .status import ensure_fresh
    from .weather import fetch_forecast, load_daily_history
    ensure_fresh()
    cfg = load_config()
    w = load_daily_history()
    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today() + dt.timedelta(days=1)
    fc = None
    if not args.no_forecast and 0 <= (date - dt.date.today()).days <= 15:
        try:
            fc = fetch_forecast(list(cfg["resorts"]))
        except Exception as e:  # noqa: BLE001
            print(f"(forecast unavailable: {e}; using climatology)")
    day, mode, hr = predict_day(cfg, w, date, forecast=fc)
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
    _print_hourly(hr, day, names)
    print("\nCrowd index: 100 = at comfortable capacity. Ranking matters more than the absolute number.")


def _print_hourly(hr, day, names):
    from . import intraday
    if hr is None or hr.empty:
        return
    order = [r for r in day.dropna(subset=["crowd_index"])["resort"] if r in set(hr["resort"])]
    t = hr.pivot_table(index="resort", columns="hour", values="crowd_index").reindex(order)
    hours = [h for h in intraday.DAY_HOURS if h in t.columns]
    print("\nBy time of day (crowd index for each hour block; 2p = 2-3pm):")
    print(f"{'':<13}" + "".join(f"{intraday.LABEL[h]:>6}" for h in hours))
    for r, row in t.iterrows():
        print(f"{names[r]:<13}" + "".join(f"{row[h]:>6.0f}" for h in hours))
    bw = intraday.best_windows(hr)
    if bw.empty:
        return
    best = bw.loc[bw["best_crowd"].idxmin()]
    lab = lambda h: intraday.LABEL[h]  # noqa: E731
    print(f"\nQuietest window: {names[best.resort]} {lab(best.best_start)}-{lab(best.best_start + 2)} "
          f"(~{best.best_crowd:.0f}).")
    for b in bw.sort_values("best_crowd").itertuples():
        print(f"  {names[b.resort]:<12} best {lab(b.best_start)}-{lab(b.best_start + 2)} (~{b.best_crowd:.0f}),"
              f" worst around {lab(b.peak_hour)} (~{b.peak_crowd:.0f})")
    if "brighton" in t.index and 17 in t.columns and t.loc["brighton"].notna().get(17, False):
        night = t.loc["brighton", [h for h in intraday.NIGHT_HOURS if h in t.columns]]
        print("  Brighton night skiing: " + ", ".join(f"{intraday.LABEL[h]} ~{v:.0f}" for h, v in night.items()))


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
    obs = load_observations(args.obs or OBS_PATH) if Path(args.obs or OBS_PATH).exists() else None
    if args.collected:
        from .collect import summarize
        from .calibrate import normalise_obs
        c = summarize(args.collected)
        print(f"Including {len(c)} observations from collected webcam / live canyon data")
        obs = normalise_obs(pd.concat([o for o in (obs, c) if o is not None and len(o)], ignore_index=True))
    if args.udot:
        from . import udot
        from .calibrate import evaluate, normalise_obs, traffic_param_specs
        if not udot.DAILY_PATH.exists():
            udot.ski_daily(udot.fetch_hourly())
        u = udot.to_observations()
        obs = normalise_obs(pd.concat([o for o in (obs, u) if o is not None and len(o)], ignore_index=True))
        cfg0 = load_config(use_calibrated=False)
        season = obs["date"].map(lambda d: season_of(d.date()))
        train = obs[season != args.holdout] if args.holdout else obs
        print(f"Training on {len(train):,} observations"
              + (f"; holding out the {args.holdout - 1}-{str(args.holdout)[2:]} season" if args.holdout else ""))
        fitted, info, _ = fit(train.reset_index(drop=True), w, cfg=cfg0, specs=traffic_param_specs(cfg0))
        from .config import apply_overrides
        post = apply_overrides(cfg0, fitted)
        for label, part in [("train", train), ("holdout", obs[season == args.holdout] if args.holdout else None)]:
            if part is None or not len(part):
                continue
            a = evaluate(part, w, cfg0).set_index(["source", "target", "n"])
            b = evaluate(part, w, post).set_index(["source", "target", "n"])
            print(f"\n{label}: rank correlation with observations, priors -> trained")
            print(pd.concat({"priors": a["spearman_r"], "trained": b["spearman_r"],
                             "pearson priors": a["pearson_r"], "pearson trained": b["pearson_r"]}, axis=1).to_string())
        if not args.dry_run:
            save_calibration(fitted, info)
            print("\nSaved data/calibrated_params.toml - all commands now use it.")
        print("\nLargest parameter changes:")
        from .calibrate import _get_default
        ch = sorted(fitted.items(), key=lambda kv: -abs(math.log(max(kv[1], 1e-6) / max(_get_default(cfg0, kv[0]), 1e-6)))
                    if _get_default(cfg0, kv[0]) > 0 and kv[1] > 0 else -abs(kv[1] - _get_default(cfg0, kv[0])))
        for k, v in ch[:12]:
            print(f"  {k:<48} {_get_default(cfg0, k):>9.3f} -> {v:.3f}")
        return
    if obs is None or len(obs) < 10:
        sys.exit(f"Only {0 if obs is None else len(obs)} usable observations in {args.obs or OBS_PATH}; log at least ~10 first.")
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


def cmd_udot(args):
    from . import udot
    if args.action == "fetch":
        print("Downloading UDOT hourly counts ...")
        h = udot.fetch_hourly()
        d = udot.ski_daily(h)
        print(f"{len(h):,} hourly station-direction-days; up-canyon directions: {udot.up_direction(h)}")
        print(d.groupby("station").agg(days=("am_up", "size"), first=("date", "min"), last=("date", "max"),
                                       median_am_up=("am_up", "median")).to_string())
    else:
        print(udot.fetch_live().to_string(index=False))


def cmd_collect(args):
    from . import collect
    if args.action == "run":
        collect.run(args.out)
    else:
        obs = collect.summarize(args.data)
        out = Path(args.data) / "observations_from_collector.csv"
        obs.to_csv(out, index=False)
        print(f"{len(obs)} daily observations -> {out}")
        if len(obs):
            print(obs.groupby("source").size().to_string())


def cmd_status(args):
    from .status import describe, ensure_fresh
    st = ensure_fresh(max_age_hours=0 if args.refresh else 12)
    if not st:
        sys.exit("No resort status available.")
    cfg = load_config()
    print(f"Resort status (Ski Utah snow report, as of {st['as_of']}):")
    print("\n".join("  " + ln for ln in describe(st, {r: c["name"] for r, c in cfg["resorts"].items()})))


def cmd_intraday(args):
    from . import intraday, udot
    from .weather import load_daily_history
    if args.action == "build":
        h = pd.read_csv(udot.HOURLY_PATH, parse_dates=["date"])
        prof = intraday.build_profiles(h, load_daily_history())
        print(f"Wrote {intraday.PROFILE_PATH.relative_to(ROOT)} "
              f"({prof.groupby(['region', 'daytype', 'snow'])['n'].min().to_dict()})")
    else:
        fit = intraday.fit_from_webcams(args.data, load_config())
        if fit is None:
            print("Not enough lift-line webcam data yet; keeping the current time-of-day settings.")


def cmd_lifts(args):
    from . import lifts
    found = lifts.fetch(resorts=tuple(args.resort) if args.resort else tuple(lifts.PAGES))
    cfg = load_config()
    for r in dict.fromkeys(x.resort for x in found):
        rl = [x for x in found if x.resort == r]
        op = [x.lift for x in rl if x.status == "open"]
        print(f"{cfg['resorts'][r]['name']}: {len(op)}/{len(rl)} open" + (f" - {', '.join(op)}" if op else ""))
    missing = [r for r in (args.resort or lifts.PAGES) if r not in {x.resort for x in found}]
    if missing:
        print("no lift list parsed for:", ", ".join(missing))


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
    ca.add_argument("--udot", action="store_true", help="also train on UDOT canyon traffic counts")
    ca.add_argument("--holdout", type=int, help="season (end year) to keep out of training and test on, e.g. 2025")
    ca.add_argument("--collected", help="directory of collector data (a checkout of the collector-data branch)")
    ca.add_argument("--dry-run", action="store_true", help="report the fit without saving it")
    ca.set_defaults(fn=cmd_calibrate)

    it = sub.add_parser("intraday", help="time-of-day curves: rebuild from UDOT counts or refit from webcams")
    it.add_argument("action", choices=["build", "fit"])
    it.add_argument("--data", default="collector-data", help="collector data directory for `fit`")
    it.set_defaults(fn=cmd_intraday)

    lf = sub.add_parser("lifts", help="current lift open/closed status from each resort's page")
    lf.add_argument("resort", nargs="*", help="resort keys (default: all six)")
    lf.set_defaults(fn=cmd_lifts)

    stp = sub.add_parser("status", help="show / refresh which resorts are open and announced opening dates")
    stp.add_argument("--refresh", action="store_true")
    stp.set_defaults(fn=cmd_status)

    ud = sub.add_parser("udot", help="UDOT traffic counts: download history or show live canyon volumes")
    ud.add_argument("action", choices=["fetch", "live"])
    ud.set_defaults(fn=cmd_udot)

    co = sub.add_parser("collect", help="webcam + live canyon collector (run by GitHub Actions)")
    co.add_argument("action", choices=["run", "summarize"])
    co.add_argument("--out", default="collector-data", help="output directory for `run`")
    co.add_argument("--data", default="collector-data", help="collected data directory for `summarize`")
    co.set_defaults(fn=cmd_collect)

    lg = sub.add_parser("log", help="record a crowd observation for training")
    lg.add_argument("date")
    lg.add_argument("resort", help="resort key (alta, snowbird, ...) or LCC/BCC for canyon_vehicles")
    lg.add_argument("kind", choices=["crowd_rating", "wait_min", "parking_full_hour", "canyon_vehicles", "resort_vehicles", "visitors"])
    lg.add_argument("value", type=float)
    lg.add_argument("--weight", type=float, default=1.0)
    lg.add_argument("--notes")
    lg.set_defaults(fn=cmd_log)

    args = p.parse_args(argv)
    pd.set_option("display.width", 140)
    args.fn(args)


if __name__ == "__main__":
    main()
