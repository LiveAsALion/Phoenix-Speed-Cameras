#!/usr/bin/env python3
"""
Daily updater for Phoenix photo safety corridor JSON.

Fetches KML from City My Maps → extracts corridor pins → builds direction-aware JSON.
"""

import json
import os
import re
import sys
from typing import List, Dict

import requests
from xml.etree import ElementTree as ET

# CONFIG
CITY_MAP_KML_URL = (
    "https://www.google.com/maps/d/kml?forcekml=1"
    "&mid=1aB99-IfJH8EKHO_nVtF-xhgsMTKU_mw"
    "&lid=zEYtk9GgoDg"
)
OUTPUT_PATH = "data/phoenix_speed_cameras.json"
DEFAULT_RADIUS_METERS = 800

DIR_TO_DEG = {
    "N/B": 0,
    "E/B": 90,
    "S/B": 180,
    "W/B": 270,
}
DIRECTION_TAG_PATTERN = re.compile(r"\b([NESW]/B)\b", re.IGNORECASE)


def fetch_kml(url: str) -> str:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_kml_placemarks(kml_text: str) -> List[Dict]:
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    root = ET.fromstring(kml_text)
    placemarks = []

    for pm in root.findall(".//k:Placemark", ns):
        name = (pm.find("k:name", ns).text or "").strip()
        desc = (pm.find("k:description", ns).text or "").strip()
        coords_el = pm.find(".//k:Point/k:coordinates", ns)
        if not coords_el or not coords_el.text:
            continue

        try:
            lng_str, lat_str, *_ = coords_el.text.strip().split(",")
            lat, lng = float(lat_str), float(lng_str)
        except Exception:
            continue

        placemarks.append({"name": name, "description": desc, "lat": lat, "lng": lng})

    return placemarks


def normalize_id(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower())
    return re.sub(r"_+", "_", s).strip("_")


def extract_direction_tags(text: str) -> List[str]:
    tags = []
    for m in DIRECTION_TAG_PATTERN.findall(text):
        tag = m.upper()
        if tag in DIR_TO_DEG and tag not in tags:
            tags.append(tag)
    return tags


def infer_axis_from_name(name: str) -> str:
    # Default to EW; can be refined if City publishes more NS corridors.
    return "EW"


def build_direction_entries(
    base_id: str, name: str, lat: float, lng: float, desc: str
) -> List[Dict]:
    text = f"{name} {desc}".upper()
    explicit_tags = extract_direction_tags(text)

    if explicit_tags:
        return [
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
            for tag in explicit_tags
        ]

    # Assume both directions along main axis
    axis = infer_axis_from_name(name)
    tags = ["E/B", "W/B"] if axis == "EW" else ["N/B", "S/B"]

    print(
        f"WARN: Direction not explicit for '{base_id}'. Assuming both along {axis}.",
        file=sys.stderr,
    )

    return [
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
        for tag in tags
    ]


def main() -> None:
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    print(f"Fetching from {CITY_MAP_KML_URL}", file=sys.stderr)
    kml_text = fetch_kml(CITY_MAP_KML_URL)
    placemarks = parse_kml_placemarks(kml_text)

    if not placemarks:
        print("ERROR: No placemarks found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(placemarks)} placemarks.", file=sys.stderr)

    entries = []
    for pm in placemarks:
        if not pm["name"]:
            continue
        base_id = normalize_id(pm["name"])
        entries.extend(build_direction_entries(**pm, base_id=base_id))

    entries.sort(key=lambda e: e["id"])

    # Only rewrite if changed
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                if json.load(f) == entries:
                    print("No changes; skipping rewrite.", file=sys.stderr)
                    return
        except Exception:
            pass

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)

    print(f"Wrote {len(entries)} entries to {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
