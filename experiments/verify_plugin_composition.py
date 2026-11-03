from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from retailops_agent.models import OrderLine, Refund
from retailops_agent.plugin_manager import PluginManager


def _oracle(orders: list[OrderLine], refunds: list[Refund]) -> list[Refund]:
    seen: set[str] = set()
    deduplicated: list[Refund] = []
    for refund in refunds:
        if refund.refund_id not in seen:
            seen.add(refund.refund_id)
            deduplicated.append(refund)
    remaining: dict[str, Decimal] = defaultdict(Decimal)
    for order in orders:
        remaining[order.order_id] += order.gross_amount
    accepted: list[Refund] = []
    for refund in deduplicated:
        amount = min(refund.refund_amount, remaining[refund.order_id])
        if amount <= 0:
            continue
        accepted.append(
            Refund(
                refund.refund_id,
                refund.order_id,
                refund.refund_date,
                amount,
            )
        )
        remaining[refund.order_id] -= amount
    return accepted


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify dedup-then-cap plugin composition.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=2026110367)
    args = parser.parse_args()
    manager = PluginManager.from_manifest(args.manifest)
    manager.manifest_path = None
    rows = []
    for case_index in range(args.count):
        rng = random.Random(args.seed + case_index)
        orders = [
            OrderLine(
                f"C{case_index}-O{index}",
                "2026-11-01",
                "A",
                f"P{index}",
                1,
                Decimal(rng.randint(40, 200)),
            )
            for index in range(1, 5)
        ]
        refunds: list[Refund] = []
        for event_index in range(rng.randint(5, 12)):
            order = rng.choice(orders)
            event = Refund(
                f"C{case_index}-R{event_index}",
                order.order_id,
                f"2026-11-{rng.randint(2, 28):02d}",
                Decimal(rng.randint(10, 160)),
            )
            refunds.append(event)
            refunds.extend(event for _ in range(rng.randint(0, 2)))
        expected = _oracle(orders, refunds)
        application = manager.apply(
            "按 refund_id 去重后应用累计退款上限",
            orders,
            refunds,
        )
        pipeline = [step["plugin_id"] for step in application.metadata["pipeline"]]
        ok = application.refunds == expected and pipeline == [
            "refund-event-dedup-v1",
            "cumulative-refund-cap-v1",
        ]
        rows.append(
            {
                "case_id": f"composition-{case_index + 1:03d}",
                "ok": ok,
                "input_rows": len(refunds),
                "expected_rows": len(expected),
                "actual_rows": len(application.refunds),
                "pipeline": pipeline,
            }
        )
    result = {
        "passed": sum(bool(row["ok"]) for row in rows),
        "count": len(rows),
        "seed": args.seed,
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"composition={result['passed']}/{result['count']} -> {args.output}"
    )
    return int(result["passed"] != result["count"])


if __name__ == "__main__":
    raise SystemExit(main())
