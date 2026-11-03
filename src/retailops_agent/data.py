from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import OrderLine, Refund, ValidationIssue


ORDER_FIELDS = {"order_id", "order_date", "category", "product", "quantity", "unit_price"}
REFUND_FIELDS = {"refund_id", "order_id", "refund_date", "refund_amount"}


class DataLoadError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        super().__init__("; ".join(issue.message for issue in issues))
        self.issues = issues


def _read_rows(path: Path, required: set[str], kind: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise DataLoadError([ValidationIssue("file_missing", f"{kind}文件不存在: {path}")])
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        actual = set(reader.fieldnames or [])
        missing = sorted(required - actual)
        if missing:
            raise DataLoadError(
                [ValidationIssue("missing_columns", f"{kind}缺少字段: {', '.join(missing)}")]
            )
        return list(reader)


def load_orders(path: Path) -> list[OrderLine]:
    rows = _read_rows(path, ORDER_FIELDS, "订单")
    orders: list[OrderLine] = []
    issues: list[ValidationIssue] = []
    for index, row in enumerate(rows, start=2):
        try:
            quantity = int(row["quantity"])
            price = Decimal(row["unit_price"])
            if quantity <= 0 or price < 0:
                raise ValueError("数量必须为正数且价格不能为负数")
            orders.append(
                OrderLine(
                    order_id=row["order_id"].strip(),
                    order_date=row["order_date"].strip(),
                    category=row["category"].strip(),
                    product=row["product"].strip(),
                    quantity=quantity,
                    unit_price=price,
                )
            )
        except (ValueError, InvalidOperation) as exc:
            issues.append(ValidationIssue("invalid_order_row", f"订单第{index}行无效: {exc}"))
    if issues:
        raise DataLoadError(issues)
    return orders


def load_refunds(path: Path) -> list[Refund]:
    rows = _read_rows(path, REFUND_FIELDS, "退款")
    refunds: list[Refund] = []
    issues: list[ValidationIssue] = []
    for index, row in enumerate(rows, start=2):
        try:
            amount = Decimal(row["refund_amount"])
            if amount <= 0:
                raise ValueError("退款金额必须为正数")
            refunds.append(
                Refund(
                    refund_id=row["refund_id"].strip(),
                    order_id=row["order_id"].strip(),
                    refund_date=row["refund_date"].strip(),
                    refund_amount=amount,
                )
            )
        except (ValueError, InvalidOperation) as exc:
            issues.append(ValidationIssue("invalid_refund_row", f"退款第{index}行无效: {exc}"))
    if issues:
        raise DataLoadError(issues)
    return refunds

