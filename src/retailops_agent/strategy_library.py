from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from .data import load_orders, load_refunds
from .models import ExecutionPolicy, OrderLine, Refund, ToolResult
from .tools import execute_tool


ACTION_DESCRIPTIONS = {
    "equal_category_allocation": (
        "split each refund equally across the distinct categories in its order"
    ),
    "quantity_category_allocation": (
        "split each refund by summed item quantity per category in its order"
    ),
    "deduplicate_refunds": "keep only the first row for each refund_id before all metrics",
    "hybrid_refund_time": (
        "read a day threshold from the instruction; short-lag refunds use order date and "
        "long-lag refunds use refund date"
    ),
}


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


@dataclass(frozen=True)
class StrategyRule:
    rule_id: str
    cue: str
    action: str
    reason: str = ""
    source_task: str = ""

    def __post_init__(self) -> None:
        if not self.rule_id or len(self.rule_id) > 80:
            raise ValueError("rule_id must contain 1-80 characters")
        if not self.cue.strip() or len(self.cue) > 120:
            raise ValueError("cue must contain 1-120 characters")
        if self.action not in ACTION_DESCRIPTIONS:
            raise ValueError(f"unsupported strategy action: {self.action}")

    def matches(self, instruction: str) -> bool:
        return _normalized_text(self.cue) in _normalized_text(instruction)

    def to_dict(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "cue": self.cue,
            "action": self.action,
            "reason": self.reason,
            "source_task": self.source_task,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "StrategyRule":
        return cls(
            rule_id=str(payload.get("rule_id", "")),
            cue=str(payload.get("cue", "")),
            action=str(payload.get("action", "")),
            reason=str(payload.get("reason", "")),
            source_task=str(payload.get("source_task", "")),
        )


@dataclass(frozen=True)
class StrategyLibrary:
    version: int = 0
    rules: tuple[StrategyRule, ...] = ()

    def __post_init__(self) -> None:
        if self.version < 0:
            raise ValueError("library version must be non-negative")
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("strategy rule ids must be unique")

    def match(self, instruction: str) -> StrategyRule | None:
        for rule in reversed(self.rules):
            if rule.matches(instruction):
                return rule
        return None

    def with_rule(self, rule: StrategyRule) -> "StrategyLibrary":
        if any(existing.action == rule.action for existing in self.rules):
            raise ValueError(f"action already learned: {rule.action}")
        return StrategyLibrary(version=self.version + 1, rules=(*self.rules, rule))

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "rules": [rule.to_dict() for rule in self.rules],
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "StrategyLibrary":
        raw_rules = payload.get("rules", [])
        if not isinstance(raw_rules, list):
            raise ValueError("strategy library rules must be a list")
        return cls(
            version=int(payload.get("version", 0)),
            rules=tuple(StrategyRule.from_dict(item) for item in raw_rules if isinstance(item, dict)),
        )

    @classmethod
    def read(cls, path: Path) -> "StrategyLibrary":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class StrategyExecution:
    results: dict[str, ToolResult]
    matched_rule_id: str | None
    action: str | None


def _category_ranking(
    orders: list[OrderLine],
    refunds: list[Refund],
    *,
    allocation: str,
) -> ToolResult:
    gross: dict[str, Decimal] = defaultdict(Decimal)
    amounts: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    quantities: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for line in orders:
        gross[line.category] += line.gross_amount
        amounts[line.order_id][line.category] += line.gross_amount
        quantities[line.order_id][line.category] += Decimal(line.quantity)

    allocated: dict[str, Decimal] = defaultdict(Decimal)
    for refund in refunds:
        weights = (
            {category: Decimal(1) for category in amounts[refund.order_id]}
            if allocation == "equal"
            else quantities[refund.order_id]
        )
        entries = list(weights.items())
        total_weight = sum((weight for _, weight in entries), Decimal(0))
        if not entries or not total_weight:
            continue
        remaining = refund.refund_amount
        for index, (category, weight) in enumerate(entries):
            share = (
                remaining
                if index == len(entries) - 1
                else refund.refund_amount * weight / total_weight
            )
            allocated[category] += share
            remaining -= share

    rows = [
        {
            "category": category,
            "gross_sales": amount,
            "refund_amount": allocated[category],
            "net_sales": amount - allocated[category],
        }
        for category, amount in gross.items()
    ]
    rows.sort(key=lambda row: (-row["net_sales"], row["category"]))
    return ToolResult(
        "category_ranking",
        rows,
        {"allocation": f"strategy_library:{allocation}"},
    )


def _hybrid_daily(
    orders: list[OrderLine],
    refunds: list[Refund],
    instruction: str,
) -> ToolResult:
    match = re.search(r"不超过\s*(\d+)\s*天", instruction)
    if not match:
        match = re.search(r"within\s+(\d+)\s+days?", instruction, re.IGNORECASE)
    if not match:
        raise ValueError("hybrid_refund_time rule matched but no day threshold was found")
    threshold = int(match.group(1))
    gross_by_date: dict[str, Decimal] = defaultdict(Decimal)
    refund_by_date: dict[str, Decimal] = defaultdict(Decimal)
    order_dates: dict[str, str] = {}
    for line in orders:
        gross_by_date[line.order_date] += line.gross_amount
        order_dates.setdefault(line.order_id, line.order_date)
    for refund in refunds:
        order_date = order_dates[refund.order_id]
        lag = (date.fromisoformat(refund.refund_date) - date.fromisoformat(order_date)).days
        reporting_date = order_date if lag <= threshold else refund.refund_date
        refund_by_date[reporting_date] += refund.refund_amount
    dates = sorted(set(gross_by_date) | set(refund_by_date))
    return ToolResult(
        "daily_net_sales",
        [
            {
                "date": current,
                "gross_sales": gross_by_date[current],
                "refund_amount": refund_by_date[current],
                "net_sales": gross_by_date[current] - refund_by_date[current],
            }
            for current in dates
        ],
        {"refund_time_attribution": "hybrid_lag", "threshold_days": threshold},
    )


def execute_with_library(
    task_dir: Path,
    policy: ExecutionPolicy,
    library: StrategyLibrary,
) -> StrategyExecution:
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    instruction = str(task["instruction"])
    tools = [str(name) for name in task["tools"]]
    orders = load_orders(task_dir / "orders.csv")
    refunds = load_refunds(task_dir / "refunds.csv")
    rule = library.match(instruction)
    action = rule.action if rule else None

    effective_refunds = refunds
    if action == "deduplicate_refunds":
        first_by_id: dict[str, Refund] = {}
        for refund in refunds:
            first_by_id.setdefault(refund.refund_id, refund)
        effective_refunds = list(first_by_id.values())

    results: dict[str, ToolResult] = {}
    for tool in tools:
        if tool == "category_ranking" and action == "equal_category_allocation":
            results[tool] = _category_ranking(orders, effective_refunds, allocation="equal")
        elif tool == "category_ranking" and action == "quantity_category_allocation":
            results[tool] = _category_ranking(orders, effective_refunds, allocation="quantity")
        elif tool == "daily_net_sales" and action == "hybrid_refund_time":
            results[tool] = _hybrid_daily(orders, effective_refunds, instruction)
        else:
            results[tool] = execute_tool(tool, orders, effective_refunds, policy)
    return StrategyExecution(
        results=results,
        matched_rule_id=rule.rule_id if rule else None,
        action=action,
    )


def candidate_rule_from_model(
    payload: dict[str, Any],
    *,
    generation: int,
    source_task: str,
    instruction: str,
    allowed_cues: list[str] | None = None,
) -> StrategyRule:
    cue = str(payload.get("cue", "")).strip()
    action = str(payload.get("action", "")).strip()
    reason = str(payload.get("reason", "")).strip()
    if _normalized_text(cue) not in _normalized_text(instruction):
        raise ValueError("candidate cue must be a literal substring of the source instruction")
    if allowed_cues is not None and cue not in allowed_cues:
        raise ValueError("candidate cue must be selected verbatim from cue_options")
    return StrategyRule(
        rule_id=f"learned-g{generation:02d}",
        cue=cue,
        action=action,
        reason=reason,
        source_task=source_task,
    )
