"""Live lift open/closed status for the six Ikon resorts.

Each resort's public lift-status page is loaded in a headless browser and
read the way a visitor sees it; the rendered text is parsed into
(lift, status) pairs. No private APIs or tokens are used.

Why it matters:
  * webcam "lift line" counts only mean something when the lift is running
    (a closed lift with skiers walking past looks like a short line);
  * real open-lift counts beat the model's snow-depth guess of terrain open;
  * the log builds a history of when each lift opens each season.

    python -m skicrowd lifts            # print current status
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

PAGES = {
    # resort: (url, section start, section end) -- regexes on whole lines
    "solitude": ("https://www.solitudemountain.com/mountain-and-village/conditions-and-maps", r"^LIFTS$", r"^TRAILS$"),
    "deer_valley": ("https://www.deervalley.com/explore-the-mountain/mountain-report", r"^LIFTS$", r"^TRAILS$"),
    "alta": ("https://www.alta.com/lift-terrain-status", r"^Mountain environments change daily", r"(?i)terrain status dashboard"),
    "snowbasin": ("https://www.snowbasin.com/the-mountain/mountain-report/", r"^Lift Name", r"^Trails$"),
    # Snowbird and Brighton load their lists from data servers this sandbox
    # can't reach, so these markers are best guesses verified from the first
    # collector runs (an unparsed page is saved to debug/ for fixing).
    "snowbird": ("https://www.snowbird.com/the-mountain/mountain-report/lift-trail-report/", r"(?i)^lifts?( status)?$", r"(?i)^(trails?|runs?)( status)?$"),
    "brighton": ("https://www.brightonresort.com/conditions", r"(?i)^lift status$", r"(?i)^(trail status|trails|runs)$"),
}

STATUS = {
    "open": "open", "lift open": "open", "opened": "open",
    "closed": "closed", "lift closed": "closed", "closed for season": "closed",
    "closed for the season": "closed", "hold": "hold", "on hold": "hold",
    "wind hold": "hold", "weather hold": "hold", "delayed": "hold",
    "scheduled": "scheduled", "expected": "scheduled", "opening soon": "scheduled",
}
LIFT_TYPES = re.compile(r"(?i)^(tram|gondola|(detachable|fixed[- ]grip)?\s*chair\s*lift|chairlift|chair|surface( lift)?|"
                        r"conveyor( lift)?|high[- ]speed (quad|six)|quad|triple|double|t-bar|rope tow)$")
NOT_A_NAME = re.compile(r"(?i)(\d{1,2}:\d{2}|[—–]|^@|opens daily|grooming status|^show (closed|open)|lift name|"
                        r"^\d+\s*/\s*\d+|^lifts?$|^lift key$|^toggle|^status$|^hours$)")


@dataclass
class Lift:
    resort: str
    lift: str
    status: str      # open / closed / hold / scheduled / other
    raw: str


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _title(name: str) -> str:
    """'POWDERHORN II' -> 'Powderhorn II' (pages that shout get title-cased)."""
    if not name.isupper():
        return name
    return " ".join(w if re.fullmatch(r"[IVX]+", w) else w.capitalize() for w in name.split())


def parse(resort: str, text: str) -> list[Lift]:
    _, start_re, end_re = PAGES[resort]
    lines = [_norm(x) for x in text.split("\n")]
    lines = [x for x in lines if x]
    start = next((i for i, x in enumerate(lines) if re.search(start_re, x)), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines)) if end_re and re.search(end_re, lines[i])), len(lines))
    out, seen, pending = [], set(), None
    for x in lines[start + 1:end]:
        key = x.lower()
        if key in STATUS:
            if pending and pending.lower() not in seen:
                out.append(Lift(resort, _title(pending), STATUS[key], x))
                seen.add(pending.lower())
            pending = None
        elif re.search(r"[A-Za-z]", x) and not NOT_A_NAME.search(x) and not LIFT_TYPES.match(x) and len(x) <= 40:
            pending = x
    return out


def fetch(resorts=tuple(PAGES), debug_dir: Path | None = None, timeout_ms: int = 45000) -> list[Lift]:
    """Render each resort's page and parse it. Pages that fail are skipped."""
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for r in resorts:
            url = PAGES[r][0]
            page = browser.new_page()
            try:
                try:
                    page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                except Exception:  # noqa: BLE001  slow trackers; use what loaded
                    pass
                page.wait_for_timeout(2500)
                text = page.evaluate("() => document.body ? document.body.innerText : ''")
                lifts = parse(r, text)
                if not lifts and debug_dir is not None:
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    (debug_dir / f"lifts_{r}.txt").write_text(text)
                results += lifts
            except Exception as e:  # noqa: BLE001
                print(f"  ! lift status failed for {r}: {e}")
            finally:
                page.close()
        browser.close()
    return results


def rows(lifts: list[Lift], collected_at: dt.datetime) -> list[dict]:
    return [{"collected_at": collected_at.isoformat(), "resort": x.resort, "lift": x.lift,
             "status": x.status, "raw": x.raw} for x in lifts]


def is_open(lift_rows: list[dict], resort: str, lift: str | None) -> int | str:
    """1/0 if this run saw the camera's lift, '' if unknown. A camera's lift
    name matches exactly, or as whole words inside the page's name
    ('Tram' matches 'Aerial Tram')."""
    if not lift:
        return ""
    want = lift.lower()
    hits = [x for x in lift_rows if x["resort"] == resort and x["lift"].lower() == want]
    if not hits:
        pat = re.compile(r"\b" + re.escape(want) + r"\b")
        hits = [x for x in lift_rows if x["resort"] == resort and pat.search(x["lift"].lower())]
    if not hits:
        return ""
    return int(any(x["status"] == "open" for x in hits))
