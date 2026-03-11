#!/usr/bin/env python3
"""
Daily updater for Phoenix photo safety corridor JSON.

Parses the City's Photo Safety page → geocodes corridors → builds JSON.
"""

import json
import os
import re
import sys
from typing import List, Dict

import requests
from bs4 import BeautifulSoup

# CONFIG
PHOTO_SAFETY_PAGE = "https://www.phoenix.gov/administration/departments/streets/safety-improvements/road-safety-action-plan/photo-safety.html"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OUTPUT_PATH = "data/phoenix_speed_cameras.json"
DEFAULT_RADIUS_METERS = 800

DIR_TO_DEG = {
    "N/B": 0,
    "E/B": 90,
    "S/B": 180,
    "W/B": 270,
}


def fetch_page(url: str) -> str:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_corridors(html: str) -> List[str]:
    """
    Extract corridor names from the City page location list.
    """
    soup = BeautifulSoup(html, "html.parser")
    
    # Look for the location list (usually in a <ul> or <li> under "Location List")
    corridors = []
    for li in soup.find_all("li"):
        text = li.get_text(strip=True)
        if ":" in text and ("to" in text.lower() or "avenue" in text.lower()):
            corridors.append(text)
    
    # Fallback: hardcoded list from page if parsing fails
    if not corridors:
        print("WARN: No corridors found via parsing; using known list.", file=sys.stderr)
        corridors = [
            "Thunderbird Road: 35th Avenue to Interstate 17",
            "32nd Street : Greenway Parkway to Bell Road",
            "Thunderbird Road: Interstate 17 to 19th Avenue",
            "7th Street:
