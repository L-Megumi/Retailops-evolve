from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from retailops_agent.plugin_sandbox import validate_plugin_source

try:
    from .run_code_operator_experiment import evaluate_split
except ImportError:  # pragma: no cover - direct script execution
    from run_code_operator_experiment import evaluate_split


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a frozen AST-restricted code plugin without model calls."
    )
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument(
        "--splits",
        nargs="+",
        choices=["evolution", "validation", "transfer"],
        default=["transfer"],
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.plugin.read_text(encoding="utf-8")
    validate_plugin_source(source)
    evaluations = {
        split: evaluate_split(args.suite, split, source) for split in args.splits
    }
    payload = {
        "suite": args.suite.name,
        "plugin": str(args.plugin),
        "plugin_sha256": hashlib.sha256(args.plugin.read_bytes()).hexdigest(),
        "model_calls": 0,
        "plugin_mutated": False,
        "evaluations": evaluations,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    scores = ", ".join(
        f"{split}={result['passed']}/{result['task_count']}"
        for split, result in evaluations.items()
    )
    print(f"frozen_plugin {scores}, calls=0 -> {args.output}")
    return int(
        any(result["passed"] != result["task_count"] for result in evaluations.values())
    )


if __name__ == "__main__":
    raise SystemExit(main())
