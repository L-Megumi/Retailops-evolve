from __future__ import annotations

from collections.abc import Mapping


DEFAULT_METRIC_CONTRACT = {
    "refund_rate_denominator": "distinct_orders",
    "category_refund_allocation": "proportional",
    "refund_time_attribution": "refund_date",
}

ALLOWED_METRIC_CONTRACT = {
    "refund_rate_denominator": {"order_lines", "distinct_orders"},
    "category_refund_allocation": {"first_category", "proportional"},
    "refund_time_attribution": {"refund_date", "order_date"},
}


def normalize_metric_contract(raw: Mapping[str, object] | None) -> dict[str, str]:
    contract = dict(DEFAULT_METRIC_CONTRACT)
    if raw:
        for key, value in raw.items():
            if key not in ALLOWED_METRIC_CONTRACT:
                raise ValueError(f"未知业务口径字段: {key}")
            normalized = str(value)
            if normalized not in ALLOWED_METRIC_CONTRACT[key]:
                allowed = ", ".join(sorted(ALLOWED_METRIC_CONTRACT[key]))
                raise ValueError(f"业务口径 {key}={normalized!r} 无效，可选值: {allowed}")
            contract[key] = normalized
    return contract
