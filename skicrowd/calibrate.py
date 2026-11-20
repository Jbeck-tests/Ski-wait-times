"""Training: fit model parameters to real observations.

No Utah resort publishes daily attendance, so the model starts from
public-information priors (config/model.toml). This module turns any crowd
evidence you can collect into a proper fit:

  kind               resort column        value                       link to model
  -----------------  -------------------  --------------------------  ---------------------
  crowd_rating       resort key           1-10 how busy it felt       affine in load
  wait_min           resort key           typical lift wait, minutes  affine in load
  parking_full_hour  resort key           hour lot/reservations sold  affine in load (b<0)
                                          out, e.g. 8.75 = 8:45am
  canyon_vehicles    LCC / BCC            UDOT up-canyon vehicle      affine in canyon visitors
                                          count for the day
  visitors           resort key           actual skier count          identity (log scale)

"Affine" observation types get their own intercept & slope (solved in closed
form at every step), so subjective scales and units don't matter - only
whether the model orders the days/resorts correctly. Parameters are fitted
by maximum a-posteriori estimation: Gaussian likelihood with unknown
variance per observation kind + Gaussian priors centred on the defaults
(so sparse data nudges, rather than wrecks, the model).
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .config import CALIBRATED_PATH, apply_overrides, get_path, ikon_resorts, load_config
from .calendar_features import season_dates, season_of
from .model import build_inputs, run_model

ROOT = Path(__file__).resolve().parent.parent
OBS_PATH = ROOT / "data" / "observations" / "observations.csv"
AFFINE_KINDS = {"crowd_rating", "wait_min", "parking_full_hour", "canyon_vehicles"}
ALL_KINDS = AFFINE_KINDS | {"visitors"}


@dataclass
class ParamSpec:
    path: str
    transform: str  # "log" or "lin"
    prior_sd: float


def default_param_specs(cfg: dict) -> list[ParamSpec]:
    specs = [
        ParamSpec("segments.slc_locals.base", "log", 0.4),
        ParamSpec("segments.ogden_locals.base", "log", 0.4),
        ParamSpec("segments.pc_locals.base", "log", 0.4),
        ParamSpec("segments.dest_parkcity.base", "log", 0.4),
        ParamSpec("segments.dest_slc.base", "log", 0.4),
        ParamSpec("segments.slc_locals.saturday", "log", 0.25),
        ParamSpec("segments.slc_locals.sunday", "log", 0.25),
        ParamSpec("segments.slc_locals.powder_participation", "log", 0.5),
        ParamSpec("segments.slc_locals.beta_powder", "log", 0.5),
        ParamSpec("behaviour.beta_crowd", "log", 0.4),
    ]
    for r in ikon_resorts(cfg):
        specs.append(ParamSpec(f"resorts.{r}.capacity_scale", "log", 0.3))
        specs.append(ParamSpec(f"segments.slc_locals.affinity.{r}", "lin", 0.5))
    return specs


def _get_default(cfg, path):
    try:
        return float(get_path(cfg, path))
    except KeyError:
        return 1.0 if path.endswith("capacity_scale") else 0.0


def _to_z(spec: ParamSpec, v: float) -> float:
    return math.log(v) if spec.transform == "log" else v


def _from_z(spec: ParamSpec, z: float) -> float:
    return math.exp(z) if spec.transform == "log" else z


# ------------------------------------------------------------- observations
def load_observations(path: Path | str = OBS_PATH) -> pd.DataFrame:
    obs = pd.read_csv(path, parse_dates=["date"], comment="#")
    obs = obs[obs["kind"].isin(ALL_KINDS)].dropna(subset=["value"])
    if "weight" not in obs:
        obs["weight"] = 1.0
    obs["weight"] = obs["weight"].fillna(1.0)
    return obs.reset_index(drop=True)


def model_values(res: pd.DataFrame, obs: pd.DataFrame, cfg: dict) -> np.ndarray:
    """The model quantity each observation is compared against."""
    canyon_of = {r: c["canyon"] for r, c in cfg["resorts"].items()}
    per_resort = res[["date", "resort", "load", "visitors"]]
    canyon = (res.assign(resort=res["resort"].map(canyon_of))
              .groupby(["date", "resort"], as_index=False)["visitors"].sum())
    canyon["load"] = np.nan
    table = pd.concat([per_resort, canyon], ignore_index=True)
    m = obs[["date", "resort", "kind"]].merge(table, on=["date", "resort"], how="left")
    return np.where(m["kind"].isin(["visitors", "canyon_vehicles"]), m["visitors"], m["load"]).astype(float)


def data_nll(obs: pd.DataFrame, x: np.ndarray) -> tuple[float, dict]:
    nll, info = 0.0, {}
    for kind, g in obs.groupby("kind"):
        xi = x[g.index]
        ok = np.isfinite(xi)
        if ok.sum() < 3:
            continue
        y, w, xi = g["value"].to_numpy()[ok], g["weight"].to_numpy()[ok], xi[ok]
        if kind == "visitors":
            resid = np.log(np.maximum(y, 1)) - np.log(np.maximum(xi, 1))
        else:
            X = np.column_stack([np.ones_like(xi), xi])
            W = np.sqrt(w)
            coef, *_ = np.linalg.lstsq(X * W[:, None], y * W, rcond=None)
            resid = y - X @ coef
            info[kind] = {"intercept": float(coef[0]), "slope": float(coef[1])}
        sse = float((w * resid ** 2).sum()) + 1e-9
        n = float(w.sum())
        nll += 0.5 * n * math.log(sse / n)
        info.setdefault(kind, {})["rmse"] = math.sqrt(sse / n)
        info[kind]["n"] = int(ok.sum())
    return nll, info


# --------------------------------------------------------------------- fit
def fit(obs: pd.DataFrame, weather: pd.DataFrame, cfg: dict | None = None,
        specs: list[ParamSpec] | None = None, maxiter: int = 4000, verbose: bool = True):
    cfg = copy.deepcopy(cfg or load_config(use_calibrated=False))
    specs = specs or default_param_specs(cfg)
    # simulate each observed season from its start (so open status and snow
    # base evolve properly) up to its last observation - but skip summers
    obs_season = obs["date"].map(lambda d: season_of(d.date()))
    ranges = []
    for s in sorted(obs_season.unique()):
        sd_ = season_dates(s)
        ranges.append(sd_[sd_ <= obs["date"][obs_season == s].max()])
    full = ranges[0].append(ranges[1:]) if len(ranges) > 1 else ranges[0]
    inp = build_inputs(cfg, full, weather)
    z0 = np.array([_to_z(s, _get_default(cfg, s.path)) for s in specs])
    sd = np.array([s.prior_sd for s in specs])

    def cfg_for(z):
        return apply_overrides(cfg, {s.path: _from_z(s, zi) for s, zi in zip(specs, z)})

    def objective(z):
        c = cfg_for(z)
        res = run_model(c, inp, iters=80)
        nll, _ = data_nll(obs, model_values(res, obs, c))
        return nll + 0.5 * float((((z - z0) / sd) ** 2).sum())

    f0 = objective(z0)
    if verbose:
        print(f"  start objective {f0:.2f}  ({len(obs)} observations, {len(specs)} parameters)")
    r = minimize(objective, z0, method="L-BFGS-B", options={"maxiter": maxiter, "eps": 1e-3})
    fitted = {s.path: round(_from_z(s, zi), 4) for s, zi in zip(specs, r.x)}
    c = cfg_for(r.x)
    res = run_model(c, inp)
    _, info = data_nll(obs, model_values(res, obs, c))
    if verbose:
        print(f"  final objective {r.fun:.2f}")
    return fitted, info, r


def save_calibration(fitted: dict, info: dict, path: Path = CALIBRATED_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Written by `python -m skicrowd calibrate`. Delete to revert to priors.", "[params]"]
    lines += [f'"{k}" = {v}' for k, v in fitted.items()]
    lines += ["", "[fit]"]
    for kind, d in info.items():
        lines.append(f"{kind} = {{ " + ", ".join(f"{k} = {round(v, 4)}" for k, v in d.items()) + " }")
    path.write_text("\n".join(lines) + "\n")


# -------------------------------------------------------- synthetic check
def synthetic_recovery(weather: pd.DataFrame, n_obs: int = 400, seed: int = 7,
                       seasons=(2023, 2024, 2025), verbose: bool = True) -> dict:
    """Proves the training loop works: invent a 'true' world that differs from
    the priors, simulate noisy crowd ratings from it, fit, and check that
    held-out predictions get closer to the truth."""
    rng = np.random.default_rng(seed)
    prior = load_config(use_calibrated=False)
    truth = apply_overrides(prior, {
        "segments.slc_locals.affinity.solitude": 1.2,     # Solitude more popular than assumed
        "resorts.snowbasin.capacity_scale": 0.7,         # Snowbasin less roomy than assumed
        "segments.slc_locals.saturday": 3.8,             # Saturdays even worse
        "segments.slc_locals.powder_participation": 0.09,
    })
    ikon = ikon_resorts(prior)
    frames = []
    for s in seasons:
        d = season_dates(s, "11-15", "04-15")
        frames.append(run_model(truth, build_inputs(truth, d, weather)))
    true_res = pd.concat(frames)
    true_res = true_res[true_res["resort"].isin(ikon) & true_res["open"]]
    dates = np.array(true_res["date"].unique())
    rng.shuffle(dates)
    train_dates, test_dates = set(dates[: len(dates) // 2]), set(dates[len(dates) // 2:])
    train_pool = true_res[true_res["date"].isin(train_dates)]
    sample = train_pool.sample(n=min(n_obs, len(train_pool)), random_state=seed)
    obs = pd.DataFrame({
        "date": sample["date"].to_numpy(), "resort": sample["resort"].to_numpy(), "kind": "crowd_rating",
        "value": np.clip(np.round(1 + 6 * sample["load"].to_numpy() + rng.normal(0, 0.8, len(sample))), 1, 10),
        "weight": 1.0,
    }).reset_index(drop=True)

    fitted, info, _ = fit(obs, weather, cfg=prior, verbose=verbose)
    post = apply_overrides(prior, fitted)

    def score(cfg):
        fr = []
        for s in seasons:
            d = season_dates(s, "11-15", "04-15")
            fr.append(run_model(cfg, build_inputs(cfg, d, weather)))
        r = pd.concat(fr)
        r = r[r["resort"].isin(ikon) & r["open"] & r["date"].isin(test_dates)]
        m = r.merge(true_res[["date", "resort", "load"]], on=["date", "resort"], suffixes=("", "_true"))
        top_pred = m.loc[m.groupby("date")["load"].idxmin(), ["date", "resort"]]
        top_true = m.loc[m.groupby("date")["load_true"].idxmin(), ["date", "resort"]]
        hit = (top_pred.set_index("date")["resort"] == top_true.set_index("date")["resort"]).mean()
        multi = m[m.groupby("date")["resort"].transform("count") >= 3]
        sp = multi.groupby("date").apply(lambda g: g["load"].rank().corr(g["load_true"].rank()), include_groups=False).mean()
        return {"least_busy_hit_rate": round(float(hit), 3), "mean_rank_corr": round(float(sp), 3)}

    out = {"prior": score(prior), "fitted": score(post),
           "params": {k: (round(_get_default(prior, k), 3), fitted[k], round(_get_default(truth, k), 3))
                      for k in ["segments.slc_locals.affinity.solitude", "resorts.snowbasin.capacity_scale",
                                "segments.slc_locals.saturday", "segments.slc_locals.powder_participation"]}}
    return out
