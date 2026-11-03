from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from .data import load_orders, load_refunds
from .models import ExecutionPolicy
from .tools import execute_tool
from .validators import validate_results


def _json_default(value: object):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def execute_policy(
    *,
    orders_path: Path,
    refunds_path: Path,
    task_path: Path,
    output_path: Path,
    policy: ExecutionPolicy,
) -> dict[str, object]:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    tools = tuple(task["tools"])
    orders = load_orders(orders_path)
    refunds = load_refunds(refunds_path)
    results = {name: execute_tool(name, orders, refunds, policy) for name in tools}
    metric_contract = task.get("metric_contract")
    issues = validate_results(orders, refunds, results, metric_contract)
    payload: dict[str, object] = {
        "task": task["instruction"],
        "ok": not issues,
        "policy": policy.to_dict(),
        "metric_contract": metric_contract,
        "issues": [asdict(issue) for issue in issues],
        "results": {name: result.data for name, result in results.items()},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return payload

