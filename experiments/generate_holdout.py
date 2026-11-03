from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from retailops_agent.contracts import DEFAULT_METRIC_CONTRACT, normalize_metric_contract


SCENARIOS = ("refund_rate", "category", "combined", "daily")
CATEGORIES = ("电子产品", "办公耗材", "办公家具", "网络设备", "仓储用品", "清洁用品")
PRODUCTS = {
    "电子产品": "显示器",
    "办公耗材": "打印纸",
    "办公家具": "升降桌",
    "网络设备": "交换机",
    "仓储用品": "货架",
    "清洁用品": "清洁剂",
}
INSTRUCTIONS = {
    "refund_rate": (
        (
            "统计发生退款的订单占比；同一订单出现多行商品时只计为一个订单，"
            "同一订单多次退款也只计为一个退款订单。"
        ),
        "给出退款订单率，分母使用去重后的订单编号，多次退款的订单只计一次。",
        "计算 refund rate；订单明细包含重复订单号时，分母仍按唯一订单统计。",
    ),
    "category": (
        (
            "输出各商品类别扣除退款后的销售额排行。若退款订单包含多个类别，"
            "按各类别在该订单中的成交额占比分配退款。"
        ),
        "比较各品类净成交额，跨品类订单的退款依据订单行金额比例拆分。",
        "生成 category ranking，并将一张订单中的退款按各类别销售额占比分摊。",
    ),
    "combined": (
        (
            "核算退款订单率和类别净销售额排行。订单率按唯一订单编号统计，"
            "跨类别订单的退款按对应订单行成交额比例拆分。"
        ),
        "同时给出退款率与品类排行：订单去重后计算比例，退款按品类成交额占比分配。",
        "输出 refund rate 与 category ranking，采用唯一订单分母和比例退款分摊。",
    ),
    "daily": (
        "按订单日期与退款发生日期汇总每日销售额、退款额和净销售额。",
        "生成按日经营汇总，分别列出成交、退款与净额。",
        "calculate daily net sales using order dates and refund dates.",
    ),
}
TOOLS = {
    "refund_rate": ["refund_rate"],
    "category": ["category_ranking"],
    "combined": ["refund_rate", "category_ranking"],
    "daily": ["daily_net_sales"],
}


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def decimal_default(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def calculate_expected(
    orders: list[dict[str, object]],
    refunds: list[dict[str, object]],
    tools: list[str],
    metric_contract: dict[str, str] | None = None,
) -> dict[str, object]:
    contract = normalize_metric_contract(metric_contract)
    results: dict[str, object] = {}
    order_ids = {str(row["order_id"]) for row in orders}

    if "refund_rate" in tools:
        refunded = {str(row["order_id"]) for row in refunds if row["order_id"] in order_ids}
        denominator = len(orders) if contract["refund_rate_denominator"] == "order_lines" else len(order_ids)
        results["refund_rate"] = {
            "total_orders": denominator,
            "refunded_orders": len(refunded),
            "refund_rate": Decimal(len(refunded)) / Decimal(denominator),
        }

    if "category_ranking" in tools:
        gross: dict[str, Decimal] = defaultdict(Decimal)
        by_order: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        for row in orders:
            amount = Decimal(str(row["unit_price"])) * int(row["quantity"])
            category = str(row["category"])
            order_id = str(row["order_id"])
            gross[category] += amount
            by_order[order_id][category] += amount
        allocated: dict[str, Decimal] = defaultdict(Decimal)
        for refund in refunds:
            entries = list(by_order[str(refund["order_id"])].items())
            total = sum((amount for _, amount in entries), Decimal(0))
            refund_amount = Decimal(str(refund["refund_amount"]))
            if contract["category_refund_allocation"] == "first_category":
                allocated[entries[0][0]] += refund_amount
            else:
                remaining = refund_amount
                for position, (category, amount) in enumerate(entries):
                    share = (
                        remaining
                        if position == len(entries) - 1
                        else refund_amount * amount / total
                    )
                    allocated[category] += share
                    remaining -= share
        ranking = [
            {
                "category": category,
                "gross_sales": amount,
                "refund_amount": allocated[category],
                "net_sales": amount - allocated[category],
            }
            for category, amount in gross.items()
        ]
        ranking.sort(key=lambda row: (-row["net_sales"], row["category"]))
        results["category_ranking"] = ranking

    if "daily_net_sales" in tools:
        gross_by_date: dict[str, Decimal] = defaultdict(Decimal)
        refund_by_date: dict[str, Decimal] = defaultdict(Decimal)
        order_dates: dict[str, str] = {}
        for row in orders:
            gross_by_date[str(row["order_date"])] += Decimal(str(row["unit_price"])) * int(
                row["quantity"]
            )
            order_dates.setdefault(str(row["order_id"]), str(row["order_date"]))
        for row in refunds:
            refund_date = (
                order_dates[str(row["order_id"])]
                if contract["refund_time_attribution"] == "order_date"
                else str(row["refund_date"])
            )
            refund_by_date[refund_date] += Decimal(str(row["refund_amount"]))
        results["daily_net_sales"] = [
            {
                "date": current,
                "gross_sales": gross_by_date[current],
                "refund_amount": refund_by_date[current],
                "net_sales": gross_by_date[current] - refund_by_date[current],
            }
            for current in sorted(set(gross_by_date) | set(refund_by_date))
        ]

    return results


def dynamic_contract(index: int, scenario: str) -> dict[str, str]:
    occurrence = (index - 1) // len(SCENARIOS)
    contract = {
        "refund_rate_denominator": "order_lines",
        "category_refund_allocation": "first_category",
        "refund_time_attribution": "refund_date",
    }
    if scenario == "refund_rate":
        contract["refund_rate_denominator"] = (
            "distinct_orders" if occurrence % 2 == 0 else "order_lines"
        )
    elif scenario == "category":
        contract["category_refund_allocation"] = (
            "proportional" if occurrence % 2 == 0 else "first_category"
        )
    elif scenario == "combined":
        combinations = (
            ("distinct_orders", "proportional"),
            ("distinct_orders", "first_category"),
            ("order_lines", "proportional"),
            ("order_lines", "first_category"),
        )
        denominator, allocation = combinations[occurrence % len(combinations)]
        contract["refund_rate_denominator"] = denominator
        contract["category_refund_allocation"] = allocation
    else:
        contract["refund_time_attribution"] = (
            "order_date" if occurrence % 2 == 0 else "refund_date"
        )
    return contract


def dynamic_instruction(scenario: str, contract: dict[str, str], rng: random.Random) -> str:
    denominator_text = (
        "唯一订单数" if contract["refund_rate_denominator"] == "distinct_orders" else "订单明细行数"
    )
    allocation_text = (
        "按各品类订单行销售额比例分摊"
        if contract["category_refund_allocation"] == "proportional"
        else "全部归入订单中首个记录的品类"
    )
    attribution_text = (
        "回溯计入原订单日期"
        if contract["refund_time_attribution"] == "order_date"
        else "计入退款实际发生日期"
    )
    variants = {
        "refund_rate": (
            f"计算退款订单占比，分母采用{denominator_text}。",
            f"输出 refund rate；以{denominator_text}作为统计分母。",
        ),
        "category": (
            f"生成品类净销售额排行，跨品类订单退款{allocation_text}。",
            f"比较 category ranking；一笔退款涉及多个品类时{allocation_text}。",
        ),
        "combined": (
            f"计算退款率与品类净销售额排行；退款率分母为{denominator_text}，跨品类退款{allocation_text}。",
            f"输出 refund rate 和 category ranking，采用{denominator_text}，退款{allocation_text}。",
        ),
        "daily": (
            f"生成每日净销售额，退款{attribution_text}。",
            f"按日汇总成交与退款；退款金额需要{attribution_text}。",
        ),
    }
    return rng.choice(variants[scenario])


def generate_case(
    root: Path,
    index: int,
    scenario: str,
    rng: random.Random,
    *,
    metric_contract: dict[str, str] | None = None,
) -> dict[str, object]:
    case_id = f"holdout-{index:03d}-{scenario}"
    case = root / case_id
    order_count = rng.randint(5, 10)
    start = date(2026, 9, 1) + timedelta(days=rng.randint(0, 12))
    orders: list[dict[str, object]] = []
    order_totals: dict[str, Decimal] = defaultdict(Decimal)
    order_dates: dict[str, date] = {}

    for order_index in range(order_count):
        order_id = f"H{index:03d}-{order_index + 1:02d}"
        order_date = start + timedelta(days=rng.randint(0, 5))
        order_dates[order_id] = order_date
        line_count = rng.randint(2, 3) if order_index == 0 else rng.randint(1, 3)
        categories = rng.sample(CATEGORIES, line_count)
        for line_index, category in enumerate(categories, start=1):
            quantity = rng.randint(1, 5)
            unit_price = Decimal(rng.randint(25, 420)) + rng.choice(
                (Decimal("0.00"), Decimal("0.50"), Decimal("0.90"))
            )
            amount = unit_price * quantity
            order_totals[order_id] += amount
            orders.append(
                {
                    "order_id": order_id,
                    "order_date": order_date.isoformat(),
                    "category": category,
                    "product": f"{PRODUCTS[category]}-{line_index}",
                    "quantity": quantity,
                    "unit_price": unit_price,
                }
            )

    refund_order_ids = [f"H{index:03d}-01"]
    if order_count >= 7 and index % 2 == 0:
        refund_order_ids.append(f"H{index:03d}-03")
    refunds: list[dict[str, object]] = []
    refund_index = 1
    for order_id in refund_order_ids:
        total_refund = (order_totals[order_id] * Decimal(rng.choice((10, 15, 20, 25))) / 100).quantize(
            Decimal("0.01")
        )
        parts = 2 if order_id.endswith("01") and index % 3 == 0 else 1
        if parts == 1:
            amounts = [total_refund]
        else:
            first_part = (total_refund / 2).quantize(Decimal("0.01"))
            amounts = [first_part, total_refund - first_part]
        for amount in amounts:
            refunds.append(
                {
                    "refund_id": f"HR{index:03d}-{refund_index:02d}",
                    "order_id": order_id,
                    "refund_date": (order_dates[order_id] + timedelta(days=rng.randint(1, 4))).isoformat(),
                    "refund_amount": amount,
                }
            )
            refund_index += 1

    case.mkdir(parents=True, exist_ok=True)
    contract = normalize_metric_contract(metric_contract)
    instruction = (
        dynamic_instruction(scenario, contract, rng)
        if metric_contract is not None
        else rng.choice(INSTRUCTIONS[scenario])
    )
    task = {"instruction": instruction, "tools": TOOLS[scenario]}
    if metric_contract is not None:
        task["metric_contract"] = contract
    (case / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(
        case / "orders.csv",
        ["order_id", "order_date", "category", "product", "quantity", "unit_price"],
        orders,
    )
    write_csv(
        case / "refunds.csv",
        ["refund_id", "order_id", "refund_date", "refund_amount"],
        refunds,
    )
    expected = {
        "case_id": case_id,
        "scenario": scenario,
        "metric_contract": contract,
        "results": calculate_expected(orders, refunds, TOOLS[scenario], contract),
    }
    (case / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2, default=decimal_default) + "\n",
        encoding="utf-8",
    )
    return {
        "case_id": case_id,
        "scenario": scenario,
        "orders": order_count,
        "order_lines": len(orders),
        "refund_rows": len(refunds),
        "instruction": instruction,
        "metric_contract": contract,
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a frozen synthetic holdout benchmark.")
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/retailops/holdout-v1")
    )
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--seed", type=int, default=2026110302)
    parser.add_argument(
        "--dynamic-contracts",
        action="store_true",
        help="vary task-level business definitions instead of using the default contract",
    )
    args = parser.parse_args()
    if args.count < 4 or args.count % len(SCENARIOS):
        parser.error("--count must be a positive multiple of 4")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")

    rng = random.Random(args.seed)
    cases = [
        generate_case(
            args.output,
            index,
            SCENARIOS[(index - 1) % len(SCENARIOS)],
            rng,
            metric_contract=(
                dynamic_contract(index, SCENARIOS[(index - 1) % len(SCENARIOS)])
                if args.dynamic_contracts
                else None
            ),
        )
        for index in range(1, args.count + 1)
    ]
    file_hashes = {
        str(path.relative_to(args.output)).replace("\\", "/"): sha256(path)
        for path in sorted(args.output.rglob("*"))
        if path.is_file()
    }
    project_root = Path(__file__).resolve().parents[1]
    frozen_source_files = sorted((project_root / "src" / "retailops_agent").rglob("*.py"))
    frozen_source_files.append(project_root / "experiments" / "run_harness2_model.py")
    frozen_source_hashes = {
        str(path.relative_to(project_root)).replace("\\", "/"): sha256(path)
        for path in frozen_source_files
    }
    manifest = {
        "name": f"retailops-{args.output.name}",
        "seed": args.seed,
        "count": args.count,
        "scenarios": list(SCENARIOS),
        "dynamic_contracts": args.dynamic_contracts,
        "cases": cases,
        "file_sha256": file_hashes,
        "frozen_source_sha256": frozen_source_hashes,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"generated frozen holdout: {args.count} cases in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
