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
import html as _html
import re
from dataclasses import dataclass
from pathlib import Path

PAGES = {
    # resort: (url, section start, section end) -- regexes on whole lines
    "solitude": ("https://www.solitudemountain.com/mountain-and-village/conditions-and-maps", r"^LIFTS$", r"^TRAILS$"),
    "deer_valley": ("https://www.deervalley.com/explore-the-mountain/mountain-report", r"^LIFTS$", r"^TRAILS$"),
    "alta": ("https://www.alta.com/lift-terrain-status", r"^Mountain environments change daily", r"(?i)terrain status dashboard"),
    "snowbasin": ("https://www.snowbasin.com/the-mountain/mountain-report/", r"^Lift Name", r"^Trails$"),
    # Snowbird's table rows read "<name>" then "<type> <status>".
    "snowbird": ("https://www.snowbird.com/the-mountain/mountain-report/lift-trail-report/", r"^Lift Status Type", r"(?i)^(gad valley|terrain & gates)$"),
    # Brighton shows each lift's status as an icon; see parse_brighton_html().
    "brighton": ("https://www.brightonresort.com/conditions", r"(?i)^show legend$", r"(?i)^trail status$"),
}

# Pages whose run lists are collapsed: click these to expand before reading.
EXPAND = {"snowbird": re.compile(r"^Trail \d+ of \d+$")}

STATUS = {
    "open": "open", "lift open": "open", "opened": "open",
    "closed": "closed", "lift closed": "closed", "closed for season": "closed",
    "closed for the season": "closed", "hold": "hold", "on hold": "hold",
    "wind hold": "hold", "weather hold": "hold", "delayed": "hold",
    "scheduled": "scheduled", "expected": "scheduled", "opening soon": "scheduled",
}
TYPE_PREFIX = re.compile(r"(?i)^(tram|gondola|double|triple|quad|six|carpet|conveyor|chair|surface|t-bar)\s+")
LIFT_TYPES = re.compile(r"(?i)^(tram|gondola|(detachable|fixed[- ]grip)?\s*chair\s*lift|chairlift|chair|surface( lift)?|"
                        r"conveyor( lift)?|high[- ]speed (quad|six)|quad|triple|double|t-bar|rope tow)$")
NOT_A_NAME = re.compile(r"(?i)(\d{1,2}:\d{2}|[—–]|^@|opens daily|grooming status|^show (closed|open)|lift name|"
                        r"^\d+\s*/\s*\d+|^lifts?$|^lift key$|^toggle|^status$|^hours$)")


@dataclass
class Lift:
    resort: str
    lift: str
    status: str      # open / closed / hold / scheduled / unknown
    raw: str


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _title(name: str) -> str:
    """'POWDERHORN II' -> 'Powderhorn II' (pages that shout get title-cased)."""
    if not name.isupper():
        return name
    return " ".join(w if re.fullmatch(r"[IVX]+", w) else w.capitalize() for w in name.split())


HOURS = re.compile(r"(?i)^\d{1,2}(:\d{2})?\s*(am|pm)\s*[-–—]\s*\d{1,2}(:\d{2})?\s*(am|pm)$")


def _status_of(line: str) -> str | None:
    key = line.lower()
    if key in STATUS:
        return STATUS[key]
    stripped = TYPE_PREFIX.sub("", line).lower()      # "Tram Closed for the season"
    if stripped == key:
        return None
    if HOURS.match(stripped):                         # Snowbird: "Tram 9 am - 3:45 pm" = running
        return "open"
    return STATUS.get(stripped)


def parse_brighton(text: str) -> list[Lift]:
    """Brighton draws each lift's status as an icon, so the text only has the
    names and a summary ('Lifts Open 0 of 8'). All-open and all-closed are
    unambiguous; mixed days are marked unknown until the icons are decoded."""
    _, start_re, end_re = PAGES["brighton"]
    lines = [x for x in (_norm(y) for y in text.split("\n")) if x]
    joined = "\n".join(lines)
    m = re.search(r"Lifts Open\n(\d+)\nof (\d+)", joined)
    start = next((i for i, x in enumerate(lines) if re.search(start_re, x)), None)
    if m is None or start is None:
        return []
    end = next((i for i in range(start + 1, len(lines)) if re.search(end_re, lines[i])), len(lines))
    names = [x for x in lines[start + 1:end] if re.search(r"[A-Za-z]", x) and len(x) <= 40]
    n_open, total = int(m.group(1)), int(m.group(2))
    status = "closed" if n_open == 0 else "open" if n_open == total else "unknown"
    return [Lift("brighton", n, status, f"{n_open} of {total} open") for n in names]


def parse_brighton_html(markup: str) -> list[Lift]:
    """Brighton's markup: one <li> per lift, with a lift-type icon, the name
    in <p>, and a status icon whose alt text is 'Open' / 'Closed' / ..."""
    out, seen = [], set()
    for item in re.findall(r"<li\b[^>]*>(.*?)</li>", markup, flags=re.S):
        if "/Lift/Type/" not in item:
            continue
        name = re.search(r"<p>(.*?)</p>", item, re.S)
        st = re.search(r'<img alt="([^"]+)"[^>]*src="[^"]*/Common/Status/[^"]+"', item)
        if not name or not st:
            continue
        n = _norm(_html.unescape(re.sub(r"<[^>]+>", "", name.group(1))))
        status = STATUS.get(st.group(1).lower(), "unknown")
        if n and n.lower() not in seen:
            out.append(Lift("brighton", n, status, st.group(1)))
            seen.add(n.lower())
    return out


def parse(resort: str, text: str) -> list[Lift]:
    if resort == "brighton":
        return parse_brighton(text)
    _, start_re, end_re = PAGES[resort]
    lines = [_norm(x) for x in text.split("\n")]
    lines = [x for x in lines if x]
    start = next((i for i, x in enumerate(lines) if re.search(start_re, x)), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines)) if end_re and re.search(end_re, lines[i])), len(lines))
    out, seen, pending = [], set(), None
    for x in lines[start + 1:end]:
        st = _status_of(x)
        if st:
            if pending and pending.lower() not in seen:
                out.append(Lift(resort, _title(pending), st, x))
                seen.add(pending.lower())
            pending = None
        elif re.search(r"[A-Za-z]", x) and not NOT_A_NAME.search(x) and not LIFT_TYPES.match(x) and len(x) <= 40:
            pending = x
    return out


def brighton_items(markup: str) -> str:
    """Just the lift and trail rows of Brighton's page (status is in icons),
    with a `<!-- group: X -->` marker where each lift's trail list starts."""
    parts = []
    pat = r'aria-label="Open accordion for ([^"]+)"|<li\b[^>]*>.*?</li>'
    for m in re.finditer(pat, markup, flags=re.S):
        if m.group(1):
            parts.append(f"<!-- group: {m.group(1)} -->")
        elif "/Lift/Type/" in m.group(0) or "/Trail/Difficulty/" in m.group(0):
            parts.append(m.group(0))
    return "<ul>\n" + "\n".join(parts) + "\n</ul>\n"


def snowbird_items(markup: str) -> str:
    """Snowbird's run tables (hidden in collapsed panels, so not in the page
    text) as compact lines: 'group<TAB>name' and
    'run<TAB>name<TAB>status<TAB>difficulty<TAB>groomed'."""
    out = []
    pat = r'<span class="panel-label">([^<]+)</span>|<tr>(.*?)</tr>'
    for m in re.finditer(pat, markup, flags=re.S):
        if m.group(1):
            out.append("group\t" + _html.unescape(m.group(1)).strip())
            continue
        row = m.group(2)
        if 'class="difficulty"' not in row:
            continue                                   # lift rows: status comes from the text
        name = re.search(r'<td class="name">(.*?)</td>', row, re.S)
        st_td = re.search(r'<td class="status">(.*?)</td>', row, re.S)
        if not name or not st_td:
            continue
        cls = re.search(r'class="[^"]*\bicon\b([^"]*)"', st_td.group(1))
        tokens = set((cls.group(1) if cls else "").split())
        fills = set(re.findall(r'fill="#([0-9A-Fa-f]{6})"', st_td.group(1).upper()))
        if tokens & {"open"}:
            status = "open"
        elif tokens & {"closed"}:
            status = "closed"
        elif tokens & {"hold", "on-hold", "expected", "scheduled"}:
            status = "hold" if tokens & {"hold", "on-hold"} else "scheduled"
        elif "D0021B" in fills:
            status = "closed"
        elif fills & {"7ED321", "4CA252", "417505", "5CB85C", "2EA44F"}:
            status = "open"
        else:
            status = "unknown"
        diff = re.search(r"difficulty-level-([a-z0-9-]+)", row)
        groomed = bool(re.search(r'<td class="groomed">\s*<(?!/td)', row))
        n = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", name.group(1)))).strip()
        out.append("\t".join(["run", n, status, diff.group(1) if diff else "", "1" if groomed else ""]))
    return "\n".join(out) + "\n"


def fetch(resorts=tuple(PAGES), debug_dir: Path | None = None, timeout_ms: int = 45000,
          pages_dir: Path | None = None) -> list[Lift]:
    """Render each resort's page and parse it. Pages that fail are skipped.
    With `pages_dir`, the page text is kept (latest only) so run status can
    be parsed later (Brighton: its lift/trail rows, which carry status icons)."""
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
                if r in EXPAND:          # runs hidden in collapsed sections
                    # click the nearest button-like ancestor of each "Trail N of M" label
                    page.evaluate("""(src) => {
                        const re = new RegExp(src);
                        const els = [...document.querySelectorAll('body *')].filter(e =>
                            e.children.length === 0 && re.test((e.textContent || '').trim()));
                        for (const e of els) {
                            const t = e.closest('button,[role=button],summary,[aria-expanded],a,[onclick]') || e.parentElement;
                            if (t && t.getAttribute('aria-expanded') !== 'true') t.click();
                        }
                    }""", EXPAND[r].pattern)
                    page.wait_for_timeout(2000)
                    if debug_dir is not None and not (debug_dir / f"expand_{r}.html").exists():
                        debug_dir.mkdir(parents=True, exist_ok=True)
                        (debug_dir / f"expand_{r}.html").write_text(page.content())
                text = page.evaluate("() => document.body ? document.body.innerText : ''")
                markup = page.content() if r == "brighton" else ""
                lifts = parse_brighton_html(markup) if r == "brighton" else []
                lifts = lifts or parse(r, text)
                if pages_dir is not None and text.strip():
                    pages_dir.mkdir(parents=True, exist_ok=True)
                    (pages_dir / f"{r}.txt").write_text(text)
                    if markup:
                        (pages_dir / f"{r}.html").write_text(brighton_items(markup))
                    if r == "snowbird":
                        full = page.content()
                        items = snowbird_items(full)
                        (pages_dir / "snowbird.runs").write_text(items)
                        if debug_dir is not None and "\tunknown\t" in items:   # a status icon we can't read
                            debug_dir.mkdir(parents=True, exist_ok=True)
                            (debug_dir / "runs_snowbird.html").write_text(full)
                if debug_dir is not None and (not lifts or any(x.status == "unknown" for x in lifts)):
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    (debug_dir / f"lifts_{r}.txt").write_text(text)
                    # keep the markup too, so icon-based statuses can be decoded
                    (debug_dir / f"lifts_{r}.html").write_text(page.content())
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
