from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from retailops_agent.contracts import DEFAULT_METRIC_CONTRACT

try:
    from .generate_holdout import calculate_expected, decimal_default, sha256, write_csv
    from .generate_rsi_benchmark import DATA_PROFILES, _generate_rows
except ImportError:  # pragma: no cover - direct script execution
    from generate_holdout import calculate_expected, decimal_default, sha256, write_csv
    from generate_rsi_benchmark import DATA_PROFILES, _generate_rows


INSTRUCTIONS = {
    "evolution": (
        "生成品类净销售额排行与每日经营汇总。累计退款上限等于对应订单成交额：按退款文件顺序逐行处理，边界行只确认剩余额度，超过上限后的退款行不再确认。",
        "核算品类和每日净销售额。每个订单的累计有效退款不得超过该订单原始销售额；依输入顺序扣减余额，最后一笔可被截断到剩余金额。",
        "输出 category ranking 和 daily net sales。退款确认采用订单级余额池，初始余额为订单成交额；每行最多确认当前余额，余额耗尽后忽略后续行。",
        "审计过量退款：以订单销售额作为累计可退款额度，顺序消费额度并裁剪越界退款，再计算品类排行与每日汇总。",
    ),
    "validation": (
        "生成品类排行和日汇总。累计退款上限为原订单成交额，按文件顺序确认退款；若某行使累计额越界，只保留恰好达到上限的部分。",
        "先执行订单级退款封顶，再做经营分析：退款总确认额不能大于订单销售额，逐笔扣减可用额度，超出部分作废。",
        "对 refunds 做顺序裁剪：每个 order_id 的总退款最多等于其 gross sales；将裁剪后的退款用于 category ranking 与 daily net sales。",
        "订单退款余额从成交额开始，退款行依次消耗余额，确认金额取申报额与剩余额度的较小值；余额为零后跳过。输出品类和每日净额。",
    ),
    "transfer": (
        "计算品类净额和每日净额。对同一订单设置累计可退天花板，天花板就是订单成交额；按输入顺序截断导致超额的退款记录。",
        "退款不得把订单净额扣成负数。顺序读取退款流水，每笔只承认尚未退掉的订单金额，然后生成两类经营汇总。",
        "Apply an order-level cumulative refund cap equal to order gross. Consume the cap in file order, clip the boundary refund, drop later excess rows, then produce category and daily metrics.",
        "先把每单退款限制在该单销售总额内：维护剩余可退金额并依次处理流水。使用处理后的流水生成 category ranking 和 daily net sales。",
    ),
}


def cap_refunds(
    orders: list[dict[str, object]], refunds: list[dict[str, object]]
) -> list[dict[str, object]]:
    remaining: dict[str, Decimal] = defaultdict(Decimal)
    for row in orders:
        remaining[str(row["order_id"])] += Decimal(str(row["unit_price"])) * int(
            row["quantity"]
        )
    accepted: list[dict[str, object]] = []
    for refund in refunds:
        order_id = str(refund["order_id"])
        amount = min(Decimal(str(refund["refund_amount"])), remaining[order_id])
        if amount <= 0:
            continue
        updated = dict(refund)
        updated["refund_amount"] = amount
        accepted.append(updated)
        remaining[order_id] -= amount
    return accepted


def generate_case(
    root: Path,
    *,
    split: str,
    index: int,
    seed: int,
    instruction: str | None = None,
) -> dict[str, object]:
    rng = random.Random(seed)
    case_id = f"code-{split}-{index:03d}"
    orders, generated_refunds = _generate_rows(
        case_id, rng, DATA_PROFILES[(index - 1) % len(DATA_PROFILES)]
    )
    order_totals: dict[str, Decimal] = defaultdict(Decimal)
    order_dates: dict[str, str] = {}
    for row in orders:
        order_id = str(row["order_id"])
        order_totals[order_id] += Decimal(str(row["unit_price"])) * int(row["quantity"])
        order_dates.setdefault(order_id, str(row["order_date"]))
    target_order = str(orders[0]["order_id"])
    total = order_totals[target_order]
    first = (total * Decimal("0.70")).quantize(Decimal("0.01"))
    second = (total * Decimal("0.60")).quantize(Decimal("0.01"))
    base_date = date.fromisoformat(order_dates[target_order])
    refunds: list[dict[str, object]] = [
        {
            "refund_id": f"{case_id.upper()}-OVER-1",
            "order_id": target_order,
            "refund_date": (base_date + timedelta(days=2)).isoformat(),
            "refund_amount": first,
        },
        {
            "refund_id": f"{case_id.upper()}-OVER-2",
            "order_id": target_order,
            "refund_date": (base_date + timedelta(days=3)).isoformat(),
            "refund_amount": second,
        },
    ]
    for row in generated_refunds:
        if str(row["order_id"]) != target_order:
            refunds.append(row)
            break

    task_root = root / "tasks" / split / case_id
    task_root.mkdir(parents=True, exist_ok=False)
    task = {
        "instruction": instruction
        or INSTRUCTIONS[split][(index - 1) % len(INSTRUCTIONS[split])],
        "tools": ["category_ranking", "daily_net_sales"],
        "benchmark_metadata": {"split": split, "case_kind": "code_capability"},
    }
    (task_root / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(
        task_root / "orders.csv",
        ["order_id", "order_date", "category", "product", "quantity", "unit_price"],
        orders,
    )
    write_csv(
        task_root / "refunds.csv",
        ["refund_id", "order_id", "refund_date", "refund_amount"],
        refunds,
    )
    oracle_root = root / "oracle" / split
    oracle_root.mkdir(parents=True, exist_ok=True)
    oracle = {
        "case_id": case_id,
        "split": split,
        "metric_contract": dict(DEFAULT_METRIC_CONTRACT),
        "results": calculate_expected(
            orders,
            cap_refunds(orders, refunds),
            list(task["tools"]),
            dict(DEFAULT_METRIC_CONTRACT),
        ),
    }
    (oracle_root / f"{case_id}.json").write_text(
        json.dumps(oracle, ensure_ascii=False, indent=2, default=decimal_default) + "\n",
        encoding="utf-8",
    )
    return {
        "case_id": case_id,
        "split": split,
        "order_lines": len(orders),
        "refund_rows": len(refunds),
    }


def generate_benchmark(root: Path, *, count_per_split: int, seed: int) -> dict[str, object]:
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {root}")
    cases: list[dict[str, object]] = []
    for split_index, split in enumerate(("evolution", "validation", "transfer")):
        for index in range(1, count_per_split + 1):
            cases.append(
                generate_case(
                    root,
                    split=split,
                    index=index,
                    seed=seed + split_index * 10_000 + index,
                )
            )
    public_hashes = {
        str(path.relative_to(root)).replace("\\", "/"): sha256(path)
        for path in sorted((root / "tasks").rglob("*"))
        if path.is_file()
    }
    oracle_hashes = {
        str(path.relative_to(root)).replace("\\", "/"): sha256(path)
        for path in sorted((root / "oracle").rglob("*"))
        if path.is_file()
    }
    manifest = {
        "name": root.name,
        "seed": seed,
        "count": len(cases),
        "count_per_split": count_per_split,
        "cases": cases,
        "public_sha256": public_hashes,
        "oracle_sha256": oracle_hashes,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the first code-operator benchmark.")
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/retailops/code-evolution-v1")
    )
    parser.add_argument("--count-per-split", type=int, default=4)
    parser.add_argument("--seed", type=int, default=2026110321)
    args = parser.parse_args()
    try:
        manifest = generate_benchmark(
            args.output, count_per_split=args.count_per_split, seed=args.seed
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(
        f"generated code benchmark: {manifest['count']} cases in {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
