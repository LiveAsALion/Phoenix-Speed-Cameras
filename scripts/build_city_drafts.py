#!/usr/bin/env python3
"""Geocode the per-city camera rosters in drafts/city_rosters.json into
review-ready draft files. Nothing here touches camera_data.json -- the app
only ever sees what is pasted into MANUAL_CAMERAS in update_cameras.py after
the tester has verified the coordinates.

Usage
-----
    python3 scripts/build_city_drafts.py --report
        Print every roster row and the query it will be looked up by. Offline.

    python3 scripts/build_city_drafts.py --geocode [--city chandler]
        Look the rows up (Nominatim, 1 request/s) and write
        drafts/<city>.geocoded.json, one file per city, in the app's entry
        format plus underscore-prefixed review fields. A city is written
        only if EVERY row resolved, passed the metro bounding box, was not a
        city/neighbourhood centroid, and shares no point with another row --
        the same guardrails as the school-zone builder. Run it from a machine
        with normal internet access; the session container is network-blocked.

Rows that resolve by a fallback (school name, street address) rather than
the named intersection are flagged in the output: the tester should pin-drop
those before they go live.
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_school_zones import (  # noqa: E402
    _nominatim, REQUEST_INTERVAL_SECONDS, overpass_intersection, osm_street_name,
)

REPO_ROOT = os.path.dirname(HERE)
ROSTERS = os.path.join(REPO_ROOT, "drafts", "city_rosters.json")
OUT_DIR = os.path.join(REPO_ROOT, "drafts")

# Wider than the school-zone box: Mesa's eastern intersections (Ellsworth,
# Crismon, Signal Butte) sit past -111.80.
LAT_MIN, LAT_MAX = 33.15, 33.95
LON_MIN, LON_MAX = -112.55, -111.55

CITY_BBOX = f"{LAT_MIN},{LON_MIN},{LAT_MAX},{LON_MAX}"

_CENTROID_TYPES = {
    "city", "town", "village", "administrative", "municipality",
    "county", "state", "postcode", "suburb", "neighbourhood",
}


def _acceptable(result):
    lat, lon = float(result["lat"]), float(result["lon"])
    if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
        return None
    if result.get("type") in _CENTROID_TYPES or result.get("class") == "boundary":
        return None
    return lat, lon


def load_rosters():
    with open(ROSTERS) as handle:
        data = json.load(handle)
    return {k: v for k, v in data.items()
            if isinstance(v, dict) and "entries" in v}


def geocode(query):
    """Resolve an 'A & B, City, AZ' query to (lat, lon, how) or None.

    Overpass first (the actual OSM node where the two streets meet), then
    Nominatim phrasings. If the streets meet in more than one place the
    candidates are printed and the row is left unresolved: put the right
    one into the roster as "lat"/"lon" and re-run.
    """
    if " & " in query:
        a, b = query.split(",")[0].split(" & ", 1)
        try:
            clusters = overpass_intersection(osm_street_name(a.strip()),
                                             osm_street_name(b.strip()), bbox=CITY_BBOX)
        except Exception as error:
            print(f"      overpass error: {error}")
            clusters = []
        if len(clusters) == 1:
            return clusters[0][0], clusters[0][1], "osm-intersection"
        if len(clusters) > 1:
            print(f"      ambiguous: {len(clusters)} separate intersections: "
                  + "; ".join(f"{c[0]:.5f},{c[1]:.5f}" for c in clusters)
                  + "  -> add lat/lon to the roster row")
            return None
    attempts = [(query, "as-written")]
    if " & " in query:
        attempts.append((query.replace(" & ", " and "), "and-phrasing"))
    for text, how in attempts:
        try:
            results = _nominatim(text)
        except Exception as error:  # network hiccup: report, keep going
            print(f"      lookup error ({how}): {error}")
            results = []
        for result in results:
            found = _acceptable(result)
            if found:
                return found[0], found[1], how
        time.sleep(REQUEST_INTERVAL_SECONDS)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--geocode", action="store_true")
    parser.add_argument("--city", help="only this roster key (e.g. chandler)")
    args = parser.parse_args()
    if not (args.report or args.geocode):
        parser.error("choose --report or --geocode")

    rosters = load_rosters()
    if args.city:
        if args.city not in rosters:
            parser.error(f"unknown city {args.city!r}; have {sorted(rosters)}")
        rosters = {args.city: rosters[args.city]}

    if args.report:
        for key, city in rosters.items():
            print(f"\n[{key}] {city['status']}")
            for row in city["entries"]:
                print(f"   {row['name']:48} <- {row['query']}   ({row.get('confidence', '')})")
            if city.get("missing"):
                print(f"   !! {city['missing']}")
        return

    for key, city in rosters.items():
        label = city["city_label"]
        default_type = city.get("default_type", "red_light_speed")
        print(f"\n[{key}] geocoding {len(city['entries'])} rows")
        out, failures, seen, dupes = [], [], {}, []
        for index, row in enumerate(city["entries"], start=1):
            if index > 1:
                time.sleep(REQUEST_INTERVAL_SECONDS)
            if "lat" in row and "lon" in row:
                found = (float(row["lat"]), float(row["lon"]), "manual")
            else:
                found = geocode(row["query"])
            if not found:
                failures.append(row["name"])
                print(f"  [{index:2}] FAIL  {row['name']}")
                continue
            lat, lon, how = found
            point = (round(lat, 5), round(lon, 5))
            if point in seen:
                dupes.append((seen[point], row["name"], point))
            else:
                seen[point] = row["name"]
            by_intersection = how in ("osm-intersection", "manual") or (
                " & " in row["query"] and how in ("as-written", "and-phrasing"))
            flag = "" if by_intersection else "   <- resolved by NAME/ADDRESS, pin-drop it"
            print(f"  [{index:2}] ok    {row['name']:48} {lat:.6f},{lon:.6f} [{how}]{flag}")
            out.append({
                "name": f"{row['name']}: {label}",
                "latitude": round(lat, 7),
                "longitude": round(lon, 7),
                "direction_deg": -1,
                "type": row.get("type", default_type),
                "_approaches": row.get("approaches", []),
                "_confidence": row.get("confidence", ""),
                "_resolved_by": "intersection" if by_intersection else "name-or-address",
                "_query": row["query"],
            })

        if failures or dupes:
            print(f"  NOT written: {len(failures)} failure(s), {len(dupes)} duplicate point(s)")
            for name in failures:
                print(f"    - failed: {name}")
            for first, second, point in dupes:
                print(f"    - same point: {first} == {second} at {point}")
            print("  Fix the query in drafts/city_rosters.json and re-run with --city "
                  f"{key}")
            continue

        path = os.path.join(OUT_DIR, f"{key}.geocoded.json")
        with open(path, "w") as handle:
            json.dump(out, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        flagged = [o["name"] for o in out if o["_resolved_by"] != "intersection"]
        print(f"  wrote {os.path.relpath(path, REPO_ROOT)} ({len(out)} entries)")
        if flagged:
            print(f"  pin-drop before go-live ({len(flagged)}): " + "; ".join(flagged))


if __name__ == "__main__":
    main()
