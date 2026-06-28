import requests
from bs4 import BeautifulSoup
import json
import re

KML_URL = "https://www.google.com/maps/d/kml?forcekml=1&mid=1aB99-IfJH8EKHO_nVtF-xhgsMTKU_mw"
OUTPUT_JSON = "camera_data.json"

# Sentinel written when a Placemark has no recognizable direction designator.
# The SpeedShield app treats direction_deg = -1 as "omnidirectional": it skips
# the heading-alignment filter and alerts regardless of travel direction.
OMNIDIRECTIONAL = -1

DIRECTION_MAP = {
    "E/B": 90, "EB": 90, "EAST": 90,
    "W/B": 270, "WB": 270, "WEST": 270,
    "N/B": 0,  "NB": 0,  "NORTH": 0,
    "S/B": 180, "SB": 180, "SOUTH": 180,
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
        direction_deg = get_direction(desc)

        if direction_deg is None:
            # No direction designator on the source map. Previously these were
            # dropped with `continue`, which silently lost ~1/3 of the corridor
            # cameras (e.g. "7th Ave - Indian School Rd to Camelback Rd"). Keep
            # them by marking omnidirectional; the app alerts in both directions.
            print(f"  No direction found, marking omnidirectional: {desc}")
            direction_deg = OMNIDIRECTIONAL

        coords = coords_tag.get_text().strip()
        parts = coords.split(",")
        if len(parts) < 2:
            continue

        lon, lat = float(parts[0]), float(parts[1])

        # Strip trailing "Portable tower location" noise and whitespace
        clean_name = re.split(r"(?i)\s*<br", desc)[0]
        clean_name = re.sub(r"(?i)\s*portable tower location.*", "", clean_name).strip()

        cameras.append({
            "name": clean_name,
            "latitude": lat,
            "longitude": lon,
            "direction_deg": direction_deg
        })

    if not cameras:
        print("No valid camera locations found.")
        return

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(cameras, f, indent=4)

    print(f"Success! Saved {len(cameras)} cameras to {OUTPUT_JSON}.")

if __name__ == "__main__":
    update_camera_data()
