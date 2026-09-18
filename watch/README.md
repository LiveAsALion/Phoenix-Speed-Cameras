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
