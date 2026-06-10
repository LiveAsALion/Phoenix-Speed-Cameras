#!/usr/bin/env python3
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path) -> list:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def archive_json(active_rel: str, archive_rel_dir: str) -> None:
    active_file = ROOT / active_rel
    archive_dir = ROOT / archive_rel_dir
    manifest_file = archive_dir / "manifest.json"

    if not active_file.exists():
        print(f"No active file to archive at {active_file}.")
        return

    archive_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now.isoformat().replace('+00:00', 'Z')
    current_hash = sha256_file(active_file)
    manifest = load_manifest(manifest_file)

    if manifest and manifest[-1].get("sha256") == current_hash:
        if not manifest[-1].get("valid_to"):
            manifest[-1]["valid_to"] = now_iso
            save_manifest(manifest_file, manifest)
            print(f"Closed validity window for {active_rel} in manifest.")
        else:
            print(f"Latest manifest entry already matches {active_rel} and has a closed validity window.")
        return

    stem = Path(active_rel).stem
    filename = f"{stem}__through_{now.strftime('%Y-%m-%dT%H-%M-%SZ')}.json"
    archive_path = archive_dir / filename
    archive_path.write_bytes(active_file.read_bytes())

    if manifest and not manifest[-1].get("valid_to"):
        manifest[-1]["valid_to"] = now_iso

    entry = {
        "file": f"{archive_rel_dir}/{filename}",
        "sha256": current_hash,
        "valid_from": manifest[-1]["valid_to"] if manifest else None,
        "valid_to": None,
        "archived_at": now_iso,
        "source_active_file": active_rel
    }
    manifest.append(entry)
    save_manifest(manifest_file, manifest)
    print(f"Archived current {active_rel} to {archive_path}.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: archive_json_snapshot.py <active_rel_path> <archive_rel_dir>")
        raise SystemExit(2)
    archive_json(sys.argv[1], sys.argv[2])
