from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from retailops_agent.strategy_library import StrategyLibrary

try:
    from .run_rsi_evolution import evaluate_library
except ImportError:  # pragma: no cover - direct script execution
    from run_rsi_evolution import evaluate_library


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a frozen strategy library without proposing or adopting changes."
    )
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument(
        "--split",
        choices=["regression", "evolution", "validation", "transfer"],
        default="transfer",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    library = StrategyLibrary.read(args.library)
    evaluation = evaluate_library(args.suite, args.split, library)
    library_hash = hashlib.sha256(args.library.read_bytes()).hexdigest()
    payload = {
        "suite": args.suite.name,
        "split": args.split,
        "library_path": str(args.library),
        "library_file_sha256": library_hash,
        "library_version": library.version,
        "passed": evaluation.passed,
        "task_count": evaluation.task_count,
        "model_calls": 0,
        "library_mutated": False,
        "rows": list(evaluation.rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"frozen_library_v{library.version}={evaluation.passed}/{evaluation.task_count}, "
        f"calls=0 -> {args.output}"
    )
    return int(evaluation.passed != evaluation.task_count)


if __name__ == "__main__":
    raise SystemExit(main())
