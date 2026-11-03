from __future__ import annotations

import argparse
import json
from pathlib import Path

from retailops_agent.plugin_manager import PluginManager


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the activated metric plugin has no deterministic legacy collisions."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--legacy-suite", type=Path, required=True)
    parser.add_argument("--multi-audit", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manager = PluginManager.from_manifest(args.manifest)
    manager.manifest_path = None
    record = next(
        record
        for record in manager.records
        if record.plugin_id == "refund-replay-rate-metric-v1"
    )
    collisions = []
    for task_path in sorted((args.legacy_suite / "tasks").rglob("task.json")):
        task = json.loads(task_path.read_text(encoding="utf-8"))
        if record.registration.matches(str(task["instruction"])):
            collisions.append(str(task_path.parent.relative_to(args.legacy_suite)))
    multi_collisions = []
    for audit in args.multi_audit:
        cases = json.loads((audit / "cases.json").read_text(encoding="utf-8"))
        for case in cases:
            if record.registration.matches(str(case["instruction"])):
                multi_collisions.append(f"{audit.name}/{case['id']}")
    result = {
        "accepted": not collisions and not multi_collisions,
        "active_plugin_count": sum(
            record.status == "active" for record in manager.records
        ),
        "legacy_task_count": len(
            list((args.legacy_suite / "tasks").rglob("task.json"))
        ),
        "legacy_deterministic_collisions": collisions,
        "multi_audit_deterministic_collisions": multi_collisions,
        "metric_plugin_sha256": record.registration.source_sha256,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"metric_plugin_regression_accepted={result['accepted']}, "
        f"legacy_collisions={len(collisions)}, multi_collisions={len(multi_collisions)} "
        f"-> {args.output}"
    )
    return int(not result["accepted"])


if __name__ == "__main__":
    raise SystemExit(main())
