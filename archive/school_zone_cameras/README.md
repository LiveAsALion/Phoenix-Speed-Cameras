# school_zone_cameras.json archive

This directory preserves every superseded `school_zone_cameras.json` file before a new active file replaces it.

## Rules
- `school_zone_cameras.json` is the active file.
- Before the update job writes a new `school_zone_cameras.json`, it must archive the current active file here.
- `manifest.json` records the catalog history, SHA-256 hash, and validity window metadata for each archived file.
- Filenames use UTC archive timestamps: `school_zone_cameras__through_YYYY-MM-DDTHH-MM-SSZ.json`.

## Validity semantics
- `valid_from`: when this file became the active source, if known.
- `valid_to`: when this file stopped being the active source.
- `archived_at`: when the archival copy was created.

If an earlier validity start is unknown, `valid_from` may be `null` until an explicit backfill is done.
