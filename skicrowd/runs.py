"""Run (trail) open/closed status, parsed from the resort pages the collector
saves each run (collector-data/pages/<resort>.txt; Brighton: <resort>.html).

Each run: {name, status, group, kind, difficulty, groomed}
  group  - the page's heading above the run: a lift (Snowbasin, Brighton),
           an area (Solitude, Deer Valley), or the lift that reaches a
           terrain area (Alta: "via Collins")
  kind   - run / area (Alta's terrain areas) / gate / park
"""
from __future__ import annotations

import html as _html
import re
from pathlib import Path

from .lifts import _norm, _title

STATUS = {
    "open": "open", "trail open": "open", "opened": "open",
    "closed": "closed", "trail closed": "closed", "closed for season": "closed",
    "closed for the season": "closed", "on hold": "hold", "hold": "hold",
    "expected": "scheduled", "scheduled": "scheduled", "scheduled for a.m.": "scheduled",
    "route in progress": "scheduled",
}
# section (start, end) on whole lines
SECTIONS = {
    "solitude": (r"^TRAILS$", r"^TERRAIN AREAS$"),
    "deer_valley": (r"^TRAILS$", r"^ACTIVITIES$"),
    "snowbasin": (r"^Trails$", r"^(Parking|Access Gates Safety Information)$"),
    "snowbird": (r"^(Gad Valley|Mineral Basin|Peruvian Gulch)$", r"^Terrain & Gates$"),
}
NOISE = re.compile(r"(?i)^(toggle accordion|trail name|status|trail key|show closed|legend|sort|alphabetical.*|"
                   r"difficulty:|features:|grooming:?|\d+\s*/\s*\d+.*|trail \d+ of \d+|"
                   r"easy|easiest|intermediate|difficult|more difficult|moderate|expert|extremely difficult|"
                   r"advanced intermediate|snowshoe|glades|moguls.*|terrain park|groomed|.*\d{1,2}:\d{2}.*)$")


def _clean(name: str) -> str:
    return _title(_norm(name))


def _kind(name: str, group: str | None) -> str:
    n = f"{name} {group or ''}".lower()
    if group and group.lower() in ("nordic", "snowshoe"):
        return "nordic"
    return "gate" if "gate" in name.lower() else "park" if "park" in n and "terrain" in n else "run"


def parse_text(resort: str, text: str) -> list[dict]:
    """Pages that list '<name>' then '<status>' under area/lift headings."""
    if resort not in SECTIONS:
        return []
    start_re, end_re = SECTIONS[resort]
    lines = [x for x in (_norm(y) for y in text.split("\n")) if x]
    start = next((i for i, x in enumerate(lines) if re.search(start_re, x)), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines)) if re.search(end_re, lines[i])), len(lines))
    out, seen, pending, group = [], set(), None, None
    for x in lines[start:end]:
        st = STATUS.get(x.lower())
        if st:
            if pending and (pending.lower(), group) not in seen:
                out.append({"name": _clean(pending), "status": st, "group": group and _clean(group),
                            "kind": _kind(pending, group), "difficulty": None, "groomed": False})
                seen.add((pending.lower(), group))
            pending = None
        elif x.lower() == "groomed" and out:
            out[-1]["groomed"] = True
        elif NOISE.match(x) or not re.search(r"[A-Za-z0-9]", x) or len(x) > 50:
            continue
        else:
            if pending is not None:       # two names in a row: the first was a heading
                group = pending
            pending = x
    return out


def parse_alta(text: str) -> list[dict]:
    """Alta lists terrain areas, not named runs: '<status>', '<AREA>', 'via <lift>'."""
    lines = [x for x in (_norm(y) for y in text.split("\n")) if x]
    out, i = [], 0
    in_section, kind = False, "area"
    while i < len(lines):
        x = lines[i]
        if x.startswith("Status Area") or x.startswith("Status\tArea"):
            in_section, kind = True, "area"
        elif x.startswith("Status Gate") or x.startswith("Status\tGate"):
            in_section, kind = True, "gate"
        elif in_section and x.lower() in STATUS and i + 1 < len(lines):
            name = lines[i + 1]
            via = lines[i + 2] if i + 2 < len(lines) and lines[i + 2].lower().startswith("via ") else None
            out.append({"name": _clean(name), "status": STATUS[x.lower()], "group": via and via[4:].strip(),
                        "kind": kind, "difficulty": None, "groomed": False})
            i += 3 if via else 2
            continue
        elif in_section and x.isupper() and x.startswith("JOIN"):
            break
        i += 1
    return out


def parse_brighton(markup: str) -> list[dict]:
    """Brighton rows carry status, difficulty and grooming as icons."""
    out, group = [], None
    for m in re.finditer(r"<!-- group: (.*?) -->|<li\b[^>]*>(.*?)</li>", markup, flags=re.S):
        if m.group(1):
            group = _html.unescape(m.group(1)).strip()
            continue
        it = m.group(2)
        if "/Trail/Difficulty/" not in it:
            continue
        name = re.search(r"<p>(.*?)</p>", it, re.S)
        st = re.search(r'<img alt="([^"]+)"[^>]*src="[^"]*/Common/Status/', it)
        diff = re.search(r'<img alt="([^"]+)"[^>]*src="[^"]*/Trail/Difficulty/', it)
        feats = re.findall(r'<img alt="([^"]+)"[^>]*src="[^"]*/Trail/Feature/', it)
        if not name or not st:
            continue
        n = _clean(_html.unescape(re.sub(r"<[^>]+>", "", name.group(1))))
        out.append({"name": n, "status": STATUS.get(st.group(1).lower(), "unknown"), "group": group,
                    "kind": "park" if group and "park" in group.lower() else "run",
                    "difficulty": diff.group(1) if diff else None, "groomed": "Groomed" in feats})
    return out


SNOWBIRD_DIFF = {"green": "Easiest", "blue": "More Difficult", "black": "Expert", "black-2": "Expert",
                  "black3": "Expert", "black-3": "Expert"}


def parse_snowbird(text: str) -> list[dict]:
    """Lines written by lifts.snowbird_items()."""
    out, group = [], None
    for line in text.splitlines():
        f = line.split("\t")
        if f[0] == "group" and len(f) > 1:
            group = f[1]
        elif f[0] == "run" and len(f) >= 5:
            out.append({"name": _clean(f[1]), "status": f[2], "group": group,
                        "kind": "park" if "park" in f[1].lower() else "run",
                        "difficulty": SNOWBIRD_DIFF.get(f[3]), "groomed": f[4] == "1"})
    return out


def load(pages_dir: Path) -> dict[str, list[dict]]:
    """Runs per resort from the collector's saved pages (missing pages skipped)."""
    out = {}
    for r in ("solitude", "deer_valley", "snowbasin", "snowbird", "alta", "brighton"):
        try:
            if r == "snowbird" and (pages_dir / "snowbird.runs").exists():
                runs = parse_snowbird((pages_dir / "snowbird.runs").read_text())
            elif r == "brighton" and (pages_dir / "brighton.html").exists():
                runs = parse_brighton((pages_dir / "brighton.html").read_text())
            elif r == "alta" and (pages_dir / "alta.txt").exists():
                runs = parse_alta((pages_dir / "alta.txt").read_text())
            elif (pages_dir / f"{r}.txt").exists():
                runs = parse_text(r, (pages_dir / f"{r}.txt").read_text())
            else:
                continue
        except Exception as e:  # noqa: BLE001  one bad page shouldn't sink the dashboard
            print(f"  ! run status for {r} failed: {e}")
            continue
        if runs:
            out[r] = runs
    return out
