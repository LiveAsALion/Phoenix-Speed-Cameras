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
    # Southbound enforcement, confirmed on the ground 2026-08-18: the camera
    # sits adjacent to 5321 N 27th Ave. Until this entry existed the camera was
    # omnidirectional and fired on I-17 traffic in BOTH directions.
    "27th Avenue: Colter Street to Missouri Avenue": 180,    # west side  -> southbound
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
        road_axis = ROAD_AXIS_OVERRIDES.get(clean_name)
        if road_axis is not None:
            camera["road_axis_deg"] = road_axis
        cameras.append(camera)

    if not cameras:
        print("No valid camera locations found.")
        return

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(cameras, f, indent=4)

    print(f"Success! Saved {len(cameras)} cameras to {OUTPUT_JSON}.")

if __name__ == "__main__":
    update_camera_data()
