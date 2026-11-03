from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


CAP = "cumulative-refund-cap-v1"
DEDUP = "refund-event-dedup-v1"

CAP_CASES = (
    "同一笔销售发生多次退货时，按先后次序认可，合计最多退回最初商品货款，越界那笔只取尚余部分。",
    "为每张销售单维护可退本金，售后事件逐条消耗它；本金归零以后不再确认后续金额。",
    "历史与本次退货要连续核算，每笔交易最终认可的退货总额不可超过其原始货值。",
    "一张销售凭证上的多笔返款依到达顺序结算，跨过原销售金额的那条需要裁短。",
    "Repeated returns on one sale share an allowance equal to its original merchandise value; earlier events consume it first.",
    "Recognize return events chronologically until their combined value reaches the value originally sold, partially accepting the boundary event.",
    "Maintain a remaining refundable principal for each transaction and stop recognizing value after that balance reaches zero.",
    "Across all return events for a sale, accepted value must not exceed original gross; trim only the event that crosses the limit.",
)

DEDUP_CASES = (
    "同一售后流水号反复出现时只采信最早到达的记录，其余原样重复项丢弃。",
    "清理重复导入的退货事件：业务流水标识相同的只留下第一次出现，保持原文件顺序。",
    "一个返款凭证编号可能被上游重放，只处理最先看到的那条，后续相同编号跳过。",
    "按售后事件唯一标识消除重复记录，首条保留且金额、日期和关联销售单都不能修改。",
    "When a reimbursement reference is replayed, retain its earliest event and discard later events carrying that same reference.",
    "Remove repeated return-event records using their unique event identifier, with first occurrence winning and order preserved.",
    "The ingestion feed may replay the same reimbursement key; process that key once using the earliest row unchanged.",
    "Filter exact event-identity repetitions while preserving distinct return events even when their amounts are equal.",
)

NONE_CASES = (
    "先删除重复售后流水，再把每张销售单的累计退货限制在原货值以内。",
    "单笔返款超过 500 元就转人工，其余金额保持不变。",
    "列出重复退款编号供审计，但不要删除或修改任何记录。",
    "按销售单编号合并所有退货行，不考虑售后流水标识。",
    "全店每天最多退款十万元，达到预算后暂停当天退款。",
    "找出累计退货超过成交金额的交易并告警，但保留输入金额。",
    "把金额和日期相同的退款视为重复，即使它们的事件编号不同。",
    "将每笔退款平均分摊到商品明细，退款事件数量和金额保持不变。",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a frozen two-plugin route audit.")
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/retailops/multi-plugin-route-audit-v1")
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
                "id": f"multi-route-{label}-{index:03d}",
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
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"generated multi-plugin route audit: {len(cases)} cases in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
