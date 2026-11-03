from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

try:
    from .generate_code_benchmark import cap_refunds
    from .generate_holdout import calculate_expected, sha256
except ImportError:  # pragma: no cover - direct script execution
    from generate_code_benchmark import cap_refunds
    from generate_holdout import calculate_expected, sha256


def _rows(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def verify(root: Path) -> list[str]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    mismatches: list[str] = []
    for group in ("public_sha256", "oracle_sha256"):
        for relative, expected_hash in manifest[group].items():
            path = root / relative
            if not path.is_file() or sha256(path) != expected_hash:
                mismatches.append(f"hash:{relative}")
    for task_path in sorted((root / "tasks").glob("*/*/task.json")):
        split = task_path.parents[1].name
        case_id = task_path.parent.name
        task = json.loads(task_path.read_text(encoding="utf-8"))
        if "metric_contract" in task or (task_path.parent / "expected.json").exists():
            mismatches.append(f"oracle_leak:{split}/{case_id}")
        oracle = json.loads(
            (root / "oracle" / split / f"{case_id}.json").read_text(encoding="utf-8")
        )
        orders = _rows(task_path.parent / "orders.csv")
        refunds = _rows(task_path.parent / "refunds.csv")
        expected = calculate_expected(
            orders,
            cap_refunds(orders, refunds),
            list(task["tools"]),
            oracle["metric_contract"],
        )
        normalized = json.loads(json.dumps(expected, default=str))
        if normalized != oracle["results"]:
            mismatches.append(f"oracle:{split}/{case_id}")
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the code-operator benchmark.")
    parser.add_argument(
        "--suite", type=Path, default=Path("benchmarks/retailops/code-evolution-v1")
    )
    args = parser.parse_args()
    mismatches = verify(args.suite)
    manifest = json.loads((args.suite / "manifest.json").read_text(encoding="utf-8"))
    print(f"cases={manifest['count']}, mismatches={len(mismatches)}")
    for mismatch in mismatches:
        print(mismatch)
    return int(bool(mismatches))


if __name__ == "__main__":
    raise SystemExit(main())
