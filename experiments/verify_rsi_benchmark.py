from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

try:
    from .generate_holdout import calculate_expected, sha256
except ImportError:  # pragma: no cover - direct script execution
    from generate_holdout import calculate_expected, sha256

try:
    from .generate_rsi_benchmark import calculate_capability_expected
except ImportError:  # pragma: no cover - direct script execution
    from generate_rsi_benchmark import calculate_capability_expected


def _read_csv(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def verify_suite(root: Path) -> list[str]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    mismatches: list[str] = []
    for group in ("public_sha256", "oracle_sha256"):
        for relative, expected_hash in manifest[group].items():
            path = root / relative
            if not path.is_file() or sha256(path) != expected_hash:
                mismatches.append(f"hash:{relative}")

    seen: set[tuple[str, str]] = set()
    for task_path in sorted((root / "tasks").glob("*/*/task.json")):
        split = task_path.parents[1].name
        case_id = task_path.parent.name
        seen.add((split, case_id))
        task = json.loads(task_path.read_text(encoding="utf-8"))
        if "metric_contract" in task:
            mismatches.append(f"oracle_leak:{split}/{case_id}/task.json")
        if (task_path.parent / "expected.json").exists():
            mismatches.append(f"oracle_leak:{split}/{case_id}/expected.json")
        oracle_path = root / "oracle" / split / f"{case_id}.json"
        if not oracle_path.is_file():
            mismatches.append(f"oracle_missing:{split}/{case_id}")
            continue
        oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
        if oracle.get("case_id") != case_id or oracle.get("split") != split:
            mismatches.append(f"identity:{split}/{case_id}")
        orders = _read_csv(task_path.parent / "orders.csv")
        refunds = _read_csv(task_path.parent / "refunds.csv")
        if oracle.get("capability_contract"):
            recalculated = calculate_capability_expected(
                orders,
                refunds,
                list(task["tools"]),
                oracle["metric_contract"],
                oracle["capability_contract"],
            )
        else:
            recalculated = calculate_expected(
                orders,
                refunds,
                list(task["tools"]),
                oracle["metric_contract"],
            )
        normalized = json.loads(json.dumps(recalculated, default=str))
        if normalized != oracle["results"]:
            mismatches.append(f"oracle_result:{split}/{case_id}")

    expected_cases = {
        (str(case["split"]), str(case["case_id"])) for case in manifest["cases"]
    }
    if seen != expected_cases:
        for missing in sorted(expected_cases - seen):
            mismatches.append(f"task_missing:{missing[0]}/{missing[1]}")
        for extra in sorted(seen - expected_cases):
            mismatches.append(f"task_extra:{extra[0]}/{extra[1]}")
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify RSI benchmark hashes, isolation and oracle.")
    parser.add_argument(
        "--suite", type=Path, default=Path("benchmarks/retailops/rsi-v1")
    )
    args = parser.parse_args()
    mismatches = verify_suite(args.suite)
    manifest = json.loads((args.suite / "manifest.json").read_text(encoding="utf-8"))
    print(
        f"cases={manifest['count']}, public_files={len(manifest['public_sha256'])}, "
        f"oracle_files={len(manifest['oracle_sha256'])}, mismatches={len(mismatches)}"
    )
    for mismatch in mismatches:
        print(mismatch)
    return int(bool(mismatches))


if __name__ == "__main__":
    raise SystemExit(main())
