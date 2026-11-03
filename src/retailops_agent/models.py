from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class OrderLine:
    order_id: str
    order_date: str
    category: str
    product: str
    quantity: int
    unit_price: Decimal

    @property
    def gross_amount(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass(frozen=True)
class Refund:
    refund_id: str
    order_id: str
    refund_date: str
    refund_amount: Decimal


@dataclass(frozen=True)
class AnalysisPlan:
    task: str
    tools: tuple[str, ...]
    max_repairs: int = 2


@dataclass(frozen=True)
class ExecutionPolicy:
    refund_rate_denominator: str = "distinct_orders"
    category_refund_allocation: str = "proportional"
    refund_time_attribution: str = "refund_date"

    @classmethod
    def naive(cls) -> "ExecutionPolicy":
        return cls(
            refund_rate_denominator="order_lines",
            category_refund_allocation="first_category",
            refund_time_attribution="refund_date",
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "refund_rate_denominator": self.refund_rate_denominator,
            "category_refund_allocation": self.category_refund_allocation,
            "refund_time_attribution": self.refund_time_attribution,
        }


@dataclass
class ToolResult:
    tool: str
    data: Any
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    repairable: bool = False
    expected: str | None = None


@dataclass
class RunTrace:
    task: str
    plan: AnalysisPlan
    attempts: int = 0
    results: dict[str, ToolResult] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    policies: list[ExecutionPolicy] = field(default_factory=list)
    plugin: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return not self.issues

