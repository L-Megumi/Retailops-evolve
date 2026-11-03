from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .generate_code_benchmark import generate_case
    from .generate_holdout import sha256
except ImportError:  # pragma: no cover - direct script execution
    from generate_code_benchmark import generate_case
    from generate_holdout import sha256


AUDIT_INSTRUCTIONS = (
    "逐条审核退货流水，任何订单累计冲销金额都不能穿透原成交本金；触及本金边界的流水只确认剩余部分，并据此生成品类与每日净额。",
    "为每张销售单维护尚可退回的金额。处理一笔退货后减少该余额，最多减到零，后续不再形成有效退款。输出两类经营汇总。",
    "Recognized returns for one order must never exceed its original order value. Process return events sequentially, trim the event that reaches the limit, and ignore later excess events.",
    "防止退款把单笔交易扣到零以下：以该单商品总额作为可冲减本金，按照流水先后确认有效金额，再计算 category 和 daily 指标。",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a post-repair routing audit with unseen paraphrases."
    )
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/retailops/code-route-audit-v1")
    )
    parser.add_argument("--seed", type=int, default=2026110337)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    cases = [
        generate_case(
            args.output,
            split="transfer",
            index=index,
            seed=args.seed + index,
            instruction=instruction,
        )
        for index, instruction in enumerate(AUDIT_INSTRUCTIONS, start=1)
    ]
    public_hashes = {
        str(path.relative_to(args.output)).replace("\\", "/"): sha256(path)
        for path in sorted((args.output / "tasks").rglob("*"))
        if path.is_file()
    }
    oracle_hashes = {
        str(path.relative_to(args.output)).replace("\\", "/"): sha256(path)
        for path in sorted((args.output / "oracle").rglob("*"))
        if path.is_file()
    }
    manifest = {
        "name": args.output.name,
        "seed": args.seed,
        "count": len(cases),
        "count_per_split": len(cases),
        "cases": cases,
        "public_sha256": public_hashes,
        "oracle_sha256": oracle_hashes,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"generated route audit: {len(cases)} cases in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
