#!/usr/bin/env python3
"""Build school_zone_cameras.json from a City of Phoenix Photo Safety schedule.

The city publishes the rotating school-zone schedule as a PDF listing, per
council district, seven schools and the week each is enforced. The PDF gives
an intersection in parentheses -- NOT coordinates -- so the intersections must
be geocoded before the app can use them.

Usage
-----
    python3 scripts/build_school_zones.py --report
        Print the parsed schedule and the exact geocoder queries. No network,
        no writes. Use this to eyeball the transcription against the PDF.

    python3 scripts/build_school_zones.py --geocode
        Geocode every entry, then (only if ALL succeed) archive the current
        school_zone_cameras.json per archive/school_zone_cameras/README.md,
        write the new active file, and update the archive manifest.

Run --geocode from a machine with normal internet access. The session
container this was written in blocks the geocoder at the network policy.

Why it refuses partial output
-----------------------------
school_zone_cameras.json is fetched live by the shipped app. An entry with a
wrong or missing coordinate is worse than no entry at all: it either alerts a
driver in the wrong place or stays silent at a real camera. So a single
geocode failure aborts the whole write and reports which rows need a manual
coordinate.
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTIVE_FILE = os.path.join(REPO_ROOT, "school_zone_cameras.json")
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive", "school_zone_cameras")
MANIFEST = os.path.join(ARCHIVE_DIR, "manifest.json")

# Contact string for the geocoder's usage policy. Nominatim requires a real
# identifier; anonymous bulk requests get blocked.
USER_AGENT = "SpeedShield-CameraData/1.0 (liveasalion@gmail.com)"
GEOCODER = "https://nominatim.openstreetmap.org/search"
REQUEST_INTERVAL_SECONDS = 1.1  # Nominatim policy: max 1 request/second.

# Sanity box for the Phoenix metro. Anything outside is a bad geocode, not a
# camera -- "31st Ave" alone matches streets in a dozen states.
LAT_MIN, LAT_MAX = 33.20, 33.98
LON_MIN, LON_MAX = -112.50, -111.80

# School zones are zones, not directional cameras, and the city schedule does
# not state an enforcement direction. -1 is the app's omnidirectional sentinel
# (proximity_service: `if (cam.directionDeg < 0) headingOk = true`).
DIRECTION_DEG = -1

# The seven enforcement weeks, in the order the schedule lists them. Every
# district follows the same calendar.
WEEKS = [
    ("2026-08-17", "2026-08-21"),
    ("2026-08-24", "2026-08-28"),
    ("2026-08-31", "2026-09-04"),
    ("2026-09-07", "2026-09-11"),
    ("2026-09-14", "2026-09-18"),
    ("2026-09-21", "2026-09-25"),
    ("2026-09-28", "2026-10-02"),
]

SCHEDULE_SOURCE = "Photo Safety Program School Schedule 8/17/26 to 10/2/26"

# Transcribed from the source PDF, district by district, in listed order.
# (school name, location text exactly as printed, geocoder query)
# A query of None means the PDF did not give a usable intersection -- those
# rows must be resolved by hand before this file can be built.
SCHEDULE = {
    1: [
        ("Desert Sage Elementary", "Alameda Rd / 40th Ln", "Alameda Rd & 40th Ln"),
        ("Paseo Hills School", "31st Ave / Louise Dr", "31st Ave & Louise Dr"),
        ("Valley Academy", "Rose Garden Ln", None),
        ("Mountain Shadows Elementary", "Oraibi Dr / 47th Ave", "Oraibi Dr & 47th Ave"),
        ("Sunrise Elementary", "Campo Bello Dr / 32nd Ave", "Campo Bello Dr & 32nd Ave"),
        ("Imagine Bell Canyon", "27th Ave / Vila Maria Dr", "27th Ave & Vila Maria Dr"),
        ("Village Meadows Elementary", "Morningside Dr / 20th Dr", "Morningside Dr & 20th Dr"),
    ],
    2: [
        ("Boulder Creek Elementary", "22nd St / Cashman Dr", "22nd St & Cashman Dr"),
        ("Wildfire Elementary", "Cashman Dr / 39th Way", "Cashman Dr & 39th Way"),
        ("Explorer Middle School", "40th St / Rough Rider Rd", "40th St & Rough Rider Rd"),
        ("Fireside Elementary", "Lone Cactus Dr / north of Sinclair St", "Lone Cactus Dr & Sinclair St"),
        ("Copper Canyon Elementary", "56th St / Muriel Dr", "56th St & Muriel Dr"),
        ("North Ranch Elementary", "60th St / Kings Ave", "60th St & Kings Ave"),
        ("Liberty Elementary", "Acoma Dr / 50th St", "Acoma Dr & 50th St"),
    ],
    3: [
        ("Larkspur Elementary", "24th St / Larkspur Dr", "24th St & Larkspur Dr"),
        ("Hidden Hills Elementary", "Sharon Dr / 19th St", "Sharon Dr & 19th St"),
        ("Greenway Middle School", "Nisbet Rd / 30th Pl", "Nisbet Rd & 30th Pl"),
        ("Scottsdale Country Day School", "56th St / Shea Blvd", "56th St & Shea Blvd"),
        ("Mercury Mine Elementary", "26th St / Turquoise Dr", "26th St & Turquoise Dr"),
        ("Sunnyslope School", "Mountain View Rd / 2nd Way", "Mountain View Rd & 2nd Way"),
        ("Mountain View Elementary", "9th Ave / Cheryl Dr", "9th Ave & Cheryl Dr"),
    ],
    4: [
        ("Morris K Udall Middle School", "Roosevelt St / 37th Ave", "Roosevelt St & 37th Ave"),
        ("JB Sutton Elementary School", "31st Ave / south of Moreland St", "31st Ave & Moreland St"),
        ("Isaac Middle School", "34th Ave / Granada Rd", "34th Ave & Granada Rd"),
        ("Mitchell School", "41st Ave / Granada Rd", "41st Ave & Granada Rd"),
        ("Glenn L Downs Academy", "47th Ave / Whitton Ave", "47th Ave & Whitton Ave"),
        ("Granada Elementary", "31st Ave / Hazelwood St", "31st Ave & Hazelwood St"),
        ("Loma Linda School", "20th St / Clarendon Ave", "20th St & Clarendon Ave"),
    ],
    5: [
        ("Amberlea Neighborhood School", "Virginia Ave / 85th Ave", "Virginia Ave & 85th Ave"),
        ("Desert Horizon Elementary", "87th Ave / Virginia Ave", "87th Ave & Virginia Ave"),
        ("Starlight Park Elementary", "Osborn Rd / 80th Ave", "Osborn Rd & 80th Ave"),
        ("John F Long Elementary", "Campbell Ave / Meadowbrook Ave", "Campbell Ave & Meadowbrook Ave"),
        ("James W Rice Elementary", "47th Ave / Hazelwood St", "47th Ave & Hazelwood St"),
        ("Sevilla Elementary", "Missouri Ave / 38th Ave", "Missouri Ave & 38th Ave"),
        ("Maryland School", "21st Ave / north of Maryland Ave", "21st Ave & Maryland Ave"),
    ],
    6: [
        ("Kyrene Altadena Middle", "Desert Foothills Pkwy / Desert Broom Way", "Desert Foothills Pkwy & Desert Broom Way"),
        ("Kyrene Akimel A-Al Middle School", "Liberty Ln", None),
        ("Kyrene de los Lagos Elementary", "Lakewood Pkwy W / 36th St", "Lakewood Pkwy W & 36th St"),
        ("Kyrene del Milenio Elementary", "Frye Rd / 46th St", "Frye Rd & 46th St"),
        ("Horizon Honors School", "Frye Rd / east of 48th St", "Frye Rd & 48th St"),
        ("Kyrene de la Esperanza Elementary", "Ranch Circle E / 41st Pl", "Ranch Circle E & 41st Pl"),
        ("Tavan Elementary", "Osborn Rd / 46th St", "Osborn Rd & 46th St"),
    ],
    7: [
        ("Kenilworth Elementary", "5th Ave / Culver St", "5th Ave & Culver St"),
        ("William R Sullivan Elementary", "31st Ave / north of Washington St", "31st Ave & Washington St"),
        ("Jack L Kuban Elementary", "31st Ave / Sherman St", "31st Ave & Sherman St"),
        ("Sunridge Elementary", "Roosevelt St / 62nd Dr", "Roosevelt St & 62nd Dr"),
        ("Charles W Harris Elementary", "55th Ave / Hubble St", "55th Ave & Hubble St"),
        ("Palm Lane Elementary", "Encanto Blvd / 64th Dr", "Encanto Blvd & 64th Dr"),
        ("Peralta Elementary", "Encanto Blvd / 71st Ln", "Encanto Blvd & 71st Ln"),
    ],
    8: [
        ("Irene Lopez Academy", "12th St / Wier Ave", "12th St & Wier Ave"),
        ("SABIS International School", "Roeser Rd / 20th St", "Roeser Rd & 20th St"),
        ("TG Barr Academy", "Vineyard Rd / 21st Pl", "Vineyard Rd & 21st Pl"),
        ("Brunson Lee Elementary", "48th St / north of Culver St", "48th St & Culver St"),
        ("Griffith Elementary", "Palm Ln / 46th St", "Palm Ln & 46th St"),
        ("Papago Elementary", "36th St / Monte Vista Rd", "36th St & Monte Vista Rd"),
        ("Gateway Elementary", "35th St / north of Belleview St", "35th St & Belleview St"),
    ],
}


def build_rows():
    """Flatten SCHEDULE into one row per school, dated by its week slot."""
    rows = []
    for district in sorted(SCHEDULE):
        entries = SCHEDULE[district]
        if len(entries) != len(WEEKS):
            raise SystemExit(
                f"District {district}: {len(entries)} schools but {len(WEEKS)} weeks defined"
            )
        for week_index, (school, location_text, query) in enumerate(entries):
            active_from, active_until = WEEKS[week_index]
            rows.append({
                "school_name": school,
                "district": district,
                "location_text": location_text,
                "query": f"{query}, Phoenix, AZ" if query else None,
                "active_from": active_from,
                "active_until": active_until,
            })
    return rows


def geocode(query):
    """Return (lat, lon) for a query, or None if nothing usable came back."""
    url = f"{GEOCODER}?" + urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "limit": 5,
        "countrycodes": "us",
    })
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        results = json.load(response)
    for result in results:
        lat, lon = float(result["lat"]), float(result["lon"])
        if LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX:
            return lat, lon
    return None


def sha256_of(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def archive_active_file(now):
    """Preserve the outgoing active file and record it, per the archive README."""
    if not os.path.exists(ACTIVE_FILE):
        return
    stamp = now.strftime("%Y-%m-%dT%H-%M-%SZ")
    name = f"school_zone_cameras__through_{stamp}.json"
    destination = os.path.join(ARCHIVE_DIR, name)
    shutil.copy2(ACTIVE_FILE, destination)

    entries = []
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as handle:
            entries = json.load(handle)
    entries.append({
        "file": f"archive/school_zone_cameras/{name}",
        "sha256": sha256_of(destination),
        "valid_from": None,
        "valid_to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "archived_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_active_file": "school_zone_cameras.json",
        "superseded_by_schedule": SCHEDULE_SOURCE,
    })
    with open(MANIFEST, "w") as handle:
        json.dump(entries, handle, indent=2)
        handle.write("\n")
    print(f"archived  {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true",
                        help="print the parsed schedule and queries; no network, no writes")
    parser.add_argument("--geocode", action="store_true",
                        help="geocode, then write the active file if every row resolved")
    args = parser.parse_args()
    if not (args.report or args.geocode):
        parser.error("choose --report or --geocode")

    rows = build_rows()
    print(f"{len(rows)} entries across {len(SCHEDULE)} districts "
          f"({rows[0]['active_from']} through {rows[-1]['active_until']})\n")

    if args.report:
        for row in rows:
            marker = "  " if row["query"] else "!!"
            print(f"{marker} d{row['district']} {row['active_from']}..{row['active_until']}  "
                  f"{row['school_name']}  <-  {row['query'] or 'NO INTERSECTION IN PDF'}")
        missing = [r for r in rows if not r["query"]]
        if missing:
            print(f"\n{len(missing)} row(s) need a hand-supplied intersection before --geocode:")
            for row in missing:
                print(f"  - {row['school_name']} (district {row['district']}): "
                      f"PDF gives only \"{row['location_text']}\"")
        return

    unresolved = [r for r in rows if not r["query"]]
    if unresolved:
        print("Refusing to geocode: these rows have no intersection in the PDF.")
        for row in unresolved:
            print(f"  - {row['school_name']} (district {row['district']}): "
                  f"\"{row['location_text']}\"")
        print("\nAdd the missing cross street to SCHEDULE in this file, then re-run.")
        sys.exit(1)

    failures = []
    for index, row in enumerate(rows, start=1):
        if index > 1:
            time.sleep(REQUEST_INTERVAL_SECONDS)
        try:
            found = geocode(row["query"])
        except Exception as error:  # network/HTTP/JSON -- all mean "no coordinate"
            found = None
            print(f"[{index:2}/{len(rows)}] ERROR {row['school_name']}: {error}")
        if found:
            row["latitude"], row["longitude"] = found
            print(f"[{index:2}/{len(rows)}] ok    {row['school_name']:38} {found[0]:.6f},{found[1]:.6f}")
        else:
            failures.append(row)
            print(f"[{index:2}/{len(rows)}] FAIL  {row['school_name']:38} {row['query']}")

    if failures:
        print(f"\n{len(failures)} of {len(rows)} rows did not resolve to a Phoenix-area point.")
        print("Nothing was written -- a partial file would ship wrong alert locations.")
        print("Fix these by supplying coordinates by hand, then re-run:")
        for row in failures:
            print(f"  - {row['school_name']} (district {row['district']}): {row['query']}")
        sys.exit(1)

    cameras = [{
        "name": f"School Zone — {row['school_name']}",
        "latitude": round(row["latitude"], 7),
        "longitude": round(row["longitude"], 7),
        "direction_deg": DIRECTION_DEG,
        "type": "school_zone",
        "school_name": row["school_name"],
        "district": row["district"],
        "active_from": row["active_from"],
        "active_until": row["active_until"],
    } for row in rows]

    now = datetime.datetime.now(datetime.timezone.utc)
    archive_active_file(now)
    with open(ACTIVE_FILE, "w") as handle:
        json.dump(cameras, handle, indent=2)
        handle.write("\n")
    print(f"\nwrote     school_zone_cameras.json  ({len(cameras)} entries)")
    print("Review the diff, then commit and push -- the app fetches this file live.")


if __name__ == "__main__":
    main()
