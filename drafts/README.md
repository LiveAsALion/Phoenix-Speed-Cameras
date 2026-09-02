# drafts/ — candidate camera rosters (NOT fetched by the app)

`city_rosters.json` holds per-city candidate lists compiled from web
research; `scripts/build_city_drafts.py --geocode` turns them into
`<city>.geocoded.json` review files with sanity-gated coordinates. Nothing
in this directory reaches phones: the app reads only `camera_data.json`
(built nightly from the Phoenix KML + `MANUAL_CAMERAS` in
`update_cameras.py`) and `school_zone_cameras.json`.

Go-live path per city: adjudicate the roster against the official source
listed in it → geocode → tester pin-drops flagged rows → paste verified
entries into `MANUAL_CAMERAS`. Full record and sources:
`SpeedShield/docs/VALLEY_CITIES_CAMERAS.md`.
