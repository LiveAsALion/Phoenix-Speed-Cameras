#!/usr/bin/env python3
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_FILE = ROOT / "camera_data.json"
ARCHIVE_DIR = ROOT / "archive" / "camera_data"
MANIFEST_FILE = ARCHIVE_DIR / "manifest.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest() -> list:
    if not MANIFEST_FILE.exists():
        return []
    return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))


def save_manifest(rows: list) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def archive_current_camera_data() -> None:
    if not ACTIVE_FILE.exists():
        print(f"No active file to archive at {ACTIVE_FILE}.")
        return

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now.isoformat().replace('+00:00', 'Z')
    current_hash = sha256_file(ACTIVE_FILE)
    manifest = load_manifest()

    if manifest and manifest[-1].get("sha256") == current_hash:
        if not manifest[-1].get("valid_to"):
            manifest[-1]["valid_to"] = now_iso
            save_manifest(manifest)
            print("Closed validity window for current active camera_data.json in manifest.")
        else:
            print("Latest manifest entry already matches active file and has a closed validity window.")
        return

    filename = f"camera_data__through_{now.strftime('%Y-%m-%dT%H-%M-%SZ')}.json"
    archive_path = ARCHIVE_DIR / filename
    archive_path.write_bytes(ACTIVE_FILE.read_bytes())

    if manifest and not manifest[-1].get("valid_to"):
        manifest[-1]["valid_to"] = now_iso

    entry = {
        "file": f"archive/camera_data/{filename}",
        "sha256": current_hash,
        "valid_from": manifest[-1]["valid_to"] if manifest else None,
        "valid_to": None,
        "archived_at": now_iso,
        "source_active_file": "camera_data.json"
    }
    manifest.append(entry)
    save_manifest(manifest)
    print(f"Archived current camera_data.json to {archive_path}.")


if __name__ == "__main__":
    archive_current_camera_data()
