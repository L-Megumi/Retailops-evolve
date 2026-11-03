from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .models import ExecutionPolicy, OrderLine, Refund, ToolResult


def daily_net_sales(
    orders: list[OrderLine],
    refunds: list[Refund],
    policy: ExecutionPolicy | None = None,
) -> ToolResult:
    policy = policy or ExecutionPolicy()
    gross_by_date: dict[str, Decimal] = defaultdict(Decimal)
    refund_by_date: dict[str, Decimal] = defaultdict(Decimal)
    order_dates: dict[str, str] = {}
    for line in orders:
        gross_by_date[line.order_date] += line.gross_amount
        order_dates.setdefault(line.order_id, line.order_date)
    for refund in refunds:
        refund_date = (
            order_dates.get(refund.order_id, refund.refund_date)
            if policy.refund_time_attribution == "order_date"
            else refund.refund_date
        )
        refund_by_date[refund_date] += refund.refund_amount
    dates = sorted(set(gross_by_date) | set(refund_by_date))
    rows = [
        {
            "date": date,
            "gross_sales": gross_by_date[date],
            "refund_amount": refund_by_date[date],
            "net_sales": gross_by_date[date] - refund_by_date[date],
        }
        for date in dates
    ]
    return ToolResult(
        "daily_net_sales",
        rows,
        {
            "date_count": len(dates),
            "refund_time_attribution": policy.refund_time_attribution,
        },
    )


def refund_rate(
    orders: list[OrderLine], refunds: list[Refund], policy: ExecutionPolicy | None = None
) -> ToolResult:
    policy = policy or ExecutionPolicy()
    order_ids = {line.order_id for line in orders}
    refunded_order_ids = {refund.order_id for refund in refunds if refund.order_id in order_ids}
    denominator = len(orders) if policy.refund_rate_denominator == "order_lines" else len(order_ids)
    rate = Decimal(len(refunded_order_ids)) / Decimal(denominator) if denominator else Decimal(0)
    return ToolResult(
        "refund_rate",
        {
            "total_orders": denominator,
            "refunded_orders": len(refunded_order_ids),
            "refund_rate": rate,
        },
        {"denominator": policy.refund_rate_denominator},
    )


def category_ranking(
    orders: list[OrderLine], refunds: list[Refund], policy: ExecutionPolicy | None = None
) -> ToolResult:
    policy = policy or ExecutionPolicy()
    gross: dict[str, Decimal] = defaultdict(Decimal)
    order_category_amounts: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for line in orders:
        gross[line.category] += line.gross_amount
        order_category_amounts[line.order_id][line.category] += line.gross_amount

    refunded: dict[str, Decimal] = defaultdict(Decimal)
    for refund in refunds:
        allocations = order_category_amounts.get(refund.order_id, {})
        order_total = sum(allocations.values(), Decimal(0))
        if not order_total:
            continue
        if policy.category_refund_allocation == "first_category":
            first = next(iter(allocations))
            refunded[first] += refund.refund_amount
        else:
            remaining = refund.refund_amount
            entries = list(allocations.items())
            for index, (category, amount) in enumerate(entries):
                share = remaining if index == len(entries) - 1 else refund.refund_amount * amount / order_total
                refunded[category] += share
                remaining -= share

    rows = [
        {
            "category": category,
            "gross_sales": amount,
            "refund_amount": refunded[category],
            "net_sales": amount - refunded[category],
        }
        for category, amount in gross.items()
    ]
    rows.sort(key=lambda row: (-row["net_sales"], row["category"]))
    return ToolResult(
        "category_ranking",
        rows,
        {"allocation": policy.category_refund_allocation},
    )


def execute_tool(
    name: str,
    orders: list[OrderLine],
    refunds: list[Refund],
    policy: ExecutionPolicy,
) -> ToolResult:
    if name == "refund_rate":
        return refund_rate(orders, refunds, policy)
    if name == "category_ranking":
        return category_ranking(orders, refunds, policy)
    if name == "daily_net_sales":
        return daily_net_sales(orders, refunds, policy)
    return TOOL_REGISTRY[name](orders, refunds)


TOOL_REGISTRY = {
    "daily_net_sales": daily_net_sales,
    "refund_rate": refund_rate,
    "category_ranking": category_ranking,
}

