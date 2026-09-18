#!/usr/bin/env python3
"""Measure the local road bearing at each DIRECTIONAL camera site in
drafts/city_rosters.json from OpenStreetMap way geometry, so a directional
entry's direction_deg is a measurement, not a compass label.

Why: the app uses direction_deg twice -- as a 45-degree heading filter and
as the corridor-gate axis (75 m cross-track). On a diagonal or curved road
the city's "E/B" label can be 30 degrees off the real bearing, which puts an
approaching driver 300 m off the axis at the 600 m ring and silences both
tiers. Straight section-line roads come out at 0/90/180/270 anyway; this
script is what proves it.

Usage
-----
    python3 scripts/measure_road_bearings.py --city scottsdale --city paradise_valley

For every roster row carrying a "directional" map ({"S/B Scottsdale Rd": 180,
...}) it fetches the named road's ways within 600 m of the pinned point,
keeps the points on the APPROACH side (a southbound driver comes from the
north), fits a line through them and the camera point, and prints the
heading a driver has while approaching, plus how much the road deviates
from that line (curvature) and how far the roster's current value is from
the measurement. Read-only; writes nothing. The roster values are then set
by hand from the printout.
"""

import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_school_zones import _overpass, osm_street_name, _haversine_m  # noqa: E402

REPO_ROOT = os.path.dirname(HERE)
ROSTERS = os.path.join(REPO_ROOT, "drafts", "city_rosters.json")

SEARCH_M = 600      # how far along the road to look
NEAR_M = 250        # "near" band, for the curvature check
APPROACH_FROM = {"N/B": "south", "S/B": "north", "E/B": "west", "W/B": "east"}


def local_xy(lat0, lon0, lat, lon):
    """Metres east/north of the camera point (flat-earth; fine at 600 m)."""
    x = (lon - lon0) * 111320.0 * math.cos(math.radians(lat0))
    y = (lat - lat0) * 110540.0
    return x, y


def fit_heading(points, toward=(0.0, 0.0)):
    """Principal axis through the points AND the camera point, oriented so
    it points from the points' centroid toward the camera. Returns
    (heading_deg, max_deviation_m)."""
    pts = list(points) + [toward]
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    theta = 0.5 * math.atan2(2 * sxy, sxx - syy)   # axis angle from +x (east)
    ux, uy = math.cos(theta), math.sin(theta)
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    # orient from the approach centroid toward the camera
    if (toward[0] - cx) * ux + (toward[1] - cy) * uy < 0:
        ux, uy = -ux, -uy
    heading = (math.degrees(math.atan2(ux, uy)) + 360.0) % 360.0   # compass
    max_dev = max(abs((p[0] - mx) * uy - (p[1] - my) * ux) for p in pts)
    return heading, max_dev


def road_points(road, lat, lon, radius=SEARCH_M):
    """All geometry points of highway ways named `road` within `radius`."""
    name = osm_street_name(road)
    regex = f'^(North |South |East |West |N |S |E |W )?{name}$'
    query = ('[out:json][timeout:25];'
             f'way["highway"]["name"~"{regex}",i](around:{radius},{lat},{lon});'
             'out geom;')
    last = None
    for attempt in range(3):
        try:
            elements = _overpass(query)
            pts = []
            for e in elements:
                for g in e.get("geometry", []) or []:
                    pts.append((g["lat"], g["lon"]))
            return pts, None
        except Exception as error:   # timeouts: back off and retry
            last = error
            time.sleep(5 * (attempt + 1))
    return [], last


def on_approach_side(token, x, y):
    side = APPROACH_FROM[token]
    return {"north": y > 15, "south": y < -15, "west": x < -15, "east": x > 15}[side]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", action="append", required=True)
    parser.add_argument("--only", help="only rows whose name contains this text")
    args = parser.parse_args()
    with open(ROSTERS) as handle:
        rosters = json.load(handle)
    print("label | points(approach side) | heading ALL | heading NEAR(<250m) | "
          "heading FAR(250-600m) | max deviation m | roster value | delta")
    for key in args.city:
        city = rosters[key]
        print(f"\n[{key}]")
        for row in city["entries"]:
            directional = row.get("directional")
            if not directional:
                continue
            if args.only and args.only.lower() not in row["name"].lower():
                continue
            lat, lon = float(row["lat"]), float(row["lon"])
            for label, current in directional.items():
                token, road = label.split(" ", 1)
                # A straight OSM way can have no node for a kilometre, so a
                # sparse approach side widens the search until it has enough
                # points (the line fit still runs through the camera point).
                side, xy, radius, error = [], [], SEARCH_M, None
                for radius in (SEARCH_M, 1200, 2000, 3000):
                    pts, error = road_points(road, lat, lon, radius)
                    if error or not pts:
                        break
                    xy = [local_xy(lat, lon, plat, plon) for plat, plon in pts]
                    side = [p for p in xy if on_approach_side(token, *p)
                            and math.hypot(*p) <= radius]
                    if len(side) >= 2:
                        break
                    time.sleep(1.5)
                if error or not xy:
                    print(f"  {row['name']} [{label}]: NO GEOMETRY ({error or 'no ways matched'})")
                    time.sleep(1.5)
                    continue
                near = [p for p in side if math.hypot(*p) <= NEAR_M]
                far = [p for p in side if math.hypot(*p) > NEAR_M]
                if len(side) < 2:
                    print(f"  {row['name']} [{label}]: only {len(side)} approach-side points "
                          f"of {len(xy)} even at {radius} m -- pin by hand")
                    time.sleep(1.5)
                    continue
                widened = f" (search widened to {radius} m)" if radius != SEARCH_M else ""
                h_all, dev = fit_heading(side)
                h_near = fit_heading(near)[0] if len(near) >= 2 else float('nan')
                h_far = fit_heading(far)[0] if len(far) >= 2 else float('nan')
                delta = "" if current is None else f"{((h_all - current + 180) % 360) - 180:+.1f}"
                print(f"  {row['name']} [{label}] | {len(side)} | {h_all:6.1f} | "
                      f"{h_near:6.1f} | {h_far:6.1f} | {dev:5.1f} | {current} | {delta}{widened}")
                time.sleep(1.5)


if __name__ == "__main__":
    main()
