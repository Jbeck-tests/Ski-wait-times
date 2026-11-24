"""Join live lift / run / camera data with each resort's map.

For every resort the dashboard gets:
  * per lift: status, the runs you can ski from it (open ones first), its
    lift-line camera;
  * per run on the map: live status when the resort's page names it;
  * cameras with a position (the bottom of their lift, or `latlon` in
    webcams.toml), latest count and current still.

Run lists per lift come from the resort's own page where it groups runs
by lift (Brighton, Snowbasin), otherwise from the map's geometry (runs
reachable from the lift's top that lead back to its bottom).
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from .resort_maps import OUT as MAPDATA, lift_key, norm_name

CAM_CONFIG = Path(__file__).resolve().parent / "config" / "webcams.toml"
OFFICIAL_MAPS = {
    "solitude": "https://www.solitudemountain.com/mountain-and-village/conditions-and-maps",
    "brighton": "https://www.brightonresort.com/conditions",
    "alta": "https://www.alta.com/lift-terrain-status",
    "snowbird": "https://www.snowbird.com/the-mountain/mountain-report/lift-trail-report/",
    "deer_valley": "https://www.deervalley.com/explore-the-mountain/mountain-report",
    "snowbasin": "https://www.snowbasin.com/the-mountain/mountain-report/",
}
DIFF_RANK = {"novice": 0, "easy": 1, "beginner": 1, "easiest": 1, "intermediate": 2, "moderate": 2,
             "advanced intermediate": 3, "advanced": 3, "difficult": 3, "more difficult": 3,
             "expert": 4, "extremely difficult": 5, "freeride": 4, "extreme": 5}


def run_key(name: str | None) -> str:
    """'HOMEWARD BOUND (LOWER)' and 'Lower Homeward Bound' -> same key."""
    n = (name or "").lower()
    m = re.search(r"\((lower|upper|mid|middle)\)", n)
    if m:
        n = m.group(1) + " " + n.replace(m.group(0), "")
    return norm_name(n).replace("middle ", "mid ")


def _tokens(s: str) -> set[str]:
    return set(s.split())


def match_lift(name: str, candidates: dict[str, object]):
    """Best candidate for a lift name (keys are lift_key()s)."""
    k = lift_key(name)
    if k in candidates:
        return candidates[k]
    best, score = None, 0.0
    for ck, v in candidates.items():
        a, b = _tokens(k), _tokens(ck)
        if not a or not b:
            continue
        j = len(a & b) / len(a | b)
        if (k in ck or ck in k) and min(len(k), len(ck)) >= 4:
            j = max(j, 0.75)
        if j > score:
            best, score = v, j
    return best if score >= 0.5 else None


def load_map(resort: str) -> dict | None:
    f = MAPDATA / f"{resort}.json"
    return json.loads(f.read_text()) if f.exists() else None


def _cams_config() -> list[dict]:
    return tomllib.loads(CAM_CONFIG.read_text())["cam"]


def resort_view(resort: str, lift_rows: list[dict], run_rows: list[dict], cams_now: list[dict],
                cam_urls: dict[str, dict], map_: dict | None = None) -> dict:
    """Everything the resort card and the map page need for one resort."""
    map_ = map_ if map_ is not None else load_map(resort)
    status_by_key = {lift_key(x["lift"]): x for x in lift_rows}
    runs_by_key = {}
    for x in run_rows:
        runs_by_key.setdefault(run_key(x["name"]), x)

    # ---- map runs: attach live status
    map_runs = []
    if map_:
        for r in map_["runs"]:
            live = runs_by_key.get(run_key(r["name"])) if r["name"] else None
            map_runs.append({**r, "status": live["status"] if live else None,
                             "groomed": bool(live and live.get("groomed")) or r.get("groomed", False),
                             "page_name": live["name"] if live else None})

    map_diff = {run_key(r["name"]): r["difficulty"] for r in (map_ or {}).get("runs", []) if r["name"] and r["difficulty"]}

    # ---- lifts: page status x map geometry
    map_lifts = {l["key"]: l for l in (map_ or {}).get("lifts", [])}
    groups = {}
    for x in run_rows:
        if x.get("group"):
            groups.setdefault(lift_key(x["group"]), []).append(x)
    lifts = []
    used_map = set()
    for x in lift_rows:
        ml = match_lift(x["lift"], map_lifts)
        if ml:
            used_map.add(ml["id"])
        # runs from this lift: the page's own grouping when it groups by lift
        page_group = match_lift(x["lift"], groups)
        if page_group:
            served = [{"name": y["name"], "status": y["status"],
                       "difficulty": y.get("difficulty") or map_diff.get(run_key(y["name"])),
                       "groomed": y.get("groomed", False), "kind": y.get("kind")} for y in page_group]
            source = "resort"
        elif ml:
            idx = {r["i"]: r for r in map_runs}
            seen, served = set(), []
            for i in ml["laps"]:
                r = idx.get(i)
                if not r or not r["name"] or run_key(r["name"]) in seen:
                    continue
                seen.add(run_key(r["name"]))
                served.append({"name": r["page_name"] or r["name"], "status": r["status"],
                               "difficulty": r["difficulty"], "groomed": r["groomed"], "kind": "run"})
            source = "map"
        else:
            served, source = [], None
        served.sort(key=lambda y: (y["status"] != "open", DIFF_RANK.get((y["difficulty"] or "").lower(), 9), y["name"]))
        lifts.append({"name": x["lift"], "status": x["status"], "map_id": ml["id"] if ml else None,
                      "runs": served, "runs_source": source,
                      "runs_open": sum(1 for y in served if y["status"] == "open")})
    lifts.sort(key=lambda l: (l["status"] != "open", l["name"]))

    # ---- cameras: position + live count + still
    cams = []
    by_title = {c["title"]: c for c in cams_now}
    for c in _cams_config():
        if c["resort"] != resort or c["kind"] not in ("lift_line", "parking", "base"):
            continue
        cid = f"{c['resort']}:{c['title']}"
        pos = c.get("latlon")
        anchor = c.get("lift") or c.get("near_lift")
        if not pos and anchor:
            ml = match_lift(anchor, map_lifts)
            if ml:
                lat, lon = ml["coords"][0]
                # a camera that only overlooks a lift's base sits a little to one side
                pos = [lat, lon] if c.get("lift") else [round(lat - 0.0003, 5), round(lon + 0.0004, 5)]
        now = by_title.get(c["title"])
        url = cam_urls.get(cid, {}).get("url")
        cams.append({"id": cid, "title": c["title"], "kind": c["kind"], "lift": c.get("lift"), "pos": pos,
                     "latest": now["latest"] if now else None, "time": now["time"] if now else None,
                     "lift_open": now["lift_open"] if now else None, "url": url})
    for l in lifts:
        l["cams"] = [c["id"] for c in cams if c["lift"] and lift_key(c["lift"]) and
                     match_lift(c["lift"], {lift_key(l["name"]): True})]

    ski = [x for x in run_rows if x.get("kind") in ("run", "park")]
    return {
        "resort": resort,
        "lifts": lifts,
        "runs_open": sum(1 for x in ski if x["status"] == "open"), "runs_total": len(ski),
        "areas": [x for x in run_rows if x.get("kind") in ("area", "gate")],
        "open_runs": [x["name"] for x in ski if x["status"] == "open"],
        "map": None if not map_ else {
            "bounds": map_["bounds"], "attribution": map_["attribution"],
            "lifts": [{**l, "status": next((x["status"] for x in lift_rows
                                            if match_lift(x["lift"], {l["key"]: True})), None)}
                      for l in map_["lifts"]],
            "runs": map_runs},
        "cams": cams,
        "official": OFFICIAL_MAPS.get(resort),
    }
