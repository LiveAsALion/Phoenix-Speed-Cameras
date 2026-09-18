# City source watch

Nightly snapshots of every official camera source the roster was
transcribed from, written by `scripts/watch_city_sources.py` (workflow
`watch_city_sources.yml`, 01:30 UTC). One `<key>.txt` per source holds the
location-like lines last seen there; `state.json` holds status and failure
streaks; `REPORT.md` is the latest run. When a source's location lines
change, or a source fails three nights running, the workflow opens a
GitHub issue labelled `camera-source-watch`.

This is DETECTION, not publication: nothing here touches
`camera_data.json`. A flagged change goes through the normal pipeline
(`drafts/city_rosters.json` → `build_city_drafts.py --geocode` → tester
pins / bearing measurement → `stage_city.py` on the tester's go).

## What each source yields

Not every city publishes a text list. Where the page has rows, the rows
are the signal; where it only draws a map, the map's identity and any
stated count are what can be watched, and a change there means a human
reads the page (Claude for Chrome) and compares it with
`drafts/city_rosters.json`.

| Source | Location lines | Other signals watched |
|---|---|---|
| Phoenix (school-zone schedule page) | corridor rows (9) | links, map images |
| Tempe | none — the list is a map IMAGE | map image identity (`Photo Enforcement Map_<date>`), stated counts ("14 intersections", "four mobile cameras"), page text hash |
| Scottsdale (police page) | `Road at Road \| S/B` table rows (11) | links, map images |
| Scottsdale (fixed-locations PDF) | rows (11) | PDF byte hash |
| Paradise Valley | six sites, published as ONE text node and split after each direction phrase | map image identity (`PV PE Map <year>`) |
| Chandler (program page) | intersection rows (12) | links (map PDF), map images |
| Chandler (camera map PDF) | none — a drawn map | PDF byte hash, legend count (`Camera (12)`) |
| Mesa | intersections (18) + school corridors | links (season PDFs), stated counts |

`content_sha` (the page text hash) changes are reported but do not open an
issue: cities edit prose constantly. Location lines, links, map images
and PDF hashes do; stated counts are recorded on every page but only open
an issue on a page that has NO rows (Tempe, Chandler's map PDF) — on a
page with rows the rows are the signal, and the prose counts there are
programme history ("21 red light cameras" in Mesa's background text).

Link and image URLs are compared with their query parameters SORTED:
the image CDN behind phoenix.gov serves `?quality=85&preferwebp=true`
and `?preferwebp=true&quality=85` on alternate fetches (issue #2 was
that, a false alarm).

A 200 answer with almost no text (fewer than 10 usable lines) is a
bot-challenge page, not the source — Tempe served one on run 5 (27 KB
against 124 KB). It is retried as Chrome, then as Safari and Firefox
(curl_cffi impersonation), then plain requests; failing all, it is a
failed night and never becomes a baseline, so the real page the next
night cannot read as a change. Three failed nights running open an
issue — counted as distinct UTC days, so re-running the workflow by hand
several times in one day cannot inflate the streak (Tempe answered every
browser shape with the stub during the 2026-09-18 burst of manual runs:
its filter is rate- or address-based, not fingerprint-based).

Changing the extraction rules: bump `EXTRACTOR_VERSION` in the script. The
next run re-records every baseline silently; without the bump, the rule
change itself would be reported as a city change (issue #1 was that).
A source that yields no location lines prints its page text into the job
log so the rules can be tuned against the real page.
