from __future__ import annotations

from dataclasses import replace

from .models import ExecutionPolicy, ValidationIssue


class PolicyRepairer:
    """Turns explicit validator feedback into a new execution policy."""

    def repair(
        self, policy: ExecutionPolicy, issues: list[ValidationIssue]
    ) -> tuple[ExecutionPolicy, list[str]]:
        repaired = policy
        changes: list[str] = []
        expected = {
            issue.code: issue.expected
            for issue in issues
            if issue.repairable and issue.expected is not None
        }
        denominator = expected.get("refund_rate_definition")
        if denominator in {"order_lines", "distinct_orders"} and (
            repaired.refund_rate_denominator != denominator
        ):
            repaired = replace(repaired, refund_rate_denominator=denominator)
            changes.append(f"退款率分母改为 {denominator}")
        allocation = expected.get("category_refund_allocation")
        if allocation in {"first_category", "proportional"} and (
            repaired.category_refund_allocation != allocation
        ):
            repaired = replace(repaired, category_refund_allocation=allocation)
            changes.append(f"跨品类退款分摊改为 {allocation}")
        attribution = expected.get("refund_time_attribution")
        if attribution in {"refund_date", "order_date"} and (
            repaired.refund_time_attribution != attribution
        ):
            repaired = replace(repaired, refund_time_attribution=attribution)
            changes.append(f"退款日期归属改为 {attribution}")
        return repaired, changes
