import requests
from bs4 import BeautifulSoup
import json
import math
import os
import re
from datetime import datetime, timezone

KML_URL = "https://www.google.com/maps/d/kml?forcekml=1&mid=1aB99-IfJH8EKHO_nVtF-xhgsMTKU_mw"
OUTPUT_JSON = "camera_data.json"

# The Phoenix MAP is watched for shape changes the way every other city's
# source is (scripts/watch_city_sources.py): each run records the raw
# placemarks it parsed (name, city pin, whether a direction token was
# present) in PLACEMARK_SNAPSHOT and compares them with the committed copy.
# Any placemark that appears, disappears, moves more than PLACEMARK_MOVE_M or
# loses its direction token is written to MAP_ALERT, and the workflow opens
# a `camera-source-watch` issue from that file. Motive (2026-10-22): the city
# replaced its whole roster — 9 corridors became 8 with no direction tokens
# at all — and the updater published the new set SILENTLY; the name-keyed
# overrides below stopped matching, every Phoenix camera fell to
# omnidirectional, and nobody knew until a tester asked why the map was
# wrong. The data is still published (the file is the roster); the issue
# is how a human finds out the same hour.
PLACEMARK_SNAPSHOT = os.path.join("watch", "phoenix_kml_placemarks.json")
MAP_ALERT = os.path.join("watch", "PHOENIX_MAP_ALERT.md")
PLACEMARK_MOVE_M = 25

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
# direction token.
#
# STANDING EVALUATION RULE (tester, 2026-10-23) for any camera whose published
# list omits the enforced direction: read which side of the street the POLE
# stands on and announce the traffic it faces (the camera looks at oncoming
# traffic):
#   north side -> westbound (270)    south side -> eastbound (90)
#   east side  -> northbound (0)     west side  -> southbound (180)
# PRECONDITION: the point read must mark the POLE, to curb precision — a curb
# is 10-18 m from the centreline of a Phoenix arterial. The city's pre-10-22
# map pins did: its two Thunderbird pins sat 40 m apart north-south, one per
# side, the width of the road, and the E/B one was on the south side. Its
# 2026-10-22 pins do NOT: Thunderbird's sits 168 m south of the road, Bell's
# 68 m south of the old north-side pole, 7th St's 184 m along the road from
# the pole (corridor positions, not poles). A side read from such a pin is
# arithmetic, not a measurement — the 2026-09-12 Northern error — so the side
# comes from Street View or a drive-by, and the result is recorded HERE by
# name with its basis. The map alert prints a Street View link for every
# direction-less placemark to make that check a one-minute job.
#
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
    # 2026-10-22: the city re-published its whole Phoenix roster with NO
    # direction token in any description, so every scraped entry fell to
    # omnidirectional. Restored from ground truth only. Tester ground check
    # 2026-10-22 (northbound 7th St pass): the camera stands on the EAST
    # side of 7th St just north of Roma Ave, facing NORTHBOUND traffic.
    "7th St: Indian School Road to Camelback Road": 0,       # east side  -> northbound (tester, 2026-10-22)
    # Tester, 2026-10-23: "the Northern camera is only on the north side for
    # westbound traffic" — the W/B the city's old label carried, confirmed
    # on the ground under the new name. Eastbound Northern stays silent.
    "Northern Ave: 16th Street to SR 51": 270,               # north side -> westbound (tester, 2026-10-23)
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
    # Northern Ave bends toward SR-51 east of the pin: the westbound approach
    # heads 268 in its last 250 m and 240 out at 250-600 m, so the due-west
    # axis (direction_deg 270) put a driver 212 m off the corridor at 550 m
    # and silenced the primary (al-android v25, 2026-09-18 3:58 pm). Measured
    # 2026-09-18 from OSM geometry (scripts/measure_road_bearings.py --point,
    # 43 approach points): axis 73 (== 253) keeps every point within 64 m of
    # the line; the tightest stretch is the near straight at ~250 m (~65-70 m).
    "W/B Northern Avenue: 16th Street to 18th Street": 73,
    # Same camera under the name the city gave it on 2026-10-22 (its pin,
    # 33.555297/-112.046559, is the 09-12 pin within 15 m); the measured
    # axis carries over. Its westbound direction was restored on
    # 2026-10-23 (NAME_DIRECTION_OVERRIDES) once the tester confirmed the
    # pole faces westbound traffic only.
    "Northern Ave: 16th Street to SR 51": 73,
}

# Coordinate overrides for scraped pins that do not sit where the camera
# stands (the app's rings and corridor gate are drawn around the pin, so a
# pin 200 m from the pole fires "600 meters" 200 m early and counts the
# camera passed 200 m before it). Keyed by the exact cleaned name; a renamed
# source entry stops matching (safe). Each value carries BOTH the pole
# coordinate we publish AND the city pin the override was written against:
# when the scraped pin later moves more than COORDINATE_OVERRIDE_DRIFT_M
# from that recorded city pin, the run prints a WARNING and writes it to
# the map alert (an issue is opened) — the city may have re-pinned the pole
# itself and the override is then the stale one. Retire it by hand.
#
# A lesson, kept: on 2026-09-12 the Northern Ave W/B pin (33.555284) was
# overridden 285 m south because a grid extrapolation said Northern "must"
# be at 33.5528 there. It is not — Northern Ave jogs north-east at 16th St
# and the city's pin sits on the road just west of 17th St (tester
# screenshot of the city map, same day). Reverted within the hour. Never
# add an entry here from arithmetic; only from the city's map or a ground
# check.
COORDINATE_OVERRIDE_DRIFT_M = 25
COORDINATE_OVERRIDES = {
    # 7th St northbound. The city's 2026-10-22 pin is the MIDPOINT of the
    # "Indian School Road to Camelback Road" corridor; the pole stands ~190 m
    # south of it, on the EAST side of 7th St just north of Roma Ave, facing
    # northbound traffic. Ground check: the tester's phone fix at the pole
    # (33.49976, -112.06497 on the 2026-10-22 6:29 pm pass), moved to the
    # east curb, and his written confirmation 2026-10-23 ("just north of
    # Roma on the east side of the street for northbound traffic"). With the
    # city's pin the primary spoke ~200 m early and the engine counted the
    # camera passed ~200 m before the pole (diagnostics logs/2026-10-22).
    "7th St: Indian School Road to Camelback Road": {
        "lat": 33.4998, "lon": -112.0648,
        "city_lat": 33.501453, "city_lon": -112.064917,   # the pin on 2026-10-22
        "since": "2026-10-23",
    },
}

# Cameras the city's published LIST still carries while its MAP has dropped
# them. Standing rule (tester, 2026-10-23): when the page and the map
# disagree, KEEP the camera until a ground check verifies it is gone — a
# missed camera is a ticket, a false alert is not. Each entry rides into the
# output like MANUAL_CAMERAS unless a scraped placemark already lies within
# KEEP_DEDUPE_M of its pin — the map has it again, under whatever name, so
# the scraped entry wins and the kept one is skipped with a notice in the
# map alert (its direction/axis then need re-keying by hand, as on
# 2026-10-22). Keys beginning with "_" are provenance and are stripped
# before writing. Remove an entry ONLY on the tester's explicit verification
# that the camera is gone; the watch (scripts/watch_city_sources.py) reports
# the page/map disagreement nightly while it lasts (Phoenix issue #4).
KEEP_DEDUPE_M = 150
KEEP_UNTIL_VERIFIED = [
    {
        # 2026-10-22: the city's map dropped "27th Avenue: Colter Street to
        # Missouri Avenue" while its page kept listing 27th Avenue
        # ("Camelback Road to Bethany Home Road", the page's wording — the
        # same pole; the spoken name is the text before the colon). Pin,
        # direction and axis are the field-verified values that were live
        # from 2026-08-18 to 2026-10-22: pole adjacent to 5321 N 27th Ave,
        # east side, northbound; axis 0 keeps the I-17 (414 m west) outside
        # the corridor — confirmed 8 passes out of 8 in the diagnostics.
        "name": "27th Avenue: Camelback Road to Bethany Home Road",
        "latitude": 33.51575,
        "longitude": -112.116861,
        "direction_deg": 0,
        "road_axis_deg": 0,
        "_since": "2026-10-23",
        "_why": "city map dropped it 2026-10-22; city page still lists it",
    },
]

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
        "name": "Tatum Blvd and McDonald Dr: Paradise Valley",
        "latitude": 33.5243756,
        "longitude": -111.9763155,
        "direction_deg": -1,
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
    },
    {
        "name": "Alma School Rd and Guadalupe Rd: Mesa",
        "latitude": 33.364235,
        "longitude": -111.859097,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Broadway Rd and Stapley Dr: Mesa",
        "latitude": 33.407913,
        "longitude": -111.805334,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Broadway Rd and Extension Rd: Mesa",
        "latitude": 33.407748,
        "longitude": -111.84838,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Country Club Dr and Southern Ave: Mesa",
        "latitude": 33.393317,
        "longitude": -111.840075,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Country Club Dr and University Dr: Mesa",
        "latitude": 33.422372,
        "longitude": -111.83995,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Ellsworth Rd and Pecos Rd: Mesa",
        "latitude": 33.29225,
        "longitude": -111.63497,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Guadalupe Rd and Carriage Ln: Mesa",
        "latitude": 33.363865,
        "longitude": -111.892298,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Gilbert Rd and Baseline Rd: Mesa",
        "latitude": 33.379598,
        "longitude": -111.788141,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Gilbert Rd and McKellips Rd: Mesa",
        "latitude": 33.451758,
        "longitude": -111.787657,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Greenfield Rd and Main St: Mesa",
        "latitude": 33.415491,
        "longitude": -111.736421,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Power Rd and Broadway Rd: Mesa",
        "latitude": 33.408144,
        "longitude": -111.684752,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Power Rd and Hampton Ave: Mesa",
        "latitude": 33.390398,
        "longitude": -111.684787,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Power Rd and McKellips Rd: Mesa",
        "latitude": 33.451652,
        "longitude": -111.684139,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Power Rd and Southern Ave: Mesa",
        "latitude": 33.393984,
        "longitude": -111.684748,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Power Rd and Main St: Mesa",
        "latitude": 33.415668,
        "longitude": -111.684528,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Southern Ave and Higley Rd: Mesa",
        "latitude": 33.393831,
        "longitude": -111.719342,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "Southern Ave and Stapley Dr: Mesa",
        "latitude": 33.393359,
        "longitude": -111.805425,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "University Dr and Mesa Dr: Mesa",
        "latitude": 33.422352,
        "longitude": -111.822881,
        "direction_deg": -1,
        "type": "red_light_speed"
    },
    {
        "name": "W/B, Rhodes Jr High on Baseline Rd: Mesa",
        "latitude": 33.378695,
        "longitude": -111.870875,
        "direction_deg": 270,
        "type": "school_zone",
        "road_axis_deg": 90
    },
    {
        "name": "E/B, Rhodes Jr High on Baseline Rd: Mesa",
        "latitude": 33.378519,
        "longitude": -111.871039,
        "direction_deg": 90,
        "type": "school_zone",
        "road_axis_deg": 90
    },
    {
        "name": "W/B, Brimhall Jr High on Southern Ave: Mesa",
        "latitude": 33.393855,
        "longitude": -111.72467,
        "direction_deg": 270,
        "type": "school_zone",
        "road_axis_deg": 90
    },
    {
        "name": "E/B, Brimhall Jr High on Southern Ave: Mesa",
        "latitude": 33.393689,
        "longitude": -111.724199,
        "direction_deg": 90,
        "type": "school_zone",
        "road_axis_deg": 90
    },
    {
        "name": "S/B, Fremont Jr High on Power Rd: Mesa",
        "latitude": 33.432782,
        "longitude": -111.684456,
        "direction_deg": 180,
        "type": "school_zone",
        "road_axis_deg": 0
    },
    {
        "name": "N/B, Fremont Jr High on Power Rd: Mesa",
        "latitude": 33.434232,
        "longitude": -111.684233,
        "direction_deg": 0,
        "type": "school_zone",
        "road_axis_deg": 0
    },
    {
        "name": "E/B, Mesa High on Southern Ave: Mesa",
        "latitude": 33.393507,
        "longitude": -111.794087,
        "direction_deg": 90,
        "type": "school_zone",
        "road_axis_deg": 90
    },
    {
        "name": "W/B, Mesa High on Southern Ave: Mesa",
        "latitude": 33.393527,
        "longitude": -111.79487,
        "direction_deg": 270,
        "type": "school_zone",
        "road_axis_deg": 90
    },
    {
        "name": "W/B, Red Mountain High on Brown Rd: Mesa",
        "latitude": 33.436936,
        "longitude": -111.674937,
        "direction_deg": 270,
        "type": "school_zone",
        "road_axis_deg": 90
    },
    {
        "name": "E/B, Red Mountain High on Brown Rd: Mesa",
        "latitude": 33.436888,
        "longitude": -111.673477,
        "direction_deg": 90,
        "type": "school_zone",
        "road_axis_deg": 90
    },
    {
        "name": "N/B, Porter Elementary on Lindsay Rd: Mesa",
        "latitude": 33.389825,
        "longitude": -111.771171,
        "direction_deg": 0,
        "type": "school_zone",
        "road_axis_deg": 0
    },
    {
        "name": "S/B, Porter Elementary on Lindsay Rd: Mesa",
        "latitude": 33.391413,
        "longitude": -111.771277,
        "direction_deg": 180,
        "type": "school_zone",
        "road_axis_deg": 0
    },
    {
        "name": "N/B, Skyline High on Crismon Rd: Mesa",
        "latitude": 33.398308,
        "longitude": -111.61533,
        "direction_deg": 0,
        "type": "school_zone",
        "road_axis_deg": 0
    },
    {
        "name": "S/B, Skyline High on Crismon Rd: Mesa",
        "latitude": 33.398933,
        "longitude": -111.615485,
        "direction_deg": 180,
        "type": "school_zone",
        "road_axis_deg": 0
    }
]

def get_direction(text):
    text_upper = text.upper()
    for key, deg in DIRECTION_MAP.items():
        if re.search(rf'\b{re.escape(key)}\b', text_upper):
            return deg
    return None


def maps_links(lat, lon):
    """Pin + Street View links for a human's side-of-street check."""
    return (f"pin https://www.google.com/maps/search/?api=1&query={lat},{lon} · "
            f"Street View https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lon}")


SIDE_RULE = "pole on the N side -> W/B 270, S -> E/B 90, E -> N/B 0, W -> S/B 180"


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_placemarks(soup, notes):
    """Turn the KML into camera dicts. Returns (cameras, placemarks): the
    cameras as the app will see them (overrides applied) and the raw
    placemarks as the city drew them (for the map snapshot). `notes` collects
    lines that need a human (override drift) for the map alert."""
    cameras = []
    placemarks = []
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
        token_direction = get_direction(desc)
        direction_deg = token_direction
        if direction_deg is None:
            direction_deg = NAME_DIRECTION_OVERRIDES.get(clean_name)
        if direction_deg is None:
            print(f"  No direction found, marking omnidirectional: {clean_name}")
            direction_deg = OMNIDIRECTIONAL

        placemarks.append({
            "name": clean_name,
            "latitude": lat,
            "longitude": lon,
            "direction_token": token_direction is not None,
        })

        camera = {
            "name": clean_name,
            "latitude": lat,
            "longitude": lon,
            "direction_deg": direction_deg
        }
        fix = COORDINATE_OVERRIDES.get(clean_name)
        if fix is not None:
            drift = haversine_m(lat, lon, fix["city_lat"], fix["city_lon"])
            if drift > COORDINATE_OVERRIDE_DRIFT_M:
                line = (f"WARNING: city moved {clean_name} by {drift:.0f} m since its coordinate "
                        f"override was written ({fix['since']}): recorded city pin "
                        f"{fix['city_lat']},{fix['city_lon']} -> now {lat},{lon}; the override "
                        f"({fix['lat']},{fix['lon']}) still applies and may be stale — re-check the pole")
                print(f"  {line}")
                notes.append(line)
            print(f"  Coordinate override: {clean_name} {lat},{lon} -> {fix['lat']},{fix['lon']}")
            camera["latitude"], camera["longitude"] = fix["lat"], fix["lon"]
        road_axis = ROAD_AXIS_OVERRIDES.get(clean_name)
        if road_axis is not None:
            camera["road_axis_deg"] = road_axis
        cameras.append(camera)
    return cameras, placemarks


def kept_cameras(placemarks, notes):
    """KEEP_UNTIL_VERIFIED entries not already on the map (within KEEP_DEDUPE_M
    of a scraped placemark), stripped of their provenance keys."""
    kept = []
    for entry in KEEP_UNTIL_VERIFIED:
        near = [pm for pm in placemarks
                if haversine_m(entry["latitude"], entry["longitude"],
                               pm["latitude"], pm["longitude"]) <= KEEP_DEDUPE_M]
        if near:
            line = (f"KEEP entry skipped: the map again carries a placemark within {KEEP_DEDUPE_M} m "
                    f"of {entry['name']!r} — {near[0]['name']!r} at {near[0]['latitude']},{near[0]['longitude']}. "
                    f"The scraped entry is published; carry its direction/axis to the new name by hand "
                    f"and retire the KEEP entry")
            print(f"  {line}")
            notes.append(line)
            continue
        print(f"  Keeping (page/map disagreement, unverified): {entry['name']}")
        kept.append({k: v for k, v in entry.items() if not k.startswith("_")})
    return kept


def load_snapshot():
    if not os.path.exists(PLACEMARK_SNAPSHOT):
        return None
    with open(PLACEMARK_SNAPSHOT, encoding="utf-8") as f:
        return json.load(f)


def diff_placemarks(previous, current):
    """Human-readable lines for every change in the city's own map."""
    lines = []
    prev_by_name = {p["name"]: p for p in previous}
    cur_by_name = {p["name"]: p for p in current}
    for name in sorted(set(prev_by_name) - set(cur_by_name)):
        p = prev_by_name[name]
        # a renamed pin keeps its coordinate — say so, it is the override re-key case
        same_spot = [c for c in current
                     if haversine_m(p["latitude"], p["longitude"], c["latitude"], c["longitude"]) <= PLACEMARK_MOVE_M]
        if same_spot:
            lines.append(f"- RENAMED  {name!r} -> {same_spot[0]['name']!r} (same pin; re-key any override)")
        else:
            lines.append(f"- REMOVED  {name!r} ({p['latitude']},{p['longitude']})")
    for name in sorted(set(cur_by_name) - set(prev_by_name)):
        c = cur_by_name[name]
        was = [p for p in previous
               if haversine_m(p["latitude"], p["longitude"], c["latitude"], c["longitude"]) <= PLACEMARK_MOVE_M]
        if was:
            continue  # reported above as RENAMED
        if c["direction_token"]:
            lines.append(f"- ADDED    {name!r} ({c['latitude']},{c['longitude']}) with a direction token")
        else:
            lines.append(f"- ADDED    {name!r} ({c['latitude']},{c['longitude']}) WITHOUT a direction token "
                         f"-> omnidirectional until a human reads the POLE's side ({SIDE_RULE}); "
                         f"{maps_links(c['latitude'], c['longitude'])}")
    for name in sorted(set(cur_by_name) & set(prev_by_name)):
        p, c = prev_by_name[name], cur_by_name[name]
        moved = haversine_m(p["latitude"], p["longitude"], c["latitude"], c["longitude"])
        if moved > PLACEMARK_MOVE_M:
            lines.append(f"- MOVED    {name!r} by {moved:.0f} m: {p['latitude']},{p['longitude']} -> {c['latitude']},{c['longitude']}")
        if p["direction_token"] and not c["direction_token"]:
            lines.append(f"- DIRECTION TOKEN LOST on {name!r} (now omnidirectional unless NAME_DIRECTION_OVERRIDES names it; "
                         f"read the POLE's side: {SIDE_RULE}; {maps_links(c['latitude'], c['longitude'])})")
        if c["direction_token"] and not p["direction_token"]:
            lines.append(f"- DIRECTION TOKEN GAINED on {name!r}")
    return lines


def write_map_alert(sections):
    if not sections:
        if os.path.exists(MAP_ALERT):
            os.unlink(MAP_ALERT)
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    body = [f"# Phoenix map (KML) changed — {stamp}", "",
            f"Source: {KML_URL}", "",
            "The updater has ALREADY PUBLISHED the new placemarks to camera_data.json "
            "(the file is the roster). A human now checks: did the city add, drop, move or "
            "relabel a camera? Re-key NAME_DIRECTION_OVERRIDES / ROAD_AXIS_OVERRIDES / "
            "COORDINATE_OVERRIDES for any renamed placemark, and apply the standing rules — "
            "a camera the city's PAGE still lists stays in KEEP_UNTIL_VERIFIED until a "
            "ground check says it is gone, and a placemark with no direction token gets its "
            "direction from the POLE's side of the street (" + SIDE_RULE + "), read in Street "
            "View — never from the pin itself when the pin is a corridor position.", ""]
    body += sections
    with open(MAP_ALERT, "w", encoding="utf-8") as f:
        f.write("\n".join(body) + "\n")
    print("\nMAP ALERT:\n" + "\n".join(sections))


def update_camera_data():
    print(f"Fetching: {KML_URL}")
    try:
        response = requests.get(KML_URL, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "xml")
    except Exception as e:
        print(f"Failed to fetch KML: {e}")
        return

    notes = []
    cameras, placemarks = parse_placemarks(soup, notes)

    if not cameras:
        print("No valid camera locations found.")
        return

    # Kept and manual cities ride along AFTER the empty-scrape guard: a failed
    # or empty Phoenix scrape still never writes, and a good one always
    # carries the hand-curated entries.
    cameras.extend(kept_cameras(placemarks, notes))
    cameras.extend(MANUAL_CAMERAS)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(cameras, f, indent=4)

    print(f"Success! Saved {len(cameras)} cameras to {OUTPUT_JSON}.")

    # The map watch: compare the city's own placemarks with the committed
    # snapshot, then record the new snapshot. First run writes the baseline.
    sections = []
    previous = load_snapshot()
    if previous is None:
        print(f"No placemark snapshot yet — writing the baseline to {PLACEMARK_SNAPSHOT}.")
    else:
        changes = diff_placemarks(previous, placemarks)
        if changes:
            sections.append("## Placemarks changed")
            sections.extend(changes)
            sections.append("")
    if notes:
        sections.append("## Overrides and kept entries needing a look")
        sections.extend(f"- {n}" for n in notes)
        sections.append("")
    os.makedirs(os.path.dirname(PLACEMARK_SNAPSHOT), exist_ok=True)
    with open(PLACEMARK_SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(placemarks, f, indent=2)
        f.write("\n")
    write_map_alert(sections)


if __name__ == "__main__":
    update_camera_data()
