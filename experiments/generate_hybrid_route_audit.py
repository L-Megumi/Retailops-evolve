from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


POSITIVE = (
    "逐单核销退货，历次已确认退回款之和至多等于该单最初货款；按流水先后吃掉额度，越界一笔仅保留余量。",
    "同一交易可能多次售后。所有售后合计不可超过这笔交易的原始商品金额，临界申请应被裁短，后到的超额申请忽略。",
    "给每个销售单建立以成交本金为初值的退款余额，依事件顺序减少至零，再基于实际确认额更新经营指标。",
    "对每个 order 的 return events 顺序结算；累计认可金额封顶为该 order 的 gross value，最后一条可部分认可。",
    "A sale can have repeated returns. Accept them chronologically until their combined value reaches the sale's original gross, partially accepting the boundary event.",
    "Maintain an order-specific refundable balance initialized from the original merchandise value; consume it in event order and reject the remainder after it reaches zero.",
    "Never recognize more return value for one transaction than was originally sold. Earlier events take priority and the event crossing the limit is trimmed.",
    "Compute accepted refunds with a per-order lifetime allowance equal to original gross sales, then use only the accepted portions downstream.",
    "历史退款与本次退款需合并校验：每个订单累计实际退款不得越过原销售本金，超过余额的部分不入账。",
    "重复退货按发生次序占用订单可退本金，累计到原单金额即止；边界记录允许只确认一部分。",
    "订单的可退余额从原始货值开始，逐条退款扣减，余额耗尽后该订单其余退款均不确认。",
    "先求每单原成交额，再按文件顺序确定每条退款的有效部分，确保该单最终确认退款总和不高于成交额。",
)

NEGATIVE = (
    "任何单笔退款不得超过 500 元，超过就转人工审核；输出每日告警数量。",
    "统计退款总额超过订单金额的可疑订单，但保留原始退款记录，不做裁剪或重算。",
    "把每个品类的每日净销售额下限设为零，负值统一显示为零。",
    "每天全店退款预算封顶 10 万元，达到预算后暂停当天所有订单的退款。",
    "按 refund_id 去重并剔除状态为 cancelled 的记录，然后生成退款明细。",
    "允许商品款之外再退运费，因此订单退款可以高于商品成交额；计算实际支出。",
    "Limit each individual return event to the configured 200-dollar threshold and flag larger events.",
    "Report orders whose refund ratio exceeds 80 percent; do not alter the accepted refund amounts.",
    "Cap the store's aggregate daily refunds, rather than maintaining a separate allowance for each order.",
    "Clamp negative category-level net revenue to zero only for dashboard presentation.",
    "核验订单折扣后的应付金额是否低于零，与退款流水无关。",
    "将每笔退款平均分摊到订单商品行，累计退款金额保持输入值不变。",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a frozen positive/negative route audit.")
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/retailops/hybrid-route-audit-v1")
    )
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)
    cases = [
        {"id": f"route-audit-pos-{index:03d}", "instruction": text, "expected": True}
        for index, text in enumerate(POSITIVE, start=1)
    ] + [
        {"id": f"route-audit-neg-{index:03d}", "instruction": text, "expected": False}
        for index, text in enumerate(NEGATIVE, start=1)
    ]
    cases_path = args.output / "cases.json"
    cases_path.write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    digest = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    manifest = {
        "name": args.output.name,
        "count": len(cases),
        "positive_count": len(POSITIVE),
        "negative_count": len(NEGATIVE),
        "cases_sha256": digest,
        "purpose": "post-freeze hybrid semantic-router audit",
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"generated hybrid route audit: {len(cases)} cases in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
