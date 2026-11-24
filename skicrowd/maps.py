"""Resort maps from OpenStreetMap (ODbL): lifts, runs and ski-area outlines.

    python -m skicrowd.maps fetch --out collector-data/maps   # raw Overpass JSON (run on GitHub)
    python -m skicrowd.maps build --raw collector-data/maps    # -> skicrowd/maps/<resort>.json

The official trail maps are the resorts' copyrighted artwork, so the
dashboard draws its own map from OpenStreetMap data instead (with
attribution), and links to the official map.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Public Overpass servers, tried in turn (the main one is often overloaded)
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
# (south, west, north, east) per region; resorts sharing a canyon share a box
REGIONS = {
    "BCC": (40.575, -111.640, 40.645, -111.555),
    "LCC": (40.545, -111.700, 40.605, -111.600),
    "DV": (40.585, -111.545, 40.660, -111.460),
    "SB": (41.165, -111.900, 41.245, -111.820),
}
REGION_RESORTS = {"BCC": ["solitude", "brighton"], "LCC": ["alta", "snowbird"],
                  "DV": ["deer_valley"], "SB": ["snowbasin"]}


def _query(bbox: tuple[float, float, float, float]) -> str:
    b = ",".join(str(x) for x in bbox)
    return (f"[out:json][timeout:180];("
            f'nwr["landuse"="winter_sports"]({b});'
            f'way["aerialway"]({b});'
            f'way["piste:type"="downhill"]({b});'
            f'relation["piste:type"="downhill"]({b});'
            f'nwr["amenity"="parking"]({b});'
            f");out geom tags;")


def fetch(out: Path, regions=tuple(REGIONS)) -> None:
    out.mkdir(parents=True, exist_ok=True)
    failed = []
    for name in regions:
        body = urllib.parse.urlencode({"data": _query(REGIONS[name])}).encode()
        for attempt in range(2 * len(OVERPASS)):
            url = OVERPASS[attempt % len(OVERPASS)]
            req = urllib.request.Request(url, data=body,
                                         headers={"User-Agent": "skicrowd/1.0 (personal ski crowd dashboard)"})
            try:
                data = urllib.request.urlopen(req, timeout=200).read()
                if not json.loads(data).get("elements"):
                    raise ValueError("empty result")
                (out / f"raw_{name}.json").write_bytes(data)
                print(f"{name}: {len(data):,} bytes from {url}")
                break
            except Exception as e:  # noqa: BLE001
                print(f"{name}: {url} failed: {e}")
                time.sleep(10)
        else:
            failed.append(name)
        time.sleep(5)        # be gentle with the public servers
    if failed:
        raise SystemExit(f"could not fetch: {', '.join(failed)}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="skicrowd.maps")
    p.add_argument("action", choices=["fetch", "build"])
    p.add_argument("--out", default="collector-data/maps")
    p.add_argument("--raw", default="collector-data/maps")
    a = p.parse_args(argv)
    if a.action == "fetch":
        fetch(Path(a.out))
    else:
        from .resort_maps import build_all
        build_all(Path(a.raw))


if __name__ == "__main__":
    main()
