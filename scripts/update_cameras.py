#!/usr/bin/env python3
"""
Daily updater for Phoenix photo safety corridor JSON.

- Fetches KML from the City's Google My Maps layer (network link).
- Extracts corridor placemarks (pins).
- Normalizes names/IDs.
- Assigns directions based on known rules / explicit tags when available.
- Writes data/phoenix_speed_cameras.json.

You must have:
    CITY_MAP_KML_URL set to the network-link KML URL you exported.
"""

import json
import math
import os
import re
import sys
from typing import Dict, List, Tuple

import requests
from xml.etree import ElementTree as ET

# ---- CONFIG -----------------------------------------------------------------

CITY_MAP_KML_URL = (
    "https://www.google.com/maps/d/kml?forcekml=1"
    "&mid=1aB99-IfJH8EKHO_nVtF-xhgsMTKU_mw"
    "&lid=zEYtk9GgoDg"
)

OUTPUT_PATH = os.path.join("data", "phoenix_speed_cameras.json")

# Fixed influence radius for all corridors (meters)
DEFAULT_RADIUS_METERS = 800

# Direction mapping
DIR_TO_DEG = {
    "N/B": 0,
    "E/B": 90,
    "S/B": 180,
    "W/B": 270,
}

# How much text we consider "explicit" direction markers
DIRECTION_TAG_PATTERN = re.compile(r"\b([NESW]/B)\b", re.IGNORECASE)


# ---- HELPER FUNCTIONS -------------------------------------------------------


def fetch_kml(url: str) -> str:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_kml_placemarks(kml_text: str) -> List[Dict]:
    """
    Return a list of dicts: {name, description, lat, lng} for each Point placemark.
    """
    # KML default namespace
    ns = {"k": "http://www.opengis.net/kml/2.2"}

    root = ET.fromstring(kml_text)
    placemarks = []

    for pm in root.findall(".//k:Placemark", ns):
        name_el = pm.find("k:name", ns)
        desc_el = pm.find("k:description", ns)
        point_el = pm.find(".//k:Point/k:coordinates", ns)

        if point_el is None:
            continue

        coords_text = (point_el.text or "").strip()
        if not coords_text:
            continue

        # KML coordinates are "lng,lat[,alt]"
        try:
            lng_str, lat_str, *_ = coords_text.split(",")
            lat = float(lat_str)
            lng = float(lng_str)
        except Exception:
            continue

        name = (name_el.text or "").strip() if name_el is not None else ""
        desc = (desc_el.text or "").strip() if desc_el is not None else ""

        placemarks.append(
            {
                "name": name,
                "description": desc,
                "lat": lat,
                "lng": lng,
            }
        )

    return placemarks


def normalize_id(name: str) -> str:
    """
    Turn corridor name into a stable ID, e.g.:
      "Camelback Rd: 24th St to 32nd St" -> "camelback_24_32"
    """
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    return s


def infer_axis_from_name(name: str) -> str:
    """
    Very simple axis inference: look for common east-west or north-south roads.
    If we can't tell, default to east-west.
    """
    # You can expand this if Phoenix later publishes more detail.
    # For now, most listed corridors are east-west arterials.
    # [web:3][web:8]
    text = name.lower()
    # If it clearly mentions a "north/south" clue, return NS
    if "avenue" in text or "st" in text:
        # many north-south streets in Phoenix are numbered avenues/streets,
        # but corridors described so far tend to be east-west arterials.
        # We default to EW unless we have explicit tags.
        pass
    # Default axis: east-west
    return "EW"


def extract_direction_tags(text: str) -> List[str]:
    """
    Find explicit N/B, E/B, S/B, W/B tags in the given text.
    """
    matches = DIRECTION_TAG_PATTERN.findall(text)
    tags = []
    for m in matches:
        tag = m.upper()
        if tag in DIR_TO_DEG and tag not in tags:
            tags.append(tag)
    return tags


def build_direction_entries(
    base_id: str,
    name: str,
    lat: float,
    lng: float,
    description: str,
) -> List[Dict]:
    """
    Build one or more JSON entries for this corridor based on direction info.
    - If text has explicit N/B, E/B, etc -> use those.
    - If text clearly says both, create both.
    - If nothing explicit: assume both directions along main axis,
      mark direction_source='assumed_both' and log a warning.
    """
    entries = []

    text = f"{name} {description}".upper()

    # First, look for explicit direction tags like N/B, E/B, etc.
    explicit_tags = extract_direction_tags(text)

    if explicit_tags:
        for tag in explicit_tags:
            entries.append(
                {
                    "id": f"{base_id}_{tag.lower().replace('/', '')}",
                    "name": f"{name} ({tag})",
                    "type": "corridor_fixed",
                    "lat": lat,
                    "lng": lng,
                    "radius_meters": DEFAULT_RADIUS_METERS,
                    "direction_deg": DIR_TO_DEG[tag],
                    "direction_source": "explicit",
                    "source": "city_my_maps",
                }
            )
        return entries

    # If description/name explicitly mentions both directions textually,
    # we still want two explicit entries. This is heuristic and easily
    # extendable if the City publishes clearer phrases.
    if "BOTH DIRECTIONS" in text or "NB/SB" in text or "EB/WB" in text:
        # Try to infer axis, then create two explicit tags for that axis.
        axis = infer_axis_from_name(name)
        if axis == "EW":
            tags = ["E/B", "W/B"]
        else:
            tags = ["N/B", "S/B"]
        for tag in tags:
            entries.append(
                {
                    "id": f"{base_id}_{tag.lower().replace('/', '')}",
                    "name": f"{name} ({tag})",
                    "type": "corridor_fixed",
                    "lat": lat,
                    "lng": lng,
                    "radius_meters": DEFAULT_RADIUS_METERS,
                    "direction_deg": DIR_TO_DEG[tag],
                    "direction_source": "explicit",
                    "source": "city_my_maps",
                }
            )
        return entries

    # No explicit direction markers: assume both directions along main axis
    axis = infer_axis_from_name(name)
    if axis == "EW":
        tags = ["E/B", "W/B"]
    else:
        tags = ["N/B", "S/B"]

    # Log once for this base_id that we are assuming both directions
    print(
        f"WARN: Direction not explicit for corridor '{base_id}'. "
        f"Assuming both directions along axis {axis}.",
        file=sys.stderr,
    )

    for tag in tags:
        entries.append(
            {
                "id": f"{base_id}_{tag.lower().replace('/', '')}",
                "name": f"{name} ({tag})",
                "type": "corridor_fixed",
                "lat": lat,
                "lng": lng,
                "radius_meters": DEFAULT_RADIUS_METERS,
                "direction_deg": DIR_TO_DEG[tag],
                "direction_source": "assumed_both",
                "source": "city_my_maps",
            }
        )

    return entries


# ---- MAIN -------------------------------------------------------------------


def main() -> None:
    # Ensure output directory exists
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    print(f"INFO: Fetching KML from {CITY_MAP_KML_URL}", file=sys.stderr)
    kml_text = fetch_kml(CITY_MAP_KML_URL)

    placemarks = parse_kml_placemarks(kml_text)
    if not placemarks:
        print("ERROR: No placemarks found in KML.", file=sys.stderr)
        sys.exit(1)

    print(f"INFO: Found {len(placemarks)} placemarks.", file=sys.stderr)

    all_entries: List[Dict] = []

    for pm in placemarks:
        name = pm["name"] or ""
        desc = pm["description"] or ""
        lat = pm["lat"]
        lng = pm["lng"]

        if not name:
            # Fallback: skip nameless placemarks
            continue

        base_id = normalize_id(name)

        entries = build_direction_entries(base_id, name, lat, lng, desc)
        all_entries.extend(entries)

    # Sort entries for stable output
    all_entries.sort(key=lambda e: e["id"])

    # If an old file exists, compare to avoid unnecessary writes
    old_data = None
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                old_data = json.load(f)
        except Exception:
            old_data = None

    if old_data == all_entries:
        print("INFO: No changes detected; not rewriting JSON.", file=sys.stderr)
        return

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_entries, f, indent=2, sort_keys=False)

    print(
        f"INFO: Wrote {len(all_entries)} entries to {OUTPUT_PATH}.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
