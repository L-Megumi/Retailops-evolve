from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


CAP = "cumulative-refund-cap-v1"
DEDUP = "refund-event-dedup-v1"

CAP_CASES = (
    "多次退回商品款都从该笔购买的同一份剩余货款中支取，按到达顺序直到余额耗尽。",
    "逐张销售凭证核定可返本金，历次售后共同消耗这一额度，跨过边界的申请只认余额。",
    "一项交易的所有退货合并后不能收回超过最初支付的商品金额，早到事件先处理。",
    "退货流按顺序结算，每笔销售的剩余可返还价值归零后，不再兑现该销售的后续申请。",
    "All partial returns for a purchase consume the same remaining principal, initialized from the original merchandise charge.",
    "Honor a sale's return events in arrival order until their shared lifetime allowance is exhausted, trimming the crossing event.",
    "The recognized total across repeated returns for one transaction is bounded by that transaction's initial gross value.",
    "Track a per-sale balance across events rather than resetting the allowance for each new return request.",
)

DEDUP_CASES = (
    "同一外部售后事件凭证被再次投递时，沿用最先收到的那条并丢弃重放记录。",
    "按返款事件唯一键过滤消息重放；不同唯一键即使内容相同也必须分别保留。",
    "退款消息的业务引用相同时视为同一事件，只让文件中的首次出现进入后续计算。",
    "清除事件标识相同的后续副本，首条的金额、日期、销售关联均保持原值。",
    "Accept the earliest delivery of each unique reimbursement event key and suppress subsequent deliveries of that key.",
    "A replayed return reference must not be counted twice; retain its first row without merging other references.",
    "Use the external refund-event key for idempotency, preserving the first seen payload for each key.",
    "Discard later message replays that carry an already seen return authorization identifier while preserving stream order.",
)

NONE_CASES = (
    "每条退货申请都单独获得等于原货值的额度，各申请之间不累计也不共享。",
    "重复事件凭证应以最后到达的版本为准，删除之前的版本。",
    "既要消除售后消息重放，又要按销售本金限制多次退货合计。",
    "只输出疑似重复事件清单，任何原始记录都不能删除。",
    "按客户和月份合并退款，不使用事件唯一标识。",
    "所有销售单共同使用当天退款预算，不为单笔交易维护余额。",
    "同金额同日期就认定为重复，即便两个事件拥有不同业务键。",
    "检查哪些交易的累计退款过高并报警，但不裁剪实际确认金额。",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the final frozen multi-plugin audit.")
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/retailops/multi-plugin-route-audit-v3")
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
                "id": f"multi-v3-{label}-{index:03d}",
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
        "purpose": "post-negation-repair multi-plugin semantic selection audit",
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"generated multi-plugin route audit v3: {len(cases)} cases in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
