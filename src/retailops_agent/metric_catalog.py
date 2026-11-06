"""Trusted metric definitions. Model-generated code never supplies its own judge."""
from __future__ import annotations

import hashlib
import json
import random
import inspect
from dataclasses import dataclass
from typing import Callable


Rows = list[dict[str, object]]


def _replay(orders: Rows, refunds: Rows) -> dict[str, int]:
    total = len(refunds)
    duplicate = total - len({str(row["refund_id"]) for row in refunds})
    return {"total_refund_rows": total, "duplicate_rows": duplicate,
            "replay_rate_bps": duplicate * 10000 // total if total else 0}


def _unique(orders: Rows, refunds: Rows) -> dict[str, int]:
    return {"unique_refund_events": len({str(row["refund_id"]) for row in refunds})}


def _average(orders: Rows, refunds: Rows) -> dict[str, int]:
    total = sum(int(row["refund_cents"]) for row in refunds)
    return {"total_refund_rows": len(refunds),
            "average_refund_cents": total // len(refunds) if refunds else 0}


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    description: str
    aliases: tuple[str, ...]
    fields: tuple[str, ...]
    oracle: Callable[[Rows, Rows], dict[str, int]]
    version: str = "1"
    input_view: str = "raw"

    @property
    def fingerprint(self) -> str:
        material = (self.name, self.version, self.fields, self.input_view, self.description,
                    inspect.getsource(self.oracle))
        return hashlib.sha256(json.dumps(material, ensure_ascii=False).encode()).hexdigest()

    def validate(self, data: object, orders: Rows, refunds: Rows) -> None:
        if not isinstance(data, dict) or set(data) != set(self.fields):
            raise ValueError(f"{self.name}: result fields do not match the trusted schema")
        if any(type(data[field]) is not int or data[field] < 0 for field in self.fields):
            raise ValueError(f"{self.name}: values must be non-negative integers")
        if data != self.oracle(orders, refunds):
            raise ValueError(f"{self.name}: result failed independent semantic verification")

    def cases(self, seed: int) -> list[tuple[Rows, Rows]]:
        rng = random.Random(seed)
        cases: list[tuple[Rows, Rows]] = [([], [])]
        for count in (1, 3, 8, 16):
            rows = [{"refund_id": f"R{rng.randrange(max(1, count // 2))}",
                     "order_id": "O1", "refund_date": "2026-01-02",
                     "refund_cents": rng.randrange(1, 9000)} for _ in range(count)]
            cases.append(([{"order_id": "O1", "order_date": "2026-01-01", "category": "A",
                            "product": "P", "quantity": 1, "unit_price_cents": 100000,
                            "gross_cents": 100000}], rows))
        return cases


METRICS = {
    definition.name: definition for definition in (
        MetricDefinition("refund_replay_rate",
                         "Count later occurrences of an exact refund_id in RAW rows; return total_refund_rows, duplicate_rows and floor(duplicate_rows*10000/total_refund_rows), or zero for empty input.",
                         ("refund replay rate", "refund_replay_rate", "replay rate", "replay share",
                          "退款事件重放率", "退款重放率", "重放行数", "退款重传率", "重复行占比"),
                         ("total_refund_rows", "duplicate_rows", "replay_rate_bps"), _replay),
        MetricDefinition("unique_refund_events", "Count distinct refund_id values in RAW refund rows; return unique_refund_events.",
                         ("unique_refund_events", "unique refund event count", "count unique refund events", "退款唯一事件数", "去重退款事件数"),
                         ("unique_refund_events",), _unique),
        MetricDefinition("average_refund_amount", "Return total_refund_rows and floor(sum(refund_cents)/total_refund_rows) as average_refund_cents; empty input yields zero. Read RAW refunds.",
                         ("average_refund_amount", "average refund amount", "平均退款金额"),
                         ("total_refund_rows", "average_refund_cents"), _average),
    )
}


def metric_definition(name: str) -> MetricDefinition:
    try:
        return METRICS[name]
    except KeyError as exc:
        raise ValueError(f"no trusted verifier registered for metric: {name}") from exc
