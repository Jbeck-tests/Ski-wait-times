"""Actual resort operating status for the current season.

Source: Ski Utah's statewide snow report, which lists every resort as open
(with snow totals), "Opening <date>", "Opening Day TBA", or closed. The model
treats this as ground truth for the current season:

  open now          -> open from today on (until its usual closing date)
  Opening <date>    -> closed before that date, open from it
  Opening Day TBA   -> closed through today; after that, snow depth decides
  Closed / season   -> closed from today on

Saved to data/season_status.json; hand-edit it if you know better (e.g. a
resort announced a date Ski Utah hasn't picked up yet).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from .calendar_features import season_of

ROOT = Path(__file__).resolve().parent.parent
STATUS_PATH = ROOT / "data" / "season_status.json"
SNOWREPORT_URL = "https://www.skiutah.com/snowreport"
MT = ZoneInfo("America/Denver")

# Ski Utah member slug -> model resort key
SLUGS = {
    "alta": "alta", "snowbird": "snowbird", "brighton": "brighton", "solitude": "solitude",
    "deer-valley": "deer_valley", "snowbasin": "snowbasin", "park-city-mountain": "park_city",
    "powder-mountain": "powder_mountain",
}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                                      "sep", "oct", "nov", "dec"], start=1)}


def _parse_date(text: str, as_of: dt.date) -> dt.date | None:
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})", text)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    month, day = MONTHS[m.group(1).lower()], int(m.group(2))
    year = as_of.year
    d = dt.date(year, month, day)
    if d < as_of - dt.timedelta(days=120):  # "Opening Jan 3" seen in November
        d = dt.date(year + 1, month, day)
    return d


def parse_snowreport(page_html: str, as_of: dt.date) -> dict[str, dict]:
    s = html.unescape(page_html)
    out = {}
    for li in re.findall(r'<li class="row">(.*?)</li>', s, flags=re.S):
        m = re.search(r"/members/([a-z0-9-]+)/snowreport", li)
        if not m or m.group(1) not in SLUGS:
            continue
        key = SLUGS[m.group(1)]
        ov = re.search(r'class="[^"]*override[^"]*">\s*(.*?)\s*</div>', li, flags=re.S)
        if ov:
            raw = re.sub(r"\s+", " ", ov.group(1)).strip()
            low = raw.lower()
            if "closed" in low:
                out[key] = {"status": "closed", "date": None, "raw": raw}
            elif "tba" in low or "tbd" in low:
                out[key] = {"status": "tba", "date": None, "raw": raw}
            elif "opening" in low:
                d = _parse_date(raw, as_of)
                out[key] = {"status": "opening" if d else "tba", "date": d.isoformat() if d else None, "raw": raw}
            elif "closing" in low:
                d = _parse_date(raw, as_of)
                out[key] = {"status": "open", "date": None, "closing": d.isoformat() if d else None, "raw": raw}
            else:
                out[key] = {"status": "unknown", "date": None, "raw": raw}
        else:
            vals = re.findall(r'class="[^"]*value[^"]*">\s*([^<]*)</div>', li)
            out[key] = {"status": "open", "date": None, "raw": " / ".join(v.strip() for v in vals)}
    return out


def fetch_status(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(MT)
    req = urllib.request.Request(SNOWREPORT_URL, headers={"User-Agent": "skicrowd/0.1"})
    with urllib.request.urlopen(req, timeout=40) as r:
        page = r.read().decode("utf-8", "ignore")
    as_of = now.astimezone(MT).date()
    resorts = parse_snowreport(page, as_of)
    if not resorts:
        raise RuntimeError("could not parse any resort status from the Ski Utah snow report")
    return {"source": SNOWREPORT_URL, "fetched_at": now.isoformat(timespec="minutes"),
            "as_of": as_of.isoformat(), "season": season_of(as_of), "resorts": resorts}


def save_status(st: dict, path: Path = STATUS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st, indent=2) + "\n")


def load_status(path: Path = STATUS_PATH) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def ensure_fresh(max_age_hours: float = 12, verbose: bool = True) -> dict | None:
    """Refresh the saved status if it's old; keep the old one if offline."""
    st = load_status()
    if st:
        age = dt.datetime.now(MT) - dt.datetime.fromisoformat(st["fetched_at"])
        if age < dt.timedelta(hours=max_age_hours):
            return st
    try:
        st = fetch_status()
        save_status(st)
        if verbose:
            print(f"(resort status refreshed from Ski Utah, as of {st['as_of']})")
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"(couldn't refresh resort status: {e}; using {'saved status' if st else 'snow-based guesses'})")
    return st


def describe(st: dict, names: dict[str, str]) -> list[str]:
    lines = []
    for r, v in st["resorts"].items():
        if r not in names:
            continue
        if v["status"] == "open":
            txt = "open"
        elif v["status"] == "opening":
            txt = f"opens {dt.date.fromisoformat(v['date']):%a %b %d}"
        elif v["status"] == "tba":
            txt = "opening day TBA"
        else:
            txt = v["raw"] or v["status"]
        lines.append(f"{names[r]:<13} {txt}")
    return lines
