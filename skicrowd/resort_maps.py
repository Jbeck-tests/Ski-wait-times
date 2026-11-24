"""Turn raw OpenStreetMap data into compact per-resort maps.

For each resort: lifts (bottom -> top), runs (drawn downhill), and for every
lift the runs you can ski from its top and get back to its bottom ("laps")
plus everything reachable downhill from its top ("reach").

Connectivity is geometric: a skier on a run can move onto another run
where the two come within LINK_M metres, entering it at the first such
point and carrying on downhill (OSM draws runs downhill and lifts uphill).
A run counts as served by a lift only if it is entered in its upper half.

Output: skicrowd/mapdata/<resort>.json  (ODbL: (c) OpenStreetMap contributors)
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np

from .maps import REGION_RESORTS

OUT = Path(__file__).resolve().parent / "mapdata"
AREA_NAME = {"solitude": "solitude", "brighton": "brighton", "alta": "alta", "snowbird": "snowbird",
             "deer_valley": "deer valley", "snowbasin": "snowbasin"}
LIFT_TYPES = {"chair_lift", "gondola", "cable_car", "mixed_lift", "drag_lift", "t-bar", "j-bar", "platter",
              "rope_tow", "magic_carpet"}
TOP_M = 90        # a run starting this close to a lift's top is reachable from it
BOTTOM_M = 90     # ...and one ending this close to a lift's bottom returns to it
LINK_M = 25       # runs this close are connected
STEP_M = 12       # densify lines to this spacing before matching

GENERIC = re.compile(r"\b(express|lift|chair|chairlift|quad|triple|double|six|high[- ]speed|detachable|the)\b")


def norm_name(s: str | None) -> str:
    s = (s or "").lower().replace("’", "'")
    s = re.sub(r"[^a-z0-9 ]+", " ", s.replace("'", ""))
    return re.sub(r"\s+", " ", s).strip()


ROMAN = {"ii": "2", "iii": "3", "iv": "4"}


def lift_key(s: str | None) -> str:
    k = re.sub(r"\s+", " ", GENERIC.sub(" ", norm_name(s))).strip()
    return " ".join(ROMAN.get(w, w) for w in k.split())


# ------------------------------------------------------------- geometry
class Proj:
    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0, self.k = lat0, lon0, math.cos(math.radians(lat0))

    def xy(self, pts) -> np.ndarray:
        a = np.asarray(pts, dtype=float).reshape(-1, 2)
        return np.c_[(a[:, 1] - self.lon0) * self.k * 111320.0, (a[:, 0] - self.lat0) * 110540.0]


def densify(xy: np.ndarray, step: float = STEP_M) -> tuple[np.ndarray, np.ndarray]:
    """Points every ~step metres, and cumulative distance along the line."""
    out = [xy[0]]
    for a, b in zip(xy[:-1], xy[1:]):
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        out.extend(a + (b - a) * (i / n) for i in range(1, n + 1))
    p = np.asarray(out)
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]
    return p, d


def simplify(pts: list[list[float]], tol_m: float = 4.0) -> list[list[float]]:
    """Douglas-Peucker on lat/lon (tolerance in metres), rounded to 5 dp."""
    if len(pts) < 3:
        return [[round(a, 5), round(b, 5)] for a, b in pts]
    pr = Proj(pts[0][0], pts[0][1])
    xy = pr.xy(pts)
    keep = np.zeros(len(pts), bool)
    keep[[0, -1]] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = xy[i], xy[j]
        ab = b - a
        L = np.dot(ab, ab)
        seg = xy[i + 1:j]
        if L == 0:
            dist = np.linalg.norm(seg - a, axis=1)
        else:
            t = np.clip(((seg - a) @ ab) / L, 0, 1)
            dist = np.linalg.norm(seg - (a + t[:, None] * ab), axis=1)
        k = int(np.argmax(dist))
        if dist[k] > tol_m:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return [[round(p[0], 5), round(p[1], 5)] for p, k in zip(pts, keep) if k]


def _geom(e) -> list[list[float]]:
    return [[g["lat"], g["lon"]] for g in e.get("geometry", []) if g]


def _rings(e) -> list[list[list[float]]]:
    """Outer rings of a winter_sports way / multipolygon relation."""
    if e["type"] == "way":
        g = _geom(e)
        return [g] if len(g) > 3 else []
    segs = [[[p["lat"], p["lon"]] for p in m.get("geometry", [])] for m in e.get("members", [])
            if m.get("role") in ("outer", "") and m.get("geometry")]
    rings = []
    while segs:
        ring = segs.pop(0)
        changed = True
        while changed and ring[0] != ring[-1]:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]:
                    ring += s[1:]
                elif s[-1] == ring[-1]:
                    ring += s[::-1][1:]
                elif s[-1] == ring[0]:
                    ring = s[:-1] + ring
                elif s[0] == ring[0]:
                    ring = s[::-1][:-1] + ring
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if len(ring) > 3:
            rings.append(ring)
    return rings


def _inside(pt, ring) -> bool:
    y, x = pt
    inside = False
    for (y1, x1), (y2, x2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-15) + x1:
            inside = not inside
    return inside


# ------------------------------------------------------------- per resort
def _assign(elements, resorts: list[str]) -> dict[str, list]:
    """Split a region's lifts/runs between its resorts by ski-area outline."""
    areas = {r: [] for r in resorts}
    for e in elements:
        t = e.get("tags", {})
        if t.get("landuse") != "winter_sports":
            continue
        name = norm_name(t.get("name"))
        for r in resorts:
            if AREA_NAME[r] in name:
                areas[r] += _rings(e)
    out = {r: [] for r in resorts}
    for e in elements:
        t = e.get("tags", {})
        if t.get("landuse") == "winter_sports" or e["type"] == "node":
            continue
        g = _geom(e) or [pt for m in e.get("members", []) for pt in _geom(m)]
        if not g:
            continue
        mid = g[len(g) // 2]
        hits = [r for r in resorts if any(_inside(mid, ring) for ring in areas[r])]
        if not hits and len(resorts) == 1:
            hits = resorts
        if not hits:   # just outside every outline: nearest outline within 400 m
            pr = Proj(*mid)
            best = min(((float(np.min(np.linalg.norm(pr.xy(ring), axis=1))), r)
                        for r in resorts for ring in areas[r]), default=(1e9, None))
            hits = [best[1]] if best[0] < 400 else []
        for r in hits[:1]:
            out[r].append(e)
    return out


def build_resort(resort: str, elements: list[dict]) -> dict:
    lifts, runs = [], []
    rel_names = {}            # way id -> (name, difficulty) from piste route relations
    for e in elements:
        t = e.get("tags", {})
        if e["type"] == "relation" and t.get("piste:type") == "downhill":
            for m in e.get("members", []):
                if m.get("type") == "way":
                    rel_names[m["ref"]] = (t.get("name") or t.get("piste:name"), t.get("piste:difficulty"))
    for e in elements:
        t, g = e.get("tags", {}), _geom(e)
        if e["type"] != "way" or len(g) < 2:
            continue
        if t.get("aerialway") in LIFT_TYPES:
            lifts.append({"id": e["id"], "name": t.get("name") or t.get("aerialway").replace("_", " ").title(),
                          "type": t["aerialway"], "coords": g})
        elif t.get("piste:type") == "downhill" and t.get("area") != "yes" and g[0] != g[-1]:
            rn, rd = rel_names.get(e["id"], (None, None))
            name = t.get("piste:name") or t.get("name") or rn
            runs.append({"id": e["id"], "name": name, "difficulty": t.get("piste:difficulty") or rd,
                         "groomed": t.get("piste:grooming") in ("classic", "mogul", "classic+skating"),
                         "coords": g})
    if not lifts and not runs:
        return {}
    all_pts = [p for x in lifts + runs for p in x["coords"]]
    lat0, lon0 = np.mean([p[0] for p in all_pts]), np.mean([p[1] for p in all_pts])
    pr = Proj(lat0, lon0)

    from scipy.spatial import cKDTree      # only needed to (re)build maps, not by the collector

    # densified runs in one KD-tree
    dens = [densify(pr.xy(r["coords"])) for r in runs]
    owner = np.concatenate([np.full(len(p), i) for i, (p, _) in enumerate(dens)]) if dens else np.array([], int)
    index = np.concatenate([np.arange(len(p)) for p, _ in dens]) if dens else np.array([], int)
    tree = cKDTree(np.concatenate([p for p, _ in dens])) if dens else None

    def entries_near(points: np.ndarray, radius: float) -> dict[int, int]:
        """run -> earliest densified index within radius of any of points."""
        hit = {}
        for lst in tree.query_ball_point(points, radius):
            for k in lst:
                w, j = int(owner[k]), int(index[k])
                if j < hit.get(w, 1 << 30):
                    hit[w] = j
        return hit

    def downhill(start: dict[int, int]) -> dict[int, int]:
        best, stack = dict(start), list(start.items())
        while stack:
            w, j = stack.pop()
            if best.get(w, 1 << 30) < j:
                continue
            seg = dens[w][0][j:]
            for v, k in entries_near(seg, LINK_M).items():
                if v != w and k < best.get(v, 1 << 30):
                    best[v] = k
                    stack.append((v, k))
        return best

    bottoms = [pr.xy([l["coords"][0]])[0] for l in lifts]
    returns_to = {}     # run -> set of lift indexes whose bottom it reaches
    if tree is not None:
        for w in range(len(runs)):
            reach = downhill({w: 0})
            ends = np.array([dens[v][0][-1] for v in reach])
            near = {i for i, b in enumerate(bottoms) if np.min(np.linalg.norm(ends - b, axis=1)) <= BOTTOM_M}
            # also: passing right by a bottom station counts
            for i, b in enumerate(bottoms):
                if any(np.min(np.linalg.norm(dens[v][0][k:] - b, axis=1)) <= LINK_M * 2 for v, k in reach.items()):
                    near.add(i)
            returns_to[w] = near

    def served(entry: dict[int, int]) -> set[int]:
        return {w for w, j in entry.items() if dens[w][1][j] <= 0.5 * dens[w][1][-1]}

    out_lifts = []
    for i, l in enumerate(lifts):
        top = pr.xy([l["coords"][-1]])
        reach = downhill(entries_near(top, TOP_M)) if tree is not None else {}
        s = served(reach)
        laps = sorted(w for w in s if i in returns_to.get(w, set()))
        out_lifts.append({"id": l["id"], "name": l["name"], "key": lift_key(l["name"]), "type": l["type"],
                          "coords": simplify(l["coords"], 2.0), "laps": laps, "reach": sorted(s)})
    out_runs = [{"id": r["id"], "i": i, "name": r["name"], "key": norm_name(r["name"]), "difficulty": r["difficulty"],
                 "groomed": r["groomed"], "coords": simplify(r["coords"])} for i, r in enumerate(runs)]
    # initial view: the lifts (a few long or stray runs shouldn't zoom the map out)
    frame = [p for l in lifts for p in (l["coords"][0], l["coords"][-1])] or all_pts
    lats = [p[0] for p in frame]
    lons = [p[1] for p in frame]
    pad_lat, pad_lon = 0.15 * (max(lats) - min(lats)) + 0.002, 0.15 * (max(lons) - min(lons)) + 0.002
    lats += [min(lats) - pad_lat, max(lats) + pad_lat]
    lons += [min(lons) - pad_lon, max(lons) + pad_lon]
    return {"resort": resort, "bounds": [[min(lats), min(lons)], [max(lats), max(lons)]],
            "lifts": out_lifts, "runs": out_runs,
            "attribution": "Map data © OpenStreetMap contributors (ODbL)"}


def build_all(raw_dir: Path, out_dir: Path = OUT) -> dict[str, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    res = {}
    for region, resorts in REGION_RESORTS.items():
        f = raw_dir / f"raw_{region}.json"
        if not f.exists():
            print(f"{region}: no raw data")
            continue
        elements = json.loads(f.read_text())["elements"]
        for r, els in _assign(elements, resorts).items():
            m = build_resort(r, els)
            if not m:
                print(f"{r}: nothing mapped")
                continue
            (out_dir / f"{r}.json").write_text(json.dumps(m, separators=(",", ":")))
            named = sum(1 for x in m["runs"] if x["name"])
            print(f"{r}: {len(m['lifts'])} lifts, {len(m['runs'])} run segments ({named} named)")
            res[r] = m
    return res
