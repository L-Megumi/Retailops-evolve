from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path


SCENARIOS = ("refund_rate", "category", "combined", "daily")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def generate_case(root: Path, index: int, scenario: str, rng: random.Random) -> None:
    case = root / f"case-{index:03d}-{scenario}"
    base = rng.choice((80, 100, 120, 150))
    orders = [
        {
            "order_id": f"O{index}01",
            "order_date": "2026-10-01",
            "category": "电子产品",
            "product": "显示器",
            "quantity": 1,
            "unit_price": base * 4,
        },
        {
            "order_id": f"O{index}01",
            "order_date": "2026-10-01",
            "category": "办公耗材",
            "product": "连接线",
            "quantity": 2,
            "unit_price": base,
        },
        {
            "order_id": f"O{index}02",
            "order_date": "2026-10-02",
            "category": "办公家具",
            "product": "办公桌",
            "quantity": 1,
            "unit_price": base * 6,
        },
        {
            "order_id": f"O{index}03",
            "order_date": "2026-10-02",
            "category": "办公耗材",
            "product": "笔记本",
            "quantity": 5,
            "unit_price": base // 4,
        },
    ]
    refunds = [
        {
            "refund_id": f"R{index}01",
            "order_id": f"O{index}01",
            "refund_date": "2026-10-03",
            "refund_amount": base * 1.5,
        }
    ]
    if scenario == "refund_rate":
        instruction = "按去重订单数计算退款率。"
        tools = ["refund_rate"]
    elif scenario == "category":
        instruction = "生成品类净销售额排行，跨品类订单退款按订单行销售额比例分摊。"
        tools = ["category_ranking"]
    elif scenario == "combined":
        instruction = "计算退款率和品类净销售额；退款率按去重订单，跨品类退款按销售额比例分摊。"
        tools = ["refund_rate", "category_ranking"]
    else:
        instruction = "统计每日净销售额。"
        tools = ["daily_net_sales"]
    case.mkdir(parents=True, exist_ok=True)
    (case / "task.json").write_text(
        json.dumps({"instruction": instruction, "tools": tools}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("benchmarks/retailops/generated"))
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20261103)
    args = parser.parse_args()
    if args.count < 4:
        parser.error("--count must be at least 4")
    rng = random.Random(args.seed)
    args.output.mkdir(parents=True, exist_ok=True)
    for index in range(1, args.count + 1):
        generate_case(args.output, index, SCENARIOS[(index - 1) % len(SCENARIOS)], rng)
    manifest = {
        "seed": args.seed,
        "count": args.count,
        "scenarios": list(SCENARIOS),
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"generated {args.count} cases in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
