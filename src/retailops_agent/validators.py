from __future__ import annotations

from decimal import Decimal
from collections import defaultdict
from collections.abc import Mapping

from .contracts import normalize_metric_contract
from .models import OrderLine, Refund, ToolResult, ValidationIssue


def validate_results(
    orders: list[OrderLine],
    refunds: list[Refund],
    results: dict[str, ToolResult],
    metric_contract: Mapping[str, object] | None = None,
) -> list[ValidationIssue]:
    contract = normalize_metric_contract(metric_contract)
    issues: list[ValidationIssue] = []
    known_orders = {line.order_id for line in orders}
    for refund in refunds:
        if refund.order_id not in known_orders:
            issues.append(
                ValidationIssue(
                    "orphan_refund",
                    f"退款 {refund.refund_id} 对应的订单 {refund.order_id} 不存在",
                )
            )

    gross_total = sum((line.gross_amount for line in orders), Decimal(0))
    refund_total = sum((refund.refund_amount for refund in refunds), Decimal(0))

    daily = results.get("daily_net_sales")
    if daily:
        daily_gross = sum((row["gross_sales"] for row in daily.data), Decimal(0))
        daily_refund = sum((row["refund_amount"] for row in daily.data), Decimal(0))
        if daily_gross != gross_total or daily_refund != refund_total:
            issues.append(
                ValidationIssue(
                    "daily_total_mismatch",
                    "每日汇总与明细总额不一致",
                    repairable=True,
                )
            )
        expected_gross_by_date: dict[str, Decimal] = defaultdict(Decimal)
        expected_refund_by_date: dict[str, Decimal] = defaultdict(Decimal)
        order_dates: dict[str, str] = {}
        for line in orders:
            expected_gross_by_date[line.order_date] += line.gross_amount
            order_dates.setdefault(line.order_id, line.order_date)
        for refund in refunds:
            expected_date = (
                order_dates.get(refund.order_id, refund.refund_date)
                if contract["refund_time_attribution"] == "order_date"
                else refund.refund_date
            )
            expected_refund_by_date[expected_date] += refund.refund_amount
        actual_by_date = {
            row["date"]: (row["gross_sales"], row["refund_amount"], row["net_sales"])
            for row in daily.data
        }
        expected_by_date = {
            current: (
                expected_gross_by_date[current],
                expected_refund_by_date[current],
                expected_gross_by_date[current] - expected_refund_by_date[current],
            )
            for current in sorted(set(expected_gross_by_date) | set(expected_refund_by_date))
        }
        if actual_by_date != expected_by_date:
            issues.append(
                ValidationIssue(
                    "refund_time_attribution",
                    "每日退款归属日期与任务要求不一致",
                    True,
                    contract["refund_time_attribution"],
                )
            )

    ranking = results.get("category_ranking")
    if ranking:
        category_gross = sum((row["gross_sales"] for row in ranking.data), Decimal(0))
        allocated_refunds = sum((row["refund_amount"] for row in ranking.data), Decimal(0))
        if category_gross != gross_total:
            issues.append(
                ValidationIssue("category_total_mismatch", "品类销售额与订单明细不一致", True)
            )
        if allocated_refunds != refund_total:
            issues.append(
                ValidationIssue(
                    "refund_allocation_mismatch",
                    "品类退款分摊额与退款明细不一致",
                    True,
                )
            )
        expected_refunds: dict[str, Decimal] = defaultdict(Decimal)
        order_categories: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        for line in orders:
            order_categories[line.order_id][line.category] += line.gross_amount
        for refund in refunds:
            entries = list(order_categories.get(refund.order_id, {}).items())
            order_total = sum((amount for _, amount in entries), Decimal(0))
            if not entries or not order_total:
                continue
            if contract["category_refund_allocation"] == "first_category":
                expected_refunds[entries[0][0]] += refund.refund_amount
            else:
                remaining = refund.refund_amount
                for index, (category, amount) in enumerate(entries):
                    share = (
                        remaining
                        if index == len(entries) - 1
                        else refund.refund_amount * amount / order_total
                    )
                    expected_refunds[category] += share
                    remaining -= share
        actual_refunds = {row["category"]: row["refund_amount"] for row in ranking.data}
        if any(actual_refunds.get(category, Decimal(0)) != amount for category, amount in expected_refunds.items()):
            issues.append(
                ValidationIssue(
                    "category_refund_allocation",
                    "跨品类订单退款分摊方式与任务要求不一致",
                    True,
                    contract["category_refund_allocation"],
                )
            )

    rate = results.get("refund_rate")
    if rate:
        expected_total = (
            len(orders)
            if contract["refund_rate_denominator"] == "order_lines"
            else len({line.order_id for line in orders})
        )
        expected_refunded = len({refund.order_id for refund in refunds if refund.order_id in known_orders})
        expected_rate = Decimal(expected_refunded) / Decimal(expected_total) if expected_total else Decimal(0)
        if (
            rate.data["total_orders"] != expected_total
            or rate.data["refunded_orders"] != expected_refunded
            or rate.data["refund_rate"] != expected_rate
        ):
            issues.append(
                ValidationIssue(
                    "refund_rate_definition",
                    "退款率分母与任务要求不一致",
                    True,
                    contract["refund_rate_denominator"],
                )
            )
    return issues

