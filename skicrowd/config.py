"""Load / copy / override the model configuration."""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "config" / "model.toml"
CALIBRATED_PATH = Path(__file__).resolve().parent.parent / "data" / "calibrated_params.toml"


def load_config(path: str | Path | None = None, use_calibrated: bool = True, use_status: bool = True) -> dict:
    with open(path or DEFAULT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    if use_calibrated and path is None and CALIBRATED_PATH.exists():
        with open(CALIBRATED_PATH, "rb") as f:
            overrides = tomllib.load(f).get("params", {})
        cfg = apply_overrides(cfg, overrides)
    if use_status:
        from .status import load_status
        st = load_status()
        if st:
            cfg["operating"] = st
    return cfg


def get_path(cfg: dict, path: str):
    node = cfg
    for k in path.split("."):
        node = node[k]
    return node


def set_path(cfg: dict, path: str, value) -> None:
    keys = path.split(".")
    node = cfg
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value


def apply_overrides(cfg: dict, overrides: dict[str, float]) -> dict:
    out = copy.deepcopy(cfg)
    for p, v in overrides.items():
        set_path(out, p, v)
    return out


def ikon_resorts(cfg: dict) -> list[str]:
    return [k for k, r in cfg["resorts"].items() if r.get("ikon")]


def season_value(resort: dict, key: str, season: int):
    by = resort.get(f"{key}_by_season", {})
    eligible = [int(s) for s in by if int(s) <= season]
    return by[str(max(eligible))] if eligible else resort[key]
