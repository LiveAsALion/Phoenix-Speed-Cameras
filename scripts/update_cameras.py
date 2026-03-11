#!/usr/bin/env python3
"""
Daily updater for Phoenix photo safety corridor JSON.

Parses City Photo Safety page → geocodes corridors → builds direction‑aware JSON.
"""

import json
import os
import re
import sys
from typing import List, Dict

import requests
from bs4 import BeautifulSoup

# CONFIG
PHOTO_SAFETY_PAGE = (
    "https://www.phoenix.gov/administration/departments/streets/safety-improvements/road-safety-action-plan/photo-safety.html"
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OUTPUT_PATH = "data/phoenix_speed_cameras.json"
DEFAULT_RADIUS_METERS = 800

DIR_TO_DEG = {"N/B": 0, "E/B": 90, "S/B": 180, "W/B": 270}
DIRECTION_TAG_PATTERN = re.compile(r"\b([NESW]/B)\b", re.IGNORECASE)


def fetch_page(url: str) -> str:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def geocode_corridor(name: str) -> Dict[str, float]:
    """
    Geocode a corridor name to approximate center lat/lng using Nominatim.
    """
    params = {
        "q": name + ", Phoenix, AZ",
        "format": "json",
        "limit": 1,
        "addressdetails": 1,
    }
    headers = {"User-Agent": "PhoenixSpeedCamerasBot/1.0"}
    
    resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
    if resp.status_code == 200:
        results = resp.json()
        if results:
            result = results[0]
            return {
                "lat": float(result["lat"]),
                "lng": float(result["lon"]),
            }
    
    # Fallback coordinates if geocoding fails (Phoenix center + offset)
    print(f"WARN: Geocoding failed for '{name}'; using fallback.", file=sys.stderr)
    return {"lat": 33.4484, "lng": -112.0740}


def parse_corridors(html: str) -> List[str]:
    """
    Extract corridor names from City page.
    """
    soup = BeautifulSoup(html, "html.parser")
    
    # Try to find list items with "to" and road names
    corridors = []
    for li in soup.find_all("li"):
        text = li.get_text(strip=True)
        if ":" in text and "to" in text.lower():
            corridors.append(text)
    
    if corridors:
        return corridors
    
    # Fallback: known 9 corridors from City page [page:0][web:3][web:8]
    print("WARN: No corridors found via parsing; using known list.", file=sys.stderr)
    return [
        "Thunderbird Road: 35th Avenue to Interstate 17",
        "32nd Street: Greenway Parkway to Bell Road",
        "Thunderbird Road: Interstate 17 to 19th Avenue",
        "7th Street: Thunderbird Road to Peoria Avenue",
        "Camelback Road: 24th Street to 32nd Street",
        "19th Avenue: Thunderbird Road to Peoria Avenue",
        "Northern Avenue: 7th Street to 19th Avenue",
        "7th Street: Indian School Road to Camelback Road",
        "19th Avenue: Indian School Road to Camelback Road",
    ]


def normalize_id(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower())
    return re.sub(r"_+", "_", s).strip("_")


def extract_direction_tags(text: str) -> List[str]:
    tags = []
    for m in DIRECTION_TAG_PATTERN.findall(text):
        tag
