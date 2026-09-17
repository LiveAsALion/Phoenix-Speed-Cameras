import requests
from bs4 import BeautifulSoup
import json
import re

KML_URL = "https://www.google.com/maps/d/kml?forcekml=1&mid=1aB99-IfJH8EKHO_nVtF-xhgsMTKU_mw"
OUTPUT_JSON = "camera_data.json"

# Sentinel written when a Placemark has no recognizable direction designator and
# no manual override below. The SpeedShield app treats direction_deg = -1 as
# "omnidirectional": it skips the heading-alignment filter and alerts regardless
# of travel direction.
OMNIDIRECTIONAL = -1

DIRECTION_MAP = {
    "E/B": 90, "EB": 90, "EAST": 90,
    "W/B": 270, "WB": 270, "WEST": 270,
    "N/B": 0,  "NB": 0,  "NORTH": 0,
    "S/B": 180, "SB": 180, "SOUTH": 180,
}

# Manual direction overrides for cameras whose source-map description carries no
# direction token. Direction is derived from which side of the corridor the pin
# sits on (the camera faces oncoming traffic):
#   north side -> westbound (270)    south side -> eastbound (90)
#   east side  -> northbound (0)     west side  -> southbound (180)
# Keyed by the exact cleaned camera name. If a name later changes on the source
# map the entry simply stops matching and the camera falls back to
# omnidirectional (safe), so this never produces a silently wrong direction.
NAME_DIRECTION_OVERRIDES = {
    "7th Ave - Indian School Rd to Camelback Rd": 180,       # west side  -> southbound
    "Missouri Ave - 99th Ave to 101st Ave": 270,             # north side -> westbound
    "Chandler Blvd- Desert Foothills": 270,                  # north side -> westbound
    "Thunderbird Rd between 7th St and Cave Creek Rd": 270,  # north side -> westbound
    "19th Ave between Peoria Ave and Cactus Rd": 0,          # east side  -> northbound
    # NORTHBOUND enforcement, corrected by the tester 2026-08-18 (an earlier
    # entry said southbound and was wrong for ~1 hour). The camera sits
    # adjacent to 5321 N 27th Ave. Until this entry existed the camera was
    # omnidirectional and fired on I-17 traffic in BOTH directions.
    "27th Avenue: Colter Street to Missouri Avenue": 0,      # east side  -> northbound
}

# Road AXIS overrides (a line: 0 == 180), distinct from enforcement direction —
# it can be known when the enforcement direction is not. Feeds the app's v13
# corridor gate: a driver more than ~75 m off the camera's road line is
# suppressed. Added for the 27th Ave camera, whose 600 m ring reaches the
# I-17 414 m away and produced four freeway false alerts in three days
# (2026-08-11..14) before this field existed. Keyed by cleaned camera name;
# a renamed camera stops matching and simply loses its corridor (safe).
ROAD_AXIS_OVERRIDES = {
    "27th Avenue: Colter Street to Missouri Avenue": 0,      # 27th Ave runs north-south
}

# Coordinate overrides for scraped pins that do not sit on the roadway they
# enforce (the app's corridor gate suppresses a driver more than ~75 m off
# the camera's road line, and the 200 m secondary ring never admits a pass
# whose closest approach is farther than that). Keyed by the exact cleaned
# name; a renamed or re-pinned source entry stops matching (safe).
#
# EMPTY ON PURPOSE, and a lesson: on 2026-09-12 the Northern Ave W/B pin
# (33.555284) was overridden 285 m south because a grid extrapolation said
# Northern "must" be at 33.5528 there. It is not — Northern Ave jogs
# north-east at 16th St and the city's pin sits on the road just west of
# 17th St (tester screenshot of the city map, same day). Reverted within
# the hour. Never add an entry here from arithmetic; only from the city's
# map or a ground check.
COORDINATE_OVERRIDES = {}

# Hand-curated entries appended to every scrape output. The scrape rebuilds
# camera_data.json from scratch, so anything not in the Phoenix KML and not
# in this list is DELETED on every run. Tempe's program
# (tempe.gov/PhotoEnforcement, verified 2026-08-30) publishes no scrapeable
# map — 14 fixed red-light+speed intersections, hand-maintained here;
# coordinates map-verified (tester pin drops + geocode,
# SpeedShield docs/TEMPE_CAMERAS_DRAFT.md). direction_deg -1 =
# omnidirectional (intersection cameras, multiple approaches); no
# road_axis_deg on purpose - two roads cross, a single corridor line would
# wrongly suppress the cross street. Tempe's four MOBILE units publish no
# locations and are deliberately absent.
MANUAL_CAMERAS = [
    {
        "name": "Baseline Rd and Rural Rd: Tempe",
        "latitude": 33.378238,
        "longitude": -111.928687,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Baseline Rd and Mill Ave: Tempe",
        "latitude": 33.3789,
        "longitude": -111.9392,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Southern Ave and Mill Ave: Tempe",
        "latitude": 33.3932,
        "longitude": -111.9394,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Warner Rd and McClintock Dr: Tempe",
        "latitude": 33.3346,
        "longitude": -111.9097,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Guadalupe Rd and McClintock Dr: Tempe",
        "latitude": 33.3635,
        "longitude": -111.9097,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "University Dr and McClintock Dr: Tempe",
        "latitude": 33.4221,
        "longitude": -111.9097,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Broadway Rd and McClintock Dr: Tempe",
        "latitude": 33.4077,
        "longitude": -111.9097,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Broadway Rd and Rural Rd: Tempe",
        "latitude": 33.4077,
        "longitude": -111.9267,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Elliot Rd and Rural Rd: Tempe",
        "latitude": 33.349184,
        "longitude": -111.928467,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Elliot Rd and Kyrene Rd: Tempe",
        "latitude": 33.349186,
        "longitude": -111.945717,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "University Dr and Priest Dr: Tempe",
        "latitude": 33.421936,
        "longitude": -111.960913,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Curry Rd and Scottsdale Rd: Tempe",
        "latitude": 33.4407,
        "longitude": -111.9262,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Rio Salado Pkwy and Rural Rd: Tempe",
        "latitude": 33.4287,
        "longitude": -111.9258,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "48th St and Broadway Rd: Tempe",
        "latitude": 33.4068,
        "longitude": -111.9785,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "S/B, Scottsdale Rd and McDowell Rd: Scottsdale",
        "latitude": 33.466266,
        "longitude": -111.92603,
        "direction_deg": 180,
        "type": "red_light_speed"
    },
    {
        "name": "N/B, Scottsdale Rd and Thomas Rd: Scottsdale",
        "latitude": 33.480353,
        "longitude": -111.926169,
        "direction_deg": 0,
        "type": "red_light_speed"
    },
    {
        "name": "W/B, Indian School Rd and Hayden Rd: Scottsdale",
        "latitude": 33.494875,
        "longitude": -111.908877,
        "direction_deg": 270,
        "type": "red_light_speed"
    },
    {
        "name": "E/B, Shea Blvd and 90th St: Scottsdale",
        "latitude": 33.582536,
        "longitude": -111.886125,
        "direction_deg": 86,
        "type": "red_light_speed"
    },
    {
        "name": "W/B, Shea Blvd and 92nd St: Scottsdale",
        "latitude": 33.582537,
        "longitude": -111.882697,
        "direction_deg": 270,
        "type": "red_light_speed"
    },
    {
        "name": "S/B, Frank Lloyd Wright Blvd and Cactus Rd: Scottsdale",
        "latitude": 33.59731,
        "longitude": -111.843267,
        "direction_deg": 125,
        "type": "red_light_speed"
    },
    {
        "name": "E/B, Frank Lloyd Wright Blvd and Greenway-Hayden Loop: Scottsdale",
        "latitude": 33.6344477,
        "longitude": -111.9077888,
        "direction_deg": 105,
        "type": "red_light_speed"
    },
    {
        "name": "W/B, Frank Lloyd Wright Blvd and Greenway-Hayden Loop: Scottsdale",
        "latitude": 33.6344477,
        "longitude": -111.9077888,
        "direction_deg": 285,
        "type": "red_light_speed"
    },
    {
        "name": "N/B, Scottsdale Rd and Frank Lloyd Wright Blvd: Scottsdale",
        "latitude": 33.638115,
        "longitude": -111.925301,
        "direction_deg": 1,
        "type": "red_light_speed"
    },
    {
        "name": "S/B, Scottsdale Rd and Pinnacle Peak Rd: Scottsdale",
        "latitude": 33.698697,
        "longitude": -111.925321,
        "direction_deg": 180,
        "type": "red_light_speed"
    },
    {
        "name": "E/B, Thomas Rd and Hayden Rd: Scottsdale",
        "latitude": 33.480391,
        "longitude": -111.908964,
        "direction_deg": 90,
        "type": "red_light_speed"
    },
    {
        "name": "Lincoln Dr and Tatum Blvd: Paradise Valley",
        "latitude": 33.531055,
        "longitude": -111.97563,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "E/B, Lincoln Dr and Palo Cristi Rd: Paradise Valley",
        "latitude": 33.531828,
        "longitude": -112.004014,
        "direction_deg": 90,
        "type": "red_light_speed"
    },
    {
        "name": "W/B, Lincoln Dr and Palo Cristi Rd: Paradise Valley",
        "latitude": 33.531828,
        "longitude": -112.004014,
        "direction_deg": 270,
        "type": "red_light_speed"
    },
    {
        "name": "E/B, Lincoln Dr and Mockingbird Ln: Paradise Valley",
        "latitude": 33.531278,
        "longitude": -111.934486,
        "direction_deg": 89,
        "type": "red_light_speed"
    },
    {
        "name": "W/B, Lincoln Dr and Mockingbird Ln: Paradise Valley",
        "latitude": 33.531278,
        "longitude": -111.934486,
        "direction_deg": 268,
        "type": "red_light_speed"
    },
    {
        "name": "N/B, Tatum Blvd and Desert Jewel Dr: Paradise Valley",
        "latitude": 33.553581,
        "longitude": -111.975667,
        "direction_deg": 0,
        "type": "red_light_speed"
    },
    {
        "name": "S/B, Tatum Blvd and Foothill Dr: Paradise Valley",
        "latitude": 33.5530848,
        "longitude": -111.9756704,
        "direction_deg": 186,
        "type": "red_light_speed"
    },
    {
        "name": "N/B, Tatum Blvd and McDonald Dr: Paradise Valley",
        "latitude": 33.5243756,
        "longitude": -111.9763155,
        "direction_deg": 3,
        "type": "red_light_speed"
    },
    {
        "name": "S/B, Tatum Blvd and McDonald Dr: Paradise Valley",
        "latitude": 33.5243756,
        "longitude": -111.9763155,
        "direction_deg": 183,
        "type": "red_light_speed"
    },
    {
        "name": "Alma School Rd and Queen Creek Rd: Chandler",
        "latitude": 33.261916,
        "longitude": -111.858578,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Alma School Rd and Ray Rd: Chandler",
        "latitude": 33.3206,
        "longitude": -111.859137,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Alma School Rd and Warner Rd: Chandler",
        "latitude": 33.33509,
        "longitude": -111.85911,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Arizona Ave and Ray Rd: Chandler",
        "latitude": 33.3206297,
        "longitude": -111.841615,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Arizona Ave and Warner Rd: Chandler",
        "latitude": 33.335134,
        "longitude": -111.841752,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Arizona Ave and Ocotillo Rd: Chandler",
        "latitude": 33.247607,
        "longitude": -111.8412465,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Chandler Blvd and Dobson Rd: Chandler",
        "latitude": 33.305965,
        "longitude": -111.876336,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Chandler Blvd and Kyrene Rd: Chandler",
        "latitude": 33.3053484,
        "longitude": -111.9457214,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "McQueen Rd and Queen Creek Rd: Chandler",
        "latitude": 33.262392,
        "longitude": -111.824132,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Ray Rd and Dobson Rd: Chandler",
        "latitude": 33.3205739,
        "longitude": -111.876403,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Ray Rd and McClintock Dr: Chandler",
        "latitude": 33.320029,
        "longitude": -111.911172,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Ray Rd and Rural Rd: Chandler",
        "latitude": 33.319799,
        "longitude": -111.927364,
        "direction_deg": -1,
        "type": "red_light_speed"
    }
]

def get_direction(text):
    text_upper = text.upper()
    for key, deg in DIRECTION_MAP.items():
        if re.search(rf'\b{re.escape(key)}\b', text_upper):
            return deg
    return None

def update_camera_data():
    print(f"Fetching: {KML_URL}")
    try:
        response = requests.get(KML_URL, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "xml")
    except Exception as e:
        print(f"Failed to fetch KML: {e}")
        return

    cameras = []
    for pm in soup.find_all("Placemark"):
        desc_tag = pm.find("description")
        coords_tag = pm.find("coordinates")

        if not desc_tag or not coords_tag:
            continue

        # Description contains direction + corridor, e.g. "E/B, Thunderbird Rd: 35th Ave to I-17"
        desc = re.sub(r"<[^>]+>", "", desc_tag.get_text()).strip()

        coords = coords_tag.get_text().strip()
        parts = coords.split(",")
        if len(parts) < 2:
            continue

        lon, lat = float(parts[0]), float(parts[1])

        # Strip trailing "Portable tower location" noise and whitespace
        clean_name = re.split(r"(?i)\s*<br", desc)[0]
        clean_name = re.sub(r"(?i)\s*portable tower location.*", "", clean_name).strip()

        # Direction priority: token in the description, then manual override,
        # then omnidirectional fallback.
        direction_deg = get_direction(desc)
        if direction_deg is None:
            direction_deg = NAME_DIRECTION_OVERRIDES.get(clean_name)
        if direction_deg is None:
            print(f"  No direction found, marking omnidirectional: {clean_name}")
            direction_deg = OMNIDIRECTIONAL

        camera = {
            "name": clean_name,
            "latitude": lat,
            "longitude": lon,
            "direction_deg": direction_deg
        }
        fix = COORDINATE_OVERRIDES.get(clean_name)
        if fix is not None:
            print(f"  Coordinate override: {clean_name} {lat},{lon} -> {fix[0]},{fix[1]}")
            camera["latitude"], camera["longitude"] = fix
        road_axis = ROAD_AXIS_OVERRIDES.get(clean_name)
        if road_axis is not None:
            camera["road_axis_deg"] = road_axis
        cameras.append(camera)

    if not cameras:
        print("No valid camera locations found.")
        return

    # Manual cities ride along AFTER the empty-scrape guard: a failed or
    # empty Phoenix scrape still never writes, and a good one always
    # carries the hand-curated entries.
    cameras.extend(MANUAL_CAMERAS)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(cameras, f, indent=4)

    print(f"Success! Saved {len(cameras)} cameras to {OUTPUT_JSON}.")

if __name__ == "__main__":
    update_camera_data()
