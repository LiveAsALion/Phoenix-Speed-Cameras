#!/usr/bin/env python3
"""Move a reviewed city from drafts/<city>.geocoded.json into the LIVE data:
append its entries to MANUAL_CAMERAS in update_cameras.py (so the nightly
scrape carries them forever) and to camera_data.json (so devices see them
at their next refresh, not at the updater's next run). Same commit, both
files -- the Tempe precedent.

    python3 scripts/stage_city.py scottsdale paradise_valley chandler

Underscore review keys are stripped. Refuses a city whose entries are
already present (by name) or whose review file is missing.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UPDATER = os.path.join(ROOT, "update_cameras.py")
LIVE = os.path.join(ROOT, "camera_data.json")
KEEP = ("name", "latitude", "longitude", "direction_deg", "type")
OPTIONAL = ("road_axis_deg",)


def entry_text(e):
    keys = [k for k in KEEP + OPTIONAL if k in e]
    body = ",\n".join(f'        "{k}": {json.dumps(e[k])}' for k in keys)
    return "    {\n" + body + "\n    }"


def main(cities):
    with open(UPDATER) as h:
        updater = h.read()
    with open(LIVE) as h:
        live = json.load(h)
    live_names = {c["name"] for c in live}
    added = []
    for city in cities:
        path = os.path.join(ROOT, "drafts", f"{city}.geocoded.json")
        if not os.path.exists(path):
            sys.exit(f"{city}: no review file at {path}")
        with open(path) as h:
            entries = json.load(h)
        for e in entries:
            if e["name"] in live_names or f'"name": {json.dumps(e["name"])}' in updater:
                sys.exit(f"{city}: {e['name']!r} is already live -- refusing")
        clean = [{k: e[k] for k in KEEP + OPTIONAL if k in e} for e in entries]
        # updater: insert before the closing bracket of MANUAL_CAMERAS
        start = updater.index("\nMANUAL_CAMERAS = [")
        end = updater.index("\n]\n", start)
        block = ",\n" + ",\n".join(entry_text(e) for e in clean)
        updater = updater[:end] + block + updater[end:]
        live.extend(clean)
        live_names.update(e["name"] for e in clean)
        added.append((city, len(clean)))
    with open(UPDATER, "w") as h:
        h.write(updater)
    with open(LIVE, "w") as h:
        json.dump(live, h, indent=4)
        h.write("\n")
    for city, n in added:
        print(f"staged {city}: {n} entries")
    print(f"camera_data.json now {len(live)} cameras")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
