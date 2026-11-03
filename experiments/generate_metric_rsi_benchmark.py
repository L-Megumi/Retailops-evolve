from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from pathlib import Path


INSTRUCTIONS = {
    "evolution": (
        "计算退款事件重放率：相同 refund_id 在首次出现后的行属于重放，重放行数除以全部退款行数。",
        "统计售后消息重复投递比例，以事件唯一编号识别重复，分母使用输入退款流水总行数。",
        "输出 refund replay rate、总退款事件数和重复事件行数；同一事件键第一次不算重复。",
        "评估退款流的幂等质量：后续再次出现的退款编号计为 replay，并计算其占全部行的比例。",
        "按退款事件 ID 计算重复行占比，保留三项指标：total rows、duplicate rows、rate bps。",
        "生成退款消息重放监控指标，重复次数按首条之后的每一行累计，比例用基点表示。",
        "Calculate the refund replay rate: later rows with an already seen refund_id are replay rows divided by all refund rows.",
        "Report total refund rows, replayed rows after the first event key occurrence, and replay rate in basis points.",
        "Measure duplicate-delivery share in the refund event stream using exact event identifiers and all rows as denominator.",
        "Compute idempotency replay metrics for refunds; repeated occurrences after the earliest row count as duplicates.",
        "How often are refund events replayed? Use duplicate event-id rows over the complete refund feed.",
        "Return refund_replay_rate with total rows, duplicate rows, and integer basis-point rate.",
    ),
    "validation": (
        "核算退款流水重放占比；业务事件号第二次及以后出现都计入重复行，分母不去重。",
        "给出售后事件流的 replay ratio，按唯一事件引用判断，输出整数基点和计数证据。",
        "统计重复退款消息在原始消息流中的比例，首个相同 ID 为正常，后续均为重放。",
        "检查退款接口幂等性：重复事件行数 / 全部输入事件行数，并报告两个计数。",
        "计算 refund event replay 指标，不按订单聚合，只按退款事件标识识别后续副本。",
        "输出总行数、重放行数以及 duplicate/total 的 basis points，空输入按零处理。",
        "Determine the share of refund feed rows that replay a previously observed event reference.",
        "Produce an idempotency metric where every occurrence after the first matching refund key is a replay.",
        "Calculate replayed refund rows divided by raw refund rows, alongside both underlying counts.",
        "Report refund message redelivery rate in integer bps using exact refund-event identity.",
        "Quantify repeated refund-event deliveries without deduplicating the denominator first.",
        "Create refund_replay_rate evidence from total feed rows and later duplicate-id rows.",
    ),
    "audit": (
        "监控售后消息被上游重复发送的频率：同一事件凭证除首次外都算重发，除以原始流量。",
        "退款事件唯一键若已见过，则当前行记为 replay；汇总 replay 数、总数和万分比。",
        "计算事件级退款重传率，分母包含重复行本身，不能先清洗再计算。",
        "衡量退款数据源的幂等重放问题，按事件引用号识别后续副本并输出基点。",
        "需要退款流重复投递 KPI：首次键正常，其后相同键每行都进入分子。",
        "报告原始退款消息中重放消息所占比例，以及用于复核的总行数与重放行数。",
        "Measure redelivered refund messages: every row after the first use of an event key is in the numerator.",
        "What fraction of the raw refund stream consists of replays of an earlier unique event reference?",
        "Compute a refund idempotency replay KPI in bps without removing duplicate rows from the denominator.",
        "Count later deliveries of each reimbursement event key and divide by the complete incoming feed size.",
        "Return the duplicate-delivery basis-point rate plus raw and replay row counts for refund events.",
        "Assess refund source replay frequency using exact event identity, where first delivery never counts as replay.",
    ),
}


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a metric-plugin RSI benchmark.")
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/retailops/metric-rsi-v1")
    )
    parser.add_argument("--seed", type=int, default=2026110359)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    cases = []
    for split_index, split in enumerate(("evolution", "validation", "audit")):
        for index, instruction in enumerate(INSTRUCTIONS[split], start=1):
            rng = random.Random(args.seed + split_index * 10_000 + index)
            case_id = f"metric-{split}-{index:03d}"
            orders = [
                {
                    "order_id": f"{case_id}-O{order_index}",
                    "order_date": f"2026-10-{order_index:02d}",
                    "category": "办公耗材" if order_index % 2 else "数码配件",
                    "product": f"P{order_index}",
                    "quantity": 1,
                    "unit_price": f"{rng.randint(20, 300)}.00",
                }
                for order_index in range(1, 5)
            ]
            unique_count = rng.randint(4, 10)
            refunds: list[dict[str, object]] = []
            for event_index in range(unique_count):
                event = {
                    "refund_id": f"{case_id}-R{event_index:02d}",
                    "order_id": orders[rng.randrange(len(orders))]["order_id"],
                    "refund_date": f"2026-11-{rng.randint(1, 28):02d}",
                    "refund_amount": f"{rng.randint(1, 19)}.00",
                }
                refunds.append(event)
                duplicate_count = 0 if index % 6 == 0 else rng.randint(0, 2)
                refunds.extend(dict(event) for _ in range(duplicate_count))
            rng.shuffle(refunds)
            seen: set[str] = set()
            duplicates = 0
            for refund in refunds:
                refund_id = str(refund["refund_id"])
                if refund_id in seen:
                    duplicates += 1
                else:
                    seen.add(refund_id)
            total = len(refunds)
            expected = {
                "total_refund_rows": total,
                "duplicate_rows": duplicates,
                "replay_rate_bps": duplicates * 10_000 // total if total else 0,
            }
            task_root = args.output / "tasks" / split / case_id
            task_root.mkdir(parents=True, exist_ok=False)
            (task_root / "task.json").write_text(
                json.dumps(
                    {
                        "instruction": instruction,
                        "required_results": ["refund_replay_rate"],
                        "benchmark_metadata": {
                            "split": split,
                            "case_kind": "capability_gap_metric_tool",
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            _write_csv(
                task_root / "orders.csv",
                ["order_id", "order_date", "category", "product", "quantity", "unit_price"],
                orders,
            )
            _write_csv(
                task_root / "refunds.csv",
                ["refund_id", "order_id", "refund_date", "refund_amount"],
                refunds,
            )
            oracle_root = args.output / "oracle" / split
            oracle_root.mkdir(parents=True, exist_ok=True)
            (oracle_root / f"{case_id}.json").write_text(
                json.dumps({"case_id": case_id, "expected": expected}, indent=2) + "\n",
                encoding="utf-8",
            )
            cases.append({"case_id": case_id, "split": split, **expected})
    public_hashes = {
        str(path.relative_to(args.output)).replace("\\", "/"): _sha256(path)
        for path in sorted((args.output / "tasks").rglob("*"))
        if path.is_file()
    }
    oracle_hashes = {
        str(path.relative_to(args.output)).replace("\\", "/"): _sha256(path)
        for path in sorted((args.output / "oracle").rglob("*"))
        if path.is_file()
    }
    manifest = {
        "name": args.output.name,
        "seed": args.seed,
        "count": len(cases),
        "count_per_split": len(INSTRUCTIONS["evolution"]),
        "cases": cases,
        "public_sha256": public_hashes,
        "oracle_sha256": oracle_hashes,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"generated metric RSI benchmark: {len(cases)} cases in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
