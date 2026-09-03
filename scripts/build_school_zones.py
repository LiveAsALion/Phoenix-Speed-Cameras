#!/usr/bin/env python3
"""Build school_zone_cameras.json from a City of Phoenix Photo Safety schedule.

The city publishes the rotating school-zone schedule as a PDF listing, per
council district, seven schools and the week each is enforced. It gives an
intersection in parentheses -- NOT coordinates -- so the intersections must be
geocoded before the app can use them.

Usage
-----
    python3 scripts/build_school_zones.py --report
        Print the parsed schedule and what each row will be looked up by.
        No network, no writes. Use it to eyeball the transcription.

    python3 scripts/build_school_zones.py --geocode
        Look every row up, then -- only if all 56 pass every check -- archive
        the current school_zone_cameras.json per
        archive/school_zone_cameras/README.md, write the new active file, and
        update the archive manifest.

Run --geocode from a machine with normal internet access; the session
container this was written in has the geocoder blocked at the network policy.

Why it refuses to write a partial file
--------------------------------------
school_zone_cameras.json is fetched live by the shipped app, so a wrong
coordinate is worse than a missing one: it either alerts a driver somewhere no
camera exists or stays silent where one does. Three checks must all pass
before anything is written:

  * every row resolved to a point inside the Phoenix metro box;
  * no result was a city/administrative centroid (the failure mode where a
    geocoder cannot find your street and hands back downtown Phoenix -- it
    passes a bounding-box test, which is why the type is checked instead);
  * no two schools share a point (the other silent-fallback signature).

Rows that resolve by school name rather than by the published intersection are
listed at the end for a manual look: the camera sits at the intersection, and
a school campus can be a few hundred meters from it. Two rows always take
that path (Valley Academy, Kyrene Akimel A-Al -- the PDF prints one street
for each), so expect at least those two in the spot-check list.

Fixing a row
------------
Put the coordinate in MANUAL_COORDS, keyed by school name, and re-run. Manual
entries take priority over every lookup.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.error
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

# Hand-supplied coordinates, keyed by school name. These take priority over
# every lookup, so this is where a failed or wrong-looking row gets fixed:
# find the camera location in a map, copy the coordinates, add a line here,
# re-run. Two schools (Valley Academy, Kyrene Akimel A-Al) have only one
# street in the city's PDF; they resolve by school name and are flagged for
# a map check -- put a verified point here to pin them exactly.
MANUAL_COORDS = {
    # Campbell Ave meets Meadowbrook Ave twice; the school is at 4407 N 55th
    # Ave, so the eastern crossing (near 53rd Ave) is the one -- the other is
    # 3 km west at 71st Ave. Coordinate is that OSM intersection node.
    "John F Long Elementary": (33.502190, -112.175074),
}

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
                "cross_streets": tuple(query.split(" & ")) if query else None,
                "active_from": active_from,
                "active_until": active_until,
            })
    return rows


def _nominatim(query):
    """One raw lookup. Returns the list of results (may be empty)."""
    url = f"{GEOCODER}?" + urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "limit": 5,
        "countrycodes": "us",
        "addressdetails": 1,
    })
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


# Result classes that mean "I could not find your street, here is the city."
# A city-centroid fallback passes a bounding-box check silently, which would put
# a phantom school zone in downtown Phoenix -- the exact failure a bbox test
# cannot catch. Reject them by type instead.
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


def _nominatim_first(query):
    """First acceptable Nominatim hit for a query, as (lat, lon), or None."""
    try:
        results = _nominatim(query)
    except Exception as error:
        print(f"      nominatim error: {error}")
        results = []
    time.sleep(REQUEST_INTERVAL_SECONDS)
    for result in results:
        found = _acceptable(result)
        if found:
            return found
    return None


# ---------------------------------------------------------------------------
# Overpass (OpenStreetMap) lookups.
#
# Nominatim cannot resolve "A & B" intersections -- the first --geocode run
# hit 1 of 56 that way. Overpass can: it returns the node(s) shared by the two
# named ways, i.e. the actual intersection. OSM spells street names out with
# a directional prefix ("North 40th Lane"), so the city's short forms are
# expanded and matched EXACTLY against the five prefix variants (exact tag
# matches are indexed and fast; the earlier regex scan of every road in the
# metro was what produced the 504s). A case-insensitive regex is the fallback.
# OSM also maps most schools as named polygons, which gives a far better
# school location than Nominatim for tie-breaks and single-street rows.
# ---------------------------------------------------------------------------
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OVERPASS_BBOX = f"{LAT_MIN},{LON_MIN},{LAT_MAX},{LON_MAX}"  # south,west,north,east
OVERPASS_INTERVAL_SECONDS = 1.5
OVERPASS_ATTEMPTS = 2  # full passes over the endpoint list
CLUSTER_METERS = 300  # nodes closer than this are one intersection (divided roads)

_STREET_TYPES = {
    "rd": "Road", "ln": "Lane", "ave": "Avenue", "dr": "Drive", "st": "Street",
    "pl": "Place", "blvd": "Boulevard", "pkwy": "Parkway", "cir": "Circle",
    "ct": "Court", "trl": "Trail", "hwy": "Highway", "ter": "Terrace",
}
_DIRECTIONS = {"n": "North", "s": "South", "e": "East", "w": "West"}
_PREFIXES = ("North ", "South ", "East ", "West ", "")

# Alternate intersection spellings tried when the published one finds
# nothing in OSM. Keyed by school name.
ALT_QUERIES = {
    "Imagine Bell Canyon": ["27th Ave & Villa Maria Dr"],
}

# Words that carry no identity when matching a school's OSM polygon by name.
_GENERIC_SCHOOL_WORDS = {
    "elementary", "school", "middle", "academy", "junior", "high",
    "intermediate", "neighborhood", "international", "traditional", "stem",
    "performing", "arts", "dual", "language", "accelerated", "community",
    "entrepreneurial", "global", "primary", "charter", "campus", "prep",
    "preparatory", "of", "the", "and",
}


def osm_street_name(short):
    """'Lakewood Pkwy W' -> 'Lakewood Parkway West'; '40th Ln' -> '40th Lane'."""
    tokens = short.replace(".", "").split()
    out = []
    for index, token in enumerate(tokens):
        low = token.lower()
        if low in _STREET_TYPES:
            out.append(_STREET_TYPES[low])
        elif low in _DIRECTIONS and index == len(tokens) - 1 and len(tokens) > 1:
            out.append(_DIRECTIONS[low])
        else:
            out.append(token)
    return " ".join(out)


def _check_name(name):
    if not re.fullmatch(r"[A-Za-z0-9 '\-]+", name):
        raise ValueError(f"street name has characters the query cannot carry: {name!r}")
    return name


_endpoint_cursor = [0]          # rotate the starting server between calls
_endpoint_penalty = {}          # url -> epoch seconds until it is retried


def _overpass(query):
    """POST a query; rotate endpoints and retry. A 200 whose body carries a
    'remark' (Overpass reports its own timeouts that way, with an EMPTY
    element list) counts as a failure, not as 'nothing there'. A server that
    answers 429/504 is benched for two minutes so a throttled address (GitHub
    runners share theirs) does not burn every attempt on the same host."""
    data = urllib.parse.urlencode({"data": query}).encode()
    last_error = None
    count = len(OVERPASS_ENDPOINTS)
    for attempt in range(OVERPASS_ATTEMPTS):
        for offset in range(count):
            index = (_endpoint_cursor[0] + offset) % count
            url = OVERPASS_ENDPOINTS[index]
            if _endpoint_penalty.get(url, 0) > time.time():
                continue
            try:
                request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=45) as response:
                    body = json.load(response)
                remark = body.get("remark", "")
                if remark and ("timed out" in remark or "error" in remark.lower()):
                    raise RuntimeError(f"overpass remark: {remark[:120]}")
                _endpoint_cursor[0] = (index + 1) % count
                return body.get("elements", [])
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code in (429, 504):
                    _endpoint_penalty[url] = time.time() + 120
            except Exception as error:
                last_error = error
            time.sleep(2 + 3 * attempt)
    raise last_error


def _haversine_m(p, q):
    lat1, lon1, lat2, lon2 = map(math.radians, (p[0], p[1], q[0], q[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _cluster(points):
    """Greedy grouping: points within CLUSTER_METERS of a cluster centroid join
    it. Returns one centroid per distinct intersection."""
    clusters = []
    for point in points:
        for cluster in clusters:
            if _haversine_m(point, cluster["centroid"]) <= CLUSTER_METERS:
                cluster["points"].append(point)
                n = len(cluster["points"])
                cluster["centroid"] = (sum(p[0] for p in cluster["points"]) / n,
                                       sum(p[1] for p in cluster["points"]) / n)
                break
        else:
            clusters.append({"points": [point], "centroid": point})
    return [c["centroid"] for c in clusters]


def _way_set_exact(name, bbox, label):
    union = "".join(f'way["highway"]["name"="{prefix}{name}"]({bbox});' for prefix in _PREFIXES)
    return f"({union})->.{label};"


def _way_set_regex(name, bbox, label):
    return f'way["highway"]["name"~"^(North |South |East |West )?{name}$",i]({bbox})->.{label};'


def overpass_intersection(street_a, street_b, bbox=OVERPASS_BBOX):
    """Centroids of every distinct place where the two named streets meet.
    Exact (indexed) name match first; case-insensitive regex if that is empty."""
    _check_name(street_a)
    _check_name(street_b)
    for builder in (_way_set_exact, _way_set_regex):
        query = ('[out:json][timeout:60];' + builder(street_a, bbox, "a")
                 + builder(street_b, bbox, "b") + 'node(w.a)(w.b);out;')
        elements = _overpass(query)
        time.sleep(OVERPASS_INTERVAL_SECONDS)
        points = [(e["lat"], e["lon"]) for e in elements if e.get("type") == "node"]
        if points:
            return _cluster(points)
    return []


def overpass_nearest_street_node(street, point, max_m, bbox=OVERPASS_BBOX):
    """The node of the named street closest to `point`, if within max_m."""
    _check_name(street)
    for builder in (_way_set_exact, _way_set_regex):
        query = '[out:json][timeout:60];' + builder(street, bbox, "s") + 'node(w.s);out;'
        elements = _overpass(query)
        time.sleep(OVERPASS_INTERVAL_SECONDS)
        nodes = [(e["lat"], e["lon"]) for e in elements if e.get("type") == "node"]
        if nodes:
            best = min(nodes, key=lambda n: _haversine_m(n, point))
            return best if _haversine_m(best, point) <= max_m else None
    return None


def _school_regex(school_name):
    tokens = [t for t in re.split(r"[\s,]+", school_name.replace(".", ""))
              if len(t) >= 3 and t.lower() not in _GENERIC_SCHOOL_WORDS]
    if not tokens:
        tokens = school_name.split()[:1]
    return ".*".join(re.escape(t) for t in tokens)


def overpass_school_center(school_name, bbox=OVERPASS_BBOX):
    """Centre of the OSM school polygon/node whose name matches, or None."""
    query = ('[out:json][timeout:60];'
             f'nwr["amenity"="school"]["name"~"{_school_regex(school_name)}",i]({bbox});'
             'out center;')
    elements = _overpass(query)
    time.sleep(OVERPASS_INTERVAL_SECONDS)
    centers = []
    for e in elements:
        if e.get("type") == "node":
            centers.append((e["lat"], e["lon"]))
        elif "center" in e:
            centers.append((e["center"]["lat"], e["center"]["lon"]))
    if not centers:
        return None
    if len(centers) > 1:
        # Several matches (branch campuses, a same-named high school): if they
        # are far apart we cannot pick, so decline rather than guess.
        if max(_haversine_m(centers[0], c) for c in centers) > 1500:
            print(f"      {len(centers)} OSM schools match {school_name!r}; not using as anchor")
            return None
    return centers[0]


def _strip_direction_suffix(expanded):
    """'Ranch Circle East' -> 'Ranch Circle' (OSM sometimes omits the suffix)."""
    tokens = expanded.split()
    if len(tokens) > 1 and tokens[-1] in _DIRECTIONS.values():
        return " ".join(tokens[:-1])
    return None


def geocode(row):
    """Resolve one row to (lat, lon, how) or None.

    Order of trust: manual -> OSM intersection node -> Nominatim intersection
    phrasing -> (single-street rows) the street node nearest the school ->
    the OSM school polygon -> Nominatim school lookup. `how` records which
    won so the report can say how much to trust each coordinate.
    """
    manual = MANUAL_COORDS.get(row["school_name"])
    if manual:
        return manual[0], manual[1], "manual"

    cache = {}

    def school_point():
        """Best available location of the school itself: OSM polygon, else
        Nominatim. Cached per row; False means both came back empty."""
        if "pt" not in cache:
            point = None
            try:
                point = overpass_school_center(row["school_name"])
                if point:
                    cache["src"] = "osm-school"
            except Exception as error:
                print(f"      overpass error (school): {error}")
            if not point:
                point = _nominatim_first(f"{row['school_name']}, Phoenix, AZ")
                if point:
                    cache["src"] = "school-name"
            cache["pt"] = point or False
        return cache["pt"] or None

    def intersection_clusters(a, b):
        try:
            return overpass_intersection(osm_street_name(a), osm_street_name(b))
        except Exception as error:
            print(f"      overpass error: {error}")
            return []

    if row["cross_streets"]:
        a, b = row["cross_streets"]
        pairs = [(a, b)]
        stripped = [_strip_direction_suffix(osm_street_name(x)) for x in (a, b)]
        if any(stripped):
            pairs.append((stripped[0] or osm_street_name(a), stripped[1] or osm_street_name(b)))
        for alt in ALT_QUERIES.get(row["school_name"], []):
            pairs.append(tuple(alt.split(" & ")))

        for pa, pb in pairs:
            clusters = intersection_clusters(pa, pb)
            if len(clusters) == 1:
                return clusters[0][0], clusters[0][1], "osm-intersection"
            if len(clusters) > 1:
                # The two streets meet more than once. Pick the meeting point
                # nearest the school; otherwise refuse rather than guess.
                point = school_point()
                if point:
                    best = min(clusters, key=lambda c: _haversine_m(c, point))
                    if _haversine_m(best, point) <= 1500:
                        return best[0], best[1], "osm-intersection-nearest-school"
                print(f"      ambiguous: {len(clusters)} separate intersections -- "
                      + "; ".join(f"https://www.google.com/maps?q={c[0]:.6f},{c[1]:.6f}"
                                  for c in clusters))
                return None
        for query, how in ((f"{a} & {b}, Phoenix, AZ", "intersection"),
                           (f"{a} and {b}, Phoenix, AZ", "intersection-alt")):
            found = _nominatim_first(query)
            if found:
                return found[0], found[1], how
    else:
        # Only a street was published: the camera is on that street at the
        # school, so take the street node nearest the school campus.
        point = school_point()
        if point:
            try:
                node = overpass_nearest_street_node(
                    osm_street_name(row["location_text"].strip()), point, max_m=800)
            except Exception as error:
                print(f"      overpass error: {error}")
                node = None
            if node:
                return node[0], node[1], "osm-street-near-school"

    point = school_point()
    if point:
        return point[0], point[1], cache.get("src", "school-name")
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
            resolvable = row["cross_streets"] or row["school_name"] in MANUAL_COORDS
            marker = "  " if resolvable else "!!"
            target = (" & ".join(row["cross_streets"]) if row["cross_streets"]
                      else ("manual coordinates" if row["school_name"] in MANUAL_COORDS
                            else "NO INTERSECTION IN PDF"))
            print(f"{marker} d{row['district']} {row['active_from']}..{row['active_until']}  "
                  f"{row['school_name']}  <-  {target}")
        missing = [r for r in rows
                   if not r["cross_streets"] and r["school_name"] not in MANUAL_COORDS]
        if missing:
            print(f"\n{len(missing)} row(s) have no published intersection; --geocode places "
                  f"them on the named street beside the school (verify on a map):")
            for row in missing:
                print(f"  - {row['school_name']} (district {row['district']}): "
                      f"\"{row['location_text']}\"")
        return

    # Rows whose PDF entry names only one street cannot be geocoded as an
    # intersection; they resolve by SCHOOL NAME instead (the campus fronts
    # that street, so the offset from the true camera spot is at most a few
    # hundred metres inside a 600 m omnidirectional ring) and are flagged at
    # the end for a map check. MANUAL_COORDS still wins when present.
    unresolved = [r for r in rows
                  if not r["cross_streets"] and r["school_name"] not in MANUAL_COORDS]
    if unresolved:
        print(f"{len(unresolved)} row(s) have no published intersection — resolving by "
              f"school name; spot-check these on a map before pushing:")
        for row in unresolved:
            print(f"  - {row['school_name']} (district {row['district']}): "
                  f"\"{row['location_text']}\"")
        print()

    failures = []
    for index, row in enumerate(rows, start=1):
        if index > 1:
            time.sleep(REQUEST_INTERVAL_SECONDS)
        found = geocode(row)
        if found:
            row["latitude"], row["longitude"], row["how"] = found
            trusted = found[2] in ("manual", "osm-intersection", "intersection",
                                   "intersection-alt")
            flag = "" if trusted else f"   <- {found[2]}, verify on a map"
            print(f"[{index:2}/{len(rows)}] ok    {row['school_name']:38} "
                  f"{found[0]:.6f},{found[1]:.6f}  [{found[2]}]{flag}")
        else:
            failures.append(row)
            target = " & ".join(row["cross_streets"]) if row["cross_streets"] else "-"
            print(f"[{index:2}/{len(rows)}] FAIL  {row['school_name']:38} {target}")

    # Duplicate detection. Two schools sharing a point means a lookup silently
    # fell back to something generic; the bbox and centroid checks both pass in
    # that case, so this is the last line of defence before a wrong coordinate
    # ships to every phone.
    seen = {}
    duplicates = []
    for row in rows:
        if "latitude" not in row:
            continue
        point = (round(row["latitude"], 5), round(row["longitude"], 5))
        if point in seen:
            duplicates.append((seen[point], row["school_name"], point))
        else:
            seen[point] = row["school_name"]

    if failures or duplicates:
        if failures:
            print(f"\n{len(failures)} of {len(rows)} rows did not resolve.")
            for row in failures:
                target = " & ".join(row["cross_streets"]) if row["cross_streets"] else "-"
                print(f"  - {row['school_name']} (district {row['district']}): {target}")
        if duplicates:
            print(f"\n{len(duplicates)} pair(s) resolved to the SAME point — at least "
                  f"one of each pair is wrong:")
            for first, second, point in duplicates:
                print(f"  - {first}  ==  {second}  at {point}")
        print("\nNothing was written — a partial or duplicated file would ship wrong "
              "alert locations.")
        print("Add the right coordinates to MANUAL_COORDS at the top of this file, "
              "then re-run.")
        sys.exit(1)

    by_name = sorted(r["how"] for r in rows)
    print("\nresolved by: " + ", ".join(f"{by_name.count(k)}x {k}" for k in sorted(set(by_name))))
    school_name_rows = [r for r in rows if r["how"] == "school-name"]
    if school_name_rows:
        print(f"NOTE: {len(school_name_rows)} row(s) resolved to the SCHOOL, not the "
              f"published intersection. The camera sits at the intersection, so spot-check "
              f"these on a map before pushing:")
        for row in school_name_rows:
            print(f"  - {row['school_name']}: published as \"{row['location_text']}\"")

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
