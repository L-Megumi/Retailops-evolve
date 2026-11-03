from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness2.core.schedule import run_domain

from retailops_agent.harness2_adapter import (
    DeterministicRetailOpsProposer,
    RetailOpsHarnessAdapter,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("runs/harness2-demo"))
    parser.add_argument(
        "--tasks", type=Path, default=Path("benchmarks/retailops/tasks")
    )
    parser.add_argument("--k", type=int, default=1)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    adapter = RetailOpsHarnessAdapter(
        args.output,
        args.tasks,
        project_root,
    )
    proposer = DeterministicRetailOpsProposer(adapter.spec.channels["proposer"])
    result = run_domain(
        "retail-analytics",
        adapter.tasks(),
        args.k,
        adapter=adapter,
        prop=proposer,
        runs_root=adapter.spec.runs_root,
        library_root=args.output / "library" / f"k{args.k}",
    )
    result["execution_count"] = adapter.execution_count
    result["editor_calls"] = len(proposer._calls)
    report = args.output / "demo.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(report)
    return int(result["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())

