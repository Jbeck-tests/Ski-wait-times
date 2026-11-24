"""Live crowd-signal collector (run every ~20 min by GitHub Actions).

Each run appends to CSVs in an output directory (the `collector-data` branch):

  canyon_live.csv    UDOT net vehicles currently in Little / Big Cottonwood
  webcam_counts.csv  people / vehicles detected in each resort webcam frame

Images are analysed in memory and never stored; only counts and quality
flags are kept.

`summarize()` turns the collected rows into daily calibration observations.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import io
import re
import tomllib
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from . import lifts as lift_status
from . import udot

CAM_CONFIG = Path(__file__).resolve().parent / "config" / "webcams.toml"
SKIUTAH_URL = "https://www.skiutah.com/resorts/webcams"
UA = "skicrowd-collector/0.1 (personal crowd research; low-frequency)"
MT = "America/Denver"
COCO_PERSON, COCO_VEHICLES = 1, {3, 4, 6, 8}  # torchvision COCO ids: person; car, motorcycle, bus, truck


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ------------------------------------------------------------- camera list
def load_cams() -> list[dict]:
    with open(CAM_CONFIG, "rb") as f:
        return tomllib.load(f)["cam"]


def resolve_skiutah(page_html: str) -> dict[tuple[str, str], dict]:
    """(section, title) -> {url, ts} from Ski Utah's webcam page."""
    s = html.unescape(page_html)
    out = {}
    for sec in s.split('<h2 class="cams-section-title">')[1:]:
        m = re.search(r"<span>(.*?)</span>", sec)
        if not m:
            continue
        section = m.group(1).strip()
        for im in re.finditer(r'<img src="(https://www\.skiutah\.com/files/blob/webcam/[^"?]+)\?_ts=(\d+)"[^>]*alt="([^"]*)"', sec):
            out[(section, im.group(3).strip())] = {"url": im.group(1), "ts": im.group(2)}
    return out


def match_cam(cam: dict, found: dict) -> dict | None:
    want = cam["title"].lower()
    for (section, title), v in found.items():
        if section == cam["section"] and want in title.lower():
            return {**v, "title": title}
    return None


# ------------------------------------------------------------ image checks
def image_quality(img) -> dict:
    """Brightness (night), sharpness (fog / snow on lens)."""
    g = np.asarray(img.convert("L").resize((320, 180)), dtype=float)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return {"brightness": round(float(g.mean()), 1), "sharpness": round(float(lap.var()), 1)}


def in_roi(cx: float, cy: float, roi) -> bool:
    if not roi:
        return True
    inside, n = False, len(roi)
    for i in range(n):
        x1, y1 = roi[i]
        x2, y2 = roi[(i + 1) % n]
        if (y1 > cy) != (y2 > cy) and cx < (x2 - x1) * (cy - y1) / (y2 - y1 + 1e-12) + x1:
            inside = not inside
    return inside


class Detector:
    """torchvision Faster R-CNN (ResNet50-FPN v2, COCO). BSD-licensed."""

    def __init__(self, score: float = 0.45):
        import torch
        from torchvision.models.detection import FasterRCNN_ResNet50_FPN_V2_Weights, fasterrcnn_resnet50_fpn_v2
        torch.set_num_threads(max(1, torch.get_num_threads()))
        self.torch = torch
        self.model = fasterrcnn_resnet50_fpn_v2(weights=FasterRCNN_ResNet50_FPN_V2_Weights.COCO_V1,
                                                box_score_thresh=score, min_size=800, max_size=1400).eval()

    def count(self, img, roi=None) -> dict:
        from torchvision.transforms.functional import to_tensor
        with self.torch.no_grad():
            out = self.model([to_tensor(img.convert("RGB"))])[0]
        w, h = img.size
        people = vehicles = 0
        for (x1, y1, x2, y2), lab in zip(out["boxes"].tolist(), out["labels"].tolist()):
            if not in_roi((x1 + x2) / 2 / w, (y1 + y2) / 2 / h, roi):
                continue
            people += lab == COCO_PERSON
            vehicles += lab in COCO_VEHICLES
        return {"people": people, "vehicles": vehicles}


# --------------------------------------------------------------------- run
def _append(path: Path, rows: list[dict], key: list[str]) -> int:
    if not rows:
        return 0
    new = pd.DataFrame(rows)
    if path.exists():
        old = pd.read_csv(path, dtype=str)
        allr = pd.concat([old, new.astype(str)], ignore_index=True).drop_duplicates(key, keep="first")
        added = len(allr) - len(old)
    else:
        allr, added = new, len(new)
    allr.to_csv(path, index=False)
    return added


def run(out_dir: str | Path, detector: Detector | None = None, verbose: bool = True) -> None:
    from PIL import Image

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

    # 1) UDOT live canyon volumes
    try:
        live = udot.fetch_live()
        live["collected_at"] = now.isoformat()
        n = _append(out / "canyon_live.csv", live.to_dict("records"), ["observed_at", "canyon"])
        if verbose:
            print(f"canyon_live: +{n} rows  {live[['canyon', 'net_vehicles_in_canyon']].to_dict('records')}")
    except Exception as e:  # noqa: BLE001
        print(f"canyon_live failed: {e}")

    # 2) resort operating status (builds a record of real opening/closing dates)
    try:
        from .status import fetch_status
        st = fetch_status()
        srows = [{"as_of": st["as_of"], "resort": r, "status": v["status"], "date": v.get("date") or "",
                  "raw": v.get("raw", ""), "collected_at": now.isoformat()} for r, v in st["resorts"].items()]
        n = _append(out / "resort_status.csv", srows, ["as_of", "resort", "status", "date"])
        (out / "season_status.json").write_text(__import__("json").dumps(st, indent=2) + "\n")
        if verbose:
            print(f"resort_status: +{n} rows")
    except Exception as e:  # noqa: BLE001
        print(f"resort_status failed: {e}")

    # 3) lift open/closed status (rendered public lift-status pages)
    lift_rows = []
    try:
        lift_rows = lift_status.rows(lift_status.fetch(debug_dir=out / "debug", pages_dir=out / "pages"), now)
        n = _append(out / "lift_status.csv", lift_rows, ["collected_at", "resort", "lift"])
        if verbose:
            counts = {}
            for x in lift_rows:
                o, t = counts.get(x["resort"], (0, 0))
                counts[x["resort"]] = (o + (x["status"] == "open"), t + 1)
            print("lift_status: +%d rows  " % n + ", ".join(f"{r} {o}/{t} open" for r, (o, t) in counts.items()))
    except Exception as e:  # noqa: BLE001
        print(f"lift_status failed: {e}")

    # 4) webcams
    counts_path = out / "webcam_counts.csv"
    seen, prev_hashes = set(), {}
    if counts_path.exists():
        prev = pd.read_csv(counts_path, dtype=str)
        seen = set(zip(prev["camera"], prev["image_ts"]))
        # image content per camera, to spot feeds whose timestamp updates
        # but whose picture is frozen (e.g. a summer frame served in winter)
        for cam_id, g in prev.groupby("camera"):
            prev_hashes[cam_id] = set(g["image_sha1"].dropna())
    found = resolve_skiutah(_get(SKIUTAH_URL).decode("utf-8", "ignore"))
    # where each camera's current still lives, for the dashboard's map
    latest = {}
    for cam in load_cams():
        hit = match_cam(cam, found)
        if hit:
            latest[f"{cam['resort']}:{cam['title']}"] = {"url": f"{hit['url']}?_ts={hit['ts']}", "ts": hit["ts"],
                                                         "source_title": hit["title"]}
    (out / "cams_latest.json").write_text(__import__("json").dumps(latest, indent=1) + "\n")
    detector = detector or Detector()
    rows = []
    for cam in load_cams():
        hit = match_cam(cam, found)
        cam_id = f"{cam['resort']}:{cam['title']}"
        if not hit:
            print(f"  ! not on page: {cam_id}")
            continue
        if (cam_id, hit["ts"]) in seen:
            continue  # image hasn't refreshed since last run
        try:
            raw = _get(f"{hit['url']}?_ts={hit['ts']}")
            img = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception as e:  # noqa: BLE001
            print(f"  ! fetch failed {cam_id}: {e}")
            continue
        q = image_quality(img)
        sha = hashlib.sha1(raw).hexdigest()[:12]
        frozen = sha in prev_hashes.get(cam_id, set())
        usable = q["brightness"] >= 45 and q["sharpness"] >= 25 and not frozen
        c = detector.count(img, cam.get("roi")) if usable else {"people": "", "vehicles": ""}
        image_time = dt.datetime.strptime(hit["ts"], "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
        rows.append({
            "collected_at": now.isoformat(), "image_time": image_time.isoformat(), "image_ts": hit["ts"],
            "camera": cam_id, "resort": cam["resort"], "kind": cam["kind"], "usable": int(usable),
            **q, **c, "image_sha1": sha, "frozen": int(frozen),
            "lift_open": lift_status.is_open(lift_rows, cam["resort"], cam.get("lift")),
        })
        if verbose:
            flag = "ok " if usable else ("frozen" if frozen else "bad")
            print(f"  {cam_id:<55} {flag} people={c['people']} vehicles={c['vehicles']}")
    n = _append(counts_path, rows, ["camera", "image_ts"])
    print(f"webcam_counts: +{n} rows")


# --------------------------------------------------------------- summarize
def summarize(data_dir: str | Path, start_hour: float = 9.0, end_hour: float = 12.5) -> pd.DataFrame:
    """Daily calibration observations from collected data: the mean count per
    camera over the late-morning window (crowds have arrived, before the
    afternoon exodus), plus the peak net vehicles in each Cottonwood canyon."""
    d = Path(data_dir)
    obs = []
    wc = d / "webcam_counts.csv"
    if wc.exists():
        w = pd.read_csv(wc)
        w = w[w["usable"] == 1].copy()
        if "lift_open" in w:   # a "line" at a closed lift is just people walking past
            w = w[~((w["kind"] == "lift_line") & (pd.to_numeric(w["lift_open"], errors="coerce") == 0))]
        t = pd.to_datetime(w["image_time"], utc=True).dt.tz_convert(MT)
        w["date"] = t.dt.tz_localize(None).dt.normalize()
        hr = t.dt.hour + t.dt.minute / 60
        w = w[(hr >= start_hour) & (hr <= end_hour)]
        w["value"] = np.where(w["kind"].isin(["parking", "road"]), w["vehicles"], w["people"])
        g = w.groupby(["date", "camera", "resort"]).agg(value=("value", "mean"), n=("value", "size")).reset_index()
        g = g[g["n"] >= 3]
        obs.append(pd.DataFrame({
            "date": g["date"], "resort": g["resort"],
            "kind": np.where(g["resort"].str.isupper(), "canyon_vehicles", "webcam_count"),
            "value": g["value"], "weight": 0.5, "source": "cam_" + g["camera"].str.lower().str.replace(r"[^a-z0-9]+", "_", regex=True),
            "notes": "webcam detections, mean 9:00-12:30",
        }))
    cl = d / "canyon_live.csv"
    if cl.exists():
        c = pd.read_csv(cl)
        t = pd.to_datetime(c["observed_at"], utc=True).dt.tz_convert(MT)
        c["date"] = t.dt.tz_localize(None).dt.normalize()
        hr = t.dt.hour + t.dt.minute / 60
        c = c[(hr >= 8) & (hr <= 14)]
        g = c.groupby(["date", "canyon"]).agg(value=("net_vehicles_in_canyon", "max"), n=("canyon", "size")).reset_index()
        g = g[g["n"] >= 4]
        obs.append(pd.DataFrame({
            "date": g["date"], "resort": g["canyon"], "kind": "canyon_vehicles", "value": g["value"],
            "weight": 1.0, "source": "udot_live_" + g["canyon"], "notes": "UDOT peak net vehicles in canyon 8-14h",
        }))
    return pd.concat(obs, ignore_index=True) if obs else pd.DataFrame()
