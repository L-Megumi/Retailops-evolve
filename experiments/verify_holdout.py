from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify holdout data and frozen source hashes.")
    parser.add_argument("--tasks", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.tasks / "manifest.json").read_text(encoding="utf-8"))
    project_root = Path(__file__).resolve().parents[1]
    mismatches: list[str] = []

    for relative, expected in manifest["file_sha256"].items():
        path = args.tasks / relative
        if not path.is_file() or sha256(path) != expected:
            mismatches.append(f"data:{relative}")
    for relative, expected in manifest["frozen_source_sha256"].items():
        path = project_root / relative
        if not path.is_file() or sha256(path) != expected:
            mismatches.append(f"source:{relative}")

    print(
        f"data_files={len(manifest['file_sha256'])}, "
        f"source_files={len(manifest['frozen_source_sha256'])}, "
        f"mismatches={len(mismatches)}"
    )
    for mismatch in mismatches:
        print(mismatch)
    return int(bool(mismatches))


if __name__ == "__main__":
    raise SystemExit(main())
