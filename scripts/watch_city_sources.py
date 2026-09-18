#!/usr/bin/env python3
"""Nightly change detector for every NON-Phoenix camera source the app's
roster was transcribed from (and the Phoenix school-zone schedule page).

Phoenix's own corridor list is rebuilt from the city's map every night by
update_cameras.py because that map carries coordinates. The other cities
publish text lists and PDFs with no coordinates, so their entries cannot be
rewritten automatically -- a new intersection has to be geocoded, pinned
and, where the city states an approach, given a measured heading. What CAN
run unattended is noticing: this script fetches each official source,
extracts the location-like lines, compares them with the last snapshot in
watch/, and records any difference so a human runs the pipeline
(drafts/city_rosters.json -> build_city_drafts.py -> stage_city.py).

It never touches camera_data.json.

    python3 scripts/watch_city_sources.py            # fetch, compare, update watch/
    python3 scripts/watch_city_sources.py --report   # print the last run's report

Outputs (all under watch/):
    <key>.txt      the location lines last seen at that source (+ header)
    state.json     per-source status, failure streak, hashes, timestamps
    REPORT.md      the latest run, human readable
    ALERT.md       written ONLY when something needs a human (a source's
                   location lines changed, or a source has failed 3 nights
                   running); the workflow turns it into a GitHub issue
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WATCH = os.path.join(ROOT, "watch")
STATE = os.path.join(WATCH, "state.json")
REPORT = os.path.join(WATCH, "REPORT.md")
ALERT = os.path.join(WATCH, "ALERT.md")
FAIL_STREAK_ALERT = 3
# Bump whenever the extraction rules change: the next run re-baselines every
# source silently instead of reporting the rule change as a city change.
EXTRACTOR_VERSION = "2026-09-18.4"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA,
           "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}

# key, city, url, kind. Keys are stable: they name the snapshot files.
SOURCES = [
    ("phoenix_school_zones", "Phoenix (school-zone schedule)",
     "https://www.phoenix.gov/administration/departments/streets/safety-improvements/road-safety-action-plan/photo-safety.html", "html"),
    ("tempe", "Tempe",
     "https://www.tempe.gov/government/transportation-and-sustainability/transportation/photo-enforcement", "html"),
    ("scottsdale_page", "Scottsdale (police page)",
     "https://www.scottsdaleaz.gov/police/police-units/photo-enforcement", "html"),
    ("scottsdale_pdf", "Scottsdale (fixed-locations PDF)",
     "https://ww2.scottsdaleaz.gov/Assets/ScottsdaleAZ/Police/Photoradar/photo-enforcement-fixed-camera-locations.pdf", "pdf"),
    ("paradise_valley", "Paradise Valley",
     "https://www.paradisevalleyaz.gov/153/Photo-Radar", "html"),
    ("chandler_page", "Chandler (program page)",
     "https://www.chandleraz.gov/residents/transportation/traffic-engineering/red-light-photo-enforcement", "html"),
    ("chandler_pdf", "Chandler (camera map PDF)",
     "https://www.chandleraz.gov/sites/default/files/departments/development-services/City-of-Chandler-Photo-Enforcement-Camera-Map.pdf", "pdf"),
    ("mesa", "Mesa",
     "https://www.mesaaz.gov/Public-Safety/Mesa-Police/About-Mesa-Police/Photo-Safety-Program", "html"),
]

ROAD = r"(?:Rd|Road|Ave|Avenue|Blvd|Boulevard|Dr|Drive|St|Street|Ln|Lane|Pkwy|Parkway|Loop|Way|Hwy|Highway|Pl|Place|Trl|Trail)"
JOIN = r"(?:\s+(?:and|&|at|@)\s+|\s*/\s*)"
LOCATION_PATTERNS = [
    # "Scottsdale Rd at McDowell Rd", "Alma School Road and Queen Creek Road", "Broadway/Stapley"
    re.compile(rf"\b[A-Z0-9][\w'.\-]*(?:\s+[A-Z0-9][\w'.\-]*)*\s*{ROAD}\.?{JOIN}[A-Z0-9][\w'.\-]*(?:\s+[A-Z0-9][\w'.\-]*)*\s*{ROAD}\.?", re.I),
    # Mesa-style "Alma School/Guadalupe", "Power/Main"
    re.compile(r"^[A-Z][\w'.]*(?: [A-Z][\w'.]*){0,2}/[A-Z][\w'.]*(?: [A-Z][\w'.]*){0,2}$"),
    # direction-labelled rows "... | S/B", "E/B and W/B", "northbound"
    re.compile(r"\b(?:N/B|S/B|E/B|W/B|northbound|southbound|eastbound|westbound)\b", re.I),
    # school corridors and schedule rows
    re.compile(r"\b(?:Jr\.?\s*High|Junior High|High School|Elementary|Middle School)\b", re.I),
    # Phoenix corridor naming "W/B, Bell Rd: I-17 to 19th Ave" / "16th Street to 18th Street"
    re.compile(rf"\b{ROAD}\.?\s*:\s*.+\bto\b", re.I),
    re.compile(r"\b\d+(?:st|nd|rd|th)\s+(?:St|Street|Ave|Avenue)\b.*\bto\b", re.I),
]
LINK_PATTERN = re.compile(r"\.pdf(?:\?|$)", re.I)
# A direction phrase, and the cut point after one: Paradise Valley lists all
# six of its sites in ONE text node ("... Rd.-both E/B and W/B E. Lincoln Dr.
# and Tatum Blvd-All four directions E. Lincoln Dr. and ..."), so a run-on
# line is split after each direction phrase where a new capitalised road
# name starts.
DIRECTION_PHRASE = re.compile(r"\b(?:[NSEW]/B|(?:north|south|east|west)bound|all four directions|both directions)\b", re.I)
DIRECTION_CUT = re.compile(r"(\b(?:[NSEW]/B|(?:north|south|east|west)bound|directions|only)\b\.?)\s+(?=[A-Z])")
# Stated counts ("14 intersections", "four mobile cameras", "Camera (12)") --
# the only text signal on a page that draws its locations on a map image
# (Tempe) or a PDF with no text rows (Chandler's map).
NUMBER = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)"
COUNT_PATTERNS = [
    re.compile(rf"\b{NUMBER}\s+(?:[A-Za-z]+\s+){{0,2}}?(?:intersections?|cameras?|locations?|sites?|schools?|corridors?)\b", re.I),
    re.compile(r"\bcameras?\s*\(\s*\d+\s*\)", re.I),
]
# Every extras key that, on change, needs a human even when the location
# lines held (a re-issued map image, a new schedule PDF, a count that moved).
SIGNAL_KEYS = ("links", "map_images", "pdf_sha", "counts")


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _clients():
    """Fetch clients in trust order. curl_cffi impersonates a real Chrome TLS
    fingerprint, which is what gets past the Akamai/Cloudflare bot filters
    that answer plain python-requests with 403 (Tempe, Mesa on the first
    run); requests is the fallback when curl_cffi is not installed."""
    try:
        from curl_cffi import requests as cffi
        yield "chrome", lambda url: cffi.get(url, headers=HEADERS, timeout=40,
                                             allow_redirects=True, impersonate="chrome")
    except ImportError:
        pass
    import requests
    yield "requests", lambda url: requests.get(url, headers=HEADERS, timeout=40, allow_redirects=True)


def fetch(url, kind):
    """Return (bytes, note); raises after every client has failed twice."""
    last = None
    for name, get in _clients():
        for attempt in range(2):
            try:
                r = get(url)
                if r.status_code == 200 and r.content:
                    return r.content, f"{r.status_code} {len(r.content)}B via {name}"
                last = f"HTTP {r.status_code} via {name}"
            except Exception as error:
                last = f"{type(error).__name__}: {error} via {name}"
            time.sleep(5)
    raise RuntimeError(last)


def html_to_text(raw):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "svg"]):
        tag.decompose()
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        label = a.get_text(" ", strip=True)
        if LINK_PATTERN.search(href) or re.search(r"schedule|rotation|location|map", href + " " + label, re.I):
            links.append(f"{href} [{label[:60]}]")
    imgs = []
    for img in soup.find_all("img"):
        src, alt = img.get("src", ""), img.get("alt", "")
        if src and re.search(r"map|radar|camera|enforce|location", src + " " + alt, re.I):
            imgs.append(f"{src} [{alt[:60]}]")
    # table rows read as one line ("Camera Location | Direction" tables)
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        tr.replace_with(" | ".join(c for c in cells if c) + "\n")
    text = soup.get_text("\n")
    return text, links, imgs


def pdf_to_text(raw):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(raw)
        path = handle.name
    try:
        out = subprocess.run(["pdftotext", "-layout", path, "-"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0:
            return out.stdout
        return ""
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        os.unlink(path)


def normalize_lines(text):
    """One entry per line; long semicolon lists (Mesa's school corridors are
    one sentence: "...corridor on Power Road; Mesa High School corridor on
    Southern Avenue; ...") are split so each item can match on its own."""
    lines = []
    for raw in text.splitlines():
        for piece in raw.replace("\xa0", " ").split(";"):
            line = re.sub(r"\s+", " ", piece).strip(" \t|-•·.")
            if len(line) >= 4:
                lines.extend(split_runon(line))
    return lines


def split_runon(line):
    """A line too long to be one location row that carries several direction
    phrases is a list glued into one text node (Paradise Valley): cut it
    after each direction phrase where the next capitalised road begins.
    Lines that already fit the row limit are returned untouched."""
    if len(line) <= 160 or len(DIRECTION_PHRASE.findall(line)) < 2:
        return [line]
    parts = DIRECTION_CUT.sub(r"\1\n", line).split("\n")
    out = [p.strip(" \t|-•·.") for p in parts]
    return [p for p in out if len(p) >= 4]


def count_phrases(lines):
    """Distinct stated counts on the page, lowercased, in sorted order."""
    found = set()
    for line in lines:
        for pattern in COUNT_PATTERNS:
            for match in pattern.finditer(line):
                found.add(re.sub(r"\s+", " ", match.group(0)).lower())
    return sorted(found)


def location_lines(lines):
    """The subset of lines that look like camera locations, in page order,
    de-duplicated. Table rows keep their ' | ' cells (e.g. '... | S/B')."""
    seen, out = set(), []
    for line in lines:
        if len(line) > 160:
            continue           # paragraphs of prose are not location rows
        if any(p.search(line) for p in LOCATION_PATTERNS):
            key = line.lower()
            if key not in seen:
                seen.add(key)
                out.append(line)
    return out


def extract(raw, kind):
    """(location lines, content sha, extras) for one fetched source."""
    extras = {}
    if kind == "pdf":
        text = pdf_to_text(raw)
        extras["pdf_sha"] = hashlib.sha256(raw).hexdigest()[:16]
        if not text.strip():
            # no text layer (or no pdftotext): the byte hash is the only signal
            return [], extras["pdf_sha"], extras
    else:
        text, links, imgs = html_to_text(raw)
        if links:
            extras["links"] = sorted(set(links))
        if imgs:
            extras["map_images"] = sorted(set(imgs))
    lines = normalize_lines(text)
    content_sha = hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]
    counts = count_phrases(lines)
    if counts:
        extras["counts"] = counts
    extras["_all_lines"] = lines          # debug dump only; stripped before saving
    return location_lines(lines), content_sha, extras


def load_snapshot(key):
    path = os.path.join(WATCH, f"{key}.txt")
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        return [l.rstrip("\n") for l in handle if not l.startswith("#")]


def save_snapshot(key, url, lines, content_sha, extras):
    path = os.path.join(WATCH, f"{key}.txt")
    with open(path, "w") as handle:
        handle.write(f"# {url}\n# fetched {now()}  content_sha {content_sha}\n")
        for k, v in sorted(extras.items()):
            handle.write(f"# {k}: {json.dumps(v)}\n")
        for line in lines:
            handle.write(line + "\n")


def run():
    os.makedirs(WATCH, exist_ok=True)
    state = {}
    if os.path.exists(STATE):
        with open(STATE) as handle:
            state = json.load(handle)
    report, alerts = [f"# City source watch — {now()}", ""], []
    rebaseline = state.get("_extractor_version") != EXTRACTOR_VERSION
    state["_extractor_version"] = EXTRACTOR_VERSION
    if rebaseline:
        report.append(f"_Extractor {EXTRACTOR_VERSION}: every source re-baselined this run, no alerts._\n")
    for key, city, url, kind in SOURCES:
        st = state.setdefault(key, {"failures": 0})
        st["url"] = url
        try:
            raw, note = fetch(url, kind)
        except Exception as error:
            st["failures"] = st.get("failures", 0) + 1
            st["last_status"] = f"FAIL {error}"
            st["last_run"] = now()
            report.append(f"- **{city}**: FETCH FAILED ({error}); {st['failures']} night(s) in a row")
            if st["failures"] == FAIL_STREAK_ALERT:
                alerts.append(f"## {city}: source unreachable {FAIL_STREAK_ALERT} nights running\n{url}\n\n`{error}`\n\n"
                              "Check whether the city moved the page or blocks the runner; a Claude-for-Chrome read is the fallback.")
            time.sleep(2)
            continue
        lines, content_sha, extras = extract(raw, kind)
        debug_dir = os.path.join(WATCH, "_debug")
        os.makedirs(debug_dir, exist_ok=True)
        all_lines = extras.pop("_all_lines", [])
        with open(os.path.join(debug_dir, f"{key}.lines.txt"), "w") as handle:
            handle.write("\n".join(all_lines) + "\n")
        if not lines:
            # nothing matched: show what the page actually says, in the job log,
            # so the rules can be tuned without downloading the artifact
            print(f"[diagnostic] {key}: 0 location lines; page text ({len(all_lines)} lines), first 80:")
            for l in all_lines[:80]:
                print("    " + l[:140])
        prev = load_snapshot(key)
        if rebaseline:
            prev = None
        st["failures"] = 0
        st["last_status"] = f"ok {note}"
        st["last_run"] = st["last_ok"] = now()
        prev_sha = st.get("content_sha")
        prev_extras = st.get("extras") or {}
        st["content_sha"] = content_sha
        st["extras"] = extras
        link_changes = {k: (prev_extras.get(k), extras.get(k)) for k in SIGNAL_KEYS
                        if prev is not None and prev_extras.get(k) != extras.get(k)}
        signals = [k for k in SIGNAL_KEYS if extras.get(k)]
        if prev is None:
            save_snapshot(key, url, lines, content_sha, extras)
            st["last_change"] = now()
            report.append(f"- **{city}**: baseline recorded ({len(lines)} location lines"
                          + (f"; also watching {', '.join(signals)}" if signals else "") + ")")
            continue
        added = [l for l in lines if l.lower() not in {p.lower() for p in prev}]
        removed = [p for p in prev if p.lower() not in {l.lower() for l in lines}]
        if link_changes and not (added or removed):
            # a new schedule PDF, a re-issued map image, a stated count that
            # moved: a human should look even though the location lines held
            save_snapshot(key, url, lines, content_sha, extras)
            st["last_change"] = now()
            body = [f"## {city}: page signals changed (location lines unchanged)\n{url}\n"]
            for k, (before, after) in link_changes.items():
                body.append(f"{k}: was {json.dumps(before)}\n    now {json.dumps(after)}")
            body.append("\nA map image or PDF with a new identity, or a count that moved, usually means "
                        "the roster changed on a page that lists no rows: read the source (Claude for Chrome) "
                        "and compare with drafts/city_rosters.json.")
            alerts.append("\n".join(body))
            report.append(f"- **{city}**: page signals changed ({', '.join(link_changes)})")
            time.sleep(2)
            continue
        if added or removed:
            save_snapshot(key, url, lines, content_sha, extras)
            st["last_change"] = now()
            body = [f"## {city}: location lines CHANGED\n{url}\n"]
            if added:
                body.append("Added:\n" + "\n".join(f"+ {l}" for l in added))
            if removed:
                body.append("Removed:\n" + "\n".join(f"- {l}" for l in removed))
            body.append("\nNext: update drafts/city_rosters.json, geocode (build_city_drafts.py), "
                        "pin/measure as the pipeline requires, then stage_city.py on the tester's go.")
            alerts.append("\n".join(body))
            report.append(f"- **{city}**: CHANGED — +{len(added)} / −{len(removed)} location lines")
        elif prev_sha and content_sha != prev_sha:
            save_snapshot(key, url, lines, content_sha, extras)
            report.append(f"- **{city}**: page text changed, location lines unchanged ({len(lines)})")
        else:
            report.append(f"- **{city}**: unchanged ({len(lines)} location lines)")
        time.sleep(2)
    with open(STATE, "w") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with open(REPORT, "w") as handle:
        handle.write("\n".join(report) + "\n")
    if alerts:
        with open(ALERT, "w") as handle:
            handle.write("\n\n".join(alerts) + "\n")
    elif os.path.exists(ALERT):
        os.unlink(ALERT)
    print("\n".join(report))
    if alerts:
        print("\nALERT:\n" + "\n\n".join(alerts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if args.report:
        print(open(REPORT).read() if os.path.exists(REPORT) else "no run recorded")
        return
    run()


if __name__ == "__main__":
    main()
