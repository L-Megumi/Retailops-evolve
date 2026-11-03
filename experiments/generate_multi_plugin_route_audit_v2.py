from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


CAP = "cumulative-refund-cap-v1"
DEDUP = "refund-event-dedup-v1"

CAP_CASES = (
    "一笔交易可以分批退货，但所有批次实际认可的金额合起来只能到原购买金额，先来的批次优先。",
    "逐个处理返款申请，并从对应销售凭证的剩余本金中扣除；本金用完时裁掉越界部分。",
    "同一销售记录的历次退回款共享原始货值这一总额度，最后一次可能只兑现一部分。",
    "售后事件按到达次序占用各自交易的可返还本金，不能让任何交易最终倒欠。",
    "Partial returns for a purchase draw down one shared allowance initialized to the purchase's original value.",
    "Earlier return events take precedence; accepted value across the sale stops at the amount originally charged for merchandise.",
    "For every sale, decrement a remaining returnable principal and partially honor the request that exhausts it.",
    "A transaction's lifetime recognized returns are bounded by its initial gross value, not by an independent limit per event.",
)

DEDUP_CASES = (
    "上游可能重发同一个售后事件键；首次入账后，再收到该键时直接忽略，其他不同事件照常处理。",
    "返款引用号相同表示同一事件被重复投递，只保留文件中位置最靠前的一条。",
    "以退货事件的唯一业务键识别重放，首个版本原样通过，后续相同键不再计算。",
    "清洗售后流中的事件级重复：唯一凭证相同的取第一次，不能因为金额相等就合并不同凭证。",
    "If the return feed resends an already processed event key, preserve the earliest row for that key and ignore its replays.",
    "Treat matching reimbursement references as the same replayed event, with the first observed record winning unchanged.",
    "Keep one earliest row for each unique return authorization key while retaining separate events that merely share an amount.",
    "Suppress later deliveries of the same external refund-event identifier without grouping records by sale or date.",
)

NONE_CASES = (
    "同一事件编号重复时保留最后一条，而不是第一条。",
    "先按事件标识去重，再限制每张销售单的累计返款本金。",
    "金额、日期和销售单都相同就合并，即使售后凭证编号不同。",
    "计算每位客户每月的退款总预算，超过预算的申请延后处理。",
    "每一笔退货最多认可原交易金额，但不同退货之间不共享余额。",
    "标记被重复投递的事件供人工复核，原始事件流不得删除。",
    "对每张销售单只保留最大金额的一笔退款，其他全部删除。",
    "把负数净销售额在展示层改成零，不调整任何退款事件。",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the second frozen multi-plugin audit.")
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/retailops/multi-plugin-route-audit-v2")
    )
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)
    cases = []
    for label, expected, values in (
        ("cap", CAP, CAP_CASES),
        ("dedup", DEDUP, DEDUP_CASES),
        ("none", None, NONE_CASES),
    ):
        cases.extend(
            {
                "id": f"multi-v2-{label}-{index:03d}",
                "instruction": instruction,
                "expected_plugin_id": expected,
            }
            for index, instruction in enumerate(values, start=1)
        )
    cases_path = args.output / "cases.json"
    cases_path.write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "name": args.output.name,
        "count": len(cases),
        "cap_positive": len(CAP_CASES),
        "dedup_positive": len(DEDUP_CASES),
        "near_negative": len(NONE_CASES),
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "purpose": "post-repair multi-plugin semantic selection audit",
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"generated multi-plugin route audit v2: {len(cases)} cases in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
