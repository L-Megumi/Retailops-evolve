from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from itertools import product
from pathlib import Path

from retailops_agent.contracts import normalize_metric_contract

try:
    from .generate_holdout import calculate_expected, decimal_default, sha256, write_csv
except ImportError:  # pragma: no cover - direct script execution
    from generate_holdout import calculate_expected, decimal_default, sha256, write_csv


SCENARIOS: tuple[tuple[str, list[str]], ...] = (
    ("refund_rate", ["refund_rate"]),
    ("category", ["category_ranking"]),
    ("daily", ["daily_net_sales"]),
    ("all_metrics", ["refund_rate", "category_ranking", "daily_net_sales"]),
)
CONTRACT_COMBINATIONS = tuple(
    {
        "refund_rate_denominator": denominator,
        "category_refund_allocation": allocation,
        "refund_time_attribution": attribution,
    }
    for denominator, allocation, attribution in product(
        ("distinct_orders", "order_lines"),
        ("proportional", "first_category"),
        ("refund_date", "order_date"),
    )
)
CATEGORIES = (
    ("电子产品", "显示器"),
    ("办公耗材", "打印纸"),
    ("办公家具", "升降桌"),
    ("网络设备", "交换机"),
    ("仓储用品", "货架"),
    ("清洁用品", "清洁剂"),
    ("包装材料", "纸箱"),
    ("照明设备", "台灯"),
)
STYLE_BY_SPLIT = {
    "evolution": ("direct", "ops_memo", "worked_example", "negated_override"),
    "validation": ("worked_example", "negated_override", "formula", "policy_ticket"),
    "transfer": ("bilingual", "formula", "compact_alias", "policy_ticket"),
}
DATA_PROFILES = ("dense", "multi_refund", "cross_period", "shuffled")


def _relevant_fields(tools: list[str]) -> list[str]:
    fields: list[str] = []
    if "refund_rate" in tools:
        fields.append("refund_rate_denominator")
    if "category_ranking" in tools:
        fields.append("category_refund_allocation")
    if "daily_net_sales" in tools:
        fields.append("refund_time_attribution")
    return fields


def _opposite(field: str, value: str) -> str:
    alternatives = {
        "refund_rate_denominator": {
            "distinct_orders": "order_lines",
            "order_lines": "distinct_orders",
        },
        "category_refund_allocation": {
            "proportional": "first_category",
            "first_category": "proportional",
        },
        "refund_time_attribution": {
            "refund_date": "order_date",
            "order_date": "refund_date",
        },
    }
    return alternatives[field][value]


def _cn_rule(field: str, value: str) -> str:
    rules = {
        ("refund_rate_denominator", "distinct_orders"): (
            "退款订单率的分母先按 order_id 去重，采用唯一订单数；分子也是发生退款的唯一订单数"
        ),
        ("refund_rate_denominator", "order_lines"): (
            "退款订单率的分母采用订单商品明细行数，不对分母中的 order_id 去重；分子仍为发生退款的唯一订单数"
        ),
        ("category_refund_allocation", "proportional"): (
            "跨品类订单的退款按各品类在该订单中的成交额占比分摊"
        ),
        ("category_refund_allocation", "first_category"): (
            "跨品类订单的整笔退款全部归入该订单在输入文件中首次出现的品类，不做比例拆分"
        ),
        ("refund_time_attribution", "refund_date"): "退款计入退款实际发生日期",
        ("refund_time_attribution", "order_date"): "退款回溯计入原订单日期",
    }
    return rules[(field, value)]


def _en_rule(field: str, value: str) -> str:
    rules = {
        ("refund_rate_denominator", "distinct_orders"): (
            "deduplicate order_id for the refund-rate denominator (unique orders)"
        ),
        ("refund_rate_denominator", "order_lines"): (
            "use raw item-row count as the refund-rate denominator; the numerator remains unique refunded orders"
        ),
        ("category_refund_allocation", "proportional"): (
            "prorate each cross-category refund by category gross contribution inside that order"
        ),
        ("category_refund_allocation", "first_category"): (
            "book the whole cross-category refund to the first category encountered for that order"
        ),
        ("refund_time_attribution", "refund_date"): "recognize refunds on the actual refund date",
        ("refund_time_attribution", "order_date"): "backdate refunds to the original order date",
    }
    return rules[(field, value)]


def build_instruction(
    scenario: str,
    tools: list[str],
    contract: dict[str, str],
    style: str,
) -> str:
    outputs = {
        "refund_rate": "退款订单率",
        "category": "品类净销售额排行",
        "daily": "每日成交额、退款额与净销售额",
        "all_metrics": "退款订单率、品类净销售额排行以及每日经营汇总",
    }[scenario]
    fields = _relevant_fields(tools)
    active = [_cn_rule(field, contract[field]) for field in fields]

    if style == "direct":
        return f"请输出{outputs}。本批次口径：" + "；".join(active) + "。"
    if style == "ops_memo":
        return (
            f"经营分析备忘录｜交付：{outputs}。P0 口径覆盖系统默认值："
            + "；".join(active)
            + "。币种保持人民币，净额=成交额-退款额。"
        )
    if style == "worked_example":
        examples: list[str] = []
        if "refund_rate_denominator" in fields:
            examples.append(
                "例如 12 条明细属于 8 个订单时，分母取 8"
                if contract["refund_rate_denominator"] == "distinct_orders"
                else "例如 12 条明细属于 8 个订单时，分母仍取 12"
            )
        if "category_refund_allocation" in fields:
            examples.append(
                "例如同单 A/B 成交额为 3:1，则退款也按 3:1 拆分"
                if contract["category_refund_allocation"] == "proportional"
                else "例如同单先出现 A 后出现 B，则退款全记 A"
            )
        if "refund_time_attribution" in fields:
            examples.append(
                "例如 9 月下单、10 月退款，退款出现在 10 月的日期"
                if contract["refund_time_attribution"] == "refund_date"
                else "例如 9 月下单、10 月退款，退款回写到 9 月的订单日期"
            )
        return f"生成{outputs}。" + "；".join(examples) + "。以上示例定义本次统计口径。"
    if style == "negated_override":
        deprecated = [_cn_rule(field, _opposite(field, contract[field])) for field in fields]
        return (
            f"输出{outputs}。注意：旧模板中的“"
            + "；".join(deprecated)
            + "”已经作废，禁止沿用。本任务唯一生效的口径是：“"
            + "；".join(active)
            + "”。"
        )
    if style == "formula":
        aliases = {
            "refund_rate_denominator": (
                "COUNT(DISTINCT order_id)"
                if contract["refund_rate_denominator"] == "distinct_orders"
                else "COUNT(order_rows)"
            ),
            "category_refund_allocation": (
                "refund × category_gross / order_gross"
                if contract["category_refund_allocation"] == "proportional"
                else "100% → FIRST(category_by_input_order)"
            ),
            "refund_time_attribution": (
                "GROUP BY refund_date"
                if contract["refund_time_attribution"] == "refund_date"
                else "GROUP BY original_order_date"
            ),
        }
        formulas = [f"{field} := {aliases[field]}" for field in fields]
        return f"交付{outputs}。统计规格：" + "；".join(formulas) + "。"
    if style == "bilingual":
        return (
            f"Build the retail report for {outputs}. Binding rules: "
            + "; ".join(_en_rule(field, contract[field]) for field in fields)
            + ". Do not infer the deprecated default policy."
        )
    if style == "compact_alias":
        aliases = {
            "refund_rate_denominator": (
                "RR_BASE=UNIQUE_ORDER" 
                if contract["refund_rate_denominator"] == "distinct_orders"
                else "RR_BASE=ITEM_ROW"
            ),
            "category_refund_allocation": (
                "REFUND_SPLIT=GROSS_WEIGHT"
                if contract["category_refund_allocation"] == "proportional"
                else "REFUND_SPLIT=FIRST_SEEN_CATEGORY"
            ),
            "refund_time_attribution": (
                "REFUND_DAY=EVENT_DAY"
                if contract["refund_time_attribution"] == "refund_date"
                else "REFUND_DAY=ORDER_DAY"
            ),
        }
        return f"生成{outputs}；本工单参数 " + ", ".join(aliases[field] for field in fields) + "。"
    if style == "policy_ticket":
        numbered = " ".join(
            f"[{position}] {_cn_rule(field, contract[field])}。"
            for position, field in enumerate(fields, start=1)
        )
        return (
            f"审计工单：重算{outputs}。以下条款优先级高于历史报表与默认设置：{numbered}"
            "不得为了让总额看起来一致而替换上述定义。"
        )
    raise ValueError(f"unknown instruction style: {style}")


def _generate_rows(
    case_id: str,
    rng: random.Random,
    profile: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    order_count = rng.randint(14, 22) if profile == "dense" else rng.randint(9, 16)
    start = (
        date(2026, 9, 27) + timedelta(days=rng.randint(0, 3))
        if profile == "cross_period"
        else date(2026, 9, 1) + timedelta(days=rng.randint(0, 20))
    )
    orders: list[dict[str, object]] = []
    totals: dict[str, Decimal] = defaultdict(Decimal)
    dates: dict[str, date] = {}
    categories_by_name = dict(CATEGORIES)
    category_names = list(categories_by_name)

    for order_index in range(order_count):
        order_id = f"{case_id.upper()}-O{order_index + 1:02d}"
        order_date = start + timedelta(days=rng.randint(0, 7))
        dates[order_id] = order_date
        max_lines = 5 if profile == "dense" else 4
        line_count = rng.randint(2, max_lines) if order_index < 3 else rng.randint(1, max_lines)
        selected = rng.sample(category_names, line_count)
        for line_index, category in enumerate(selected, start=1):
            quantity = rng.randint(1, 8)
            unit_price = Decimal(rng.randint(18, 780)) + rng.choice(
                (Decimal("0.00"), Decimal("0.13"), Decimal("0.47"), Decimal("0.99"))
            )
            totals[order_id] += unit_price * quantity
            orders.append(
                {
                    "order_id": order_id,
                    "order_date": order_date.isoformat(),
                    "category": category,
                    "product": f"{categories_by_name[category]}-{line_index}",
                    "quantity": quantity,
                    "unit_price": unit_price,
                }
            )

    refund_count = min(order_count, rng.randint(4, 6) if profile == "multi_refund" else rng.randint(2, 4))
    refund_order_ids = rng.sample(list(dates), refund_count)
    refunds: list[dict[str, object]] = []
    refund_index = 1
    for order_position, order_id in enumerate(refund_order_ids):
        ratio = Decimal(rng.choice((7, 13, 19, 25, 33))) / Decimal(100)
        total_refund = (totals[order_id] * ratio).quantize(Decimal("0.01"))
        part_count = rng.randint(2, 3) if profile == "multi_refund" or order_position == 0 else 1
        remaining = total_refund
        amounts: list[Decimal] = []
        for part in range(part_count):
            amount = (
                remaining
                if part == part_count - 1
                else (total_refund / Decimal(part_count)).quantize(Decimal("0.01"))
            )
            amounts.append(amount)
            remaining -= amount
        for amount in amounts:
            lag = rng.randint(5, 12) if profile == "cross_period" else rng.randint(1, 6)
            refunds.append(
                {
                    "refund_id": f"{case_id.upper()}-R{refund_index:02d}",
                    "order_id": order_id,
                    "refund_date": (dates[order_id] + timedelta(days=lag)).isoformat(),
                    "refund_amount": amount,
                }
            )
            refund_index += 1

    if profile == "shuffled":
        rng.shuffle(orders)
        rng.shuffle(refunds)
    return orders, refunds


def calculate_capability_expected(
    orders: list[dict[str, object]],
    refunds: list[dict[str, object]],
    tools: list[str],
    metric_contract: dict[str, str],
    capability_contract: dict[str, object],
) -> dict[str, object]:
    effective_refunds = list(refunds)
    if capability_contract.get("refund_row_identity") == "deduplicate_refund_id":
        deduplicated: dict[str, dict[str, object]] = {}
        for refund in effective_refunds:
            deduplicated.setdefault(str(refund["refund_id"]), refund)
        effective_refunds = list(deduplicated.values())

    results = calculate_expected(orders, effective_refunds, tools, metric_contract)
    allocation = capability_contract.get("category_refund_allocation_v2")
    if allocation in {"equal_categories", "quantity_weighted"} and "category_ranking" in tools:
        gross: dict[str, Decimal] = defaultdict(Decimal)
        amounts_by_order: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        quantities_by_order: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        for row in orders:
            order_id = str(row["order_id"])
            category = str(row["category"])
            amount = Decimal(str(row["unit_price"])) * int(row["quantity"])
            gross[category] += amount
            amounts_by_order[order_id][category] += amount
            quantities_by_order[order_id][category] += Decimal(int(row["quantity"]))
        allocated: dict[str, Decimal] = defaultdict(Decimal)
        for refund in effective_refunds:
            order_id = str(refund["order_id"])
            refund_amount = Decimal(str(refund["refund_amount"]))
            weights = (
                {category: Decimal(1) for category in amounts_by_order[order_id]}
                if allocation == "equal_categories"
                else quantities_by_order[order_id]
            )
            entries = list(weights.items())
            total_weight = sum((weight for _, weight in entries), Decimal(0))
            remaining = refund_amount
            for position, (category, weight) in enumerate(entries):
                share = (
                    remaining
                    if position == len(entries) - 1
                    else refund_amount * weight / total_weight
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

    if capability_contract.get("refund_time_rule") == "hybrid_lag":
        threshold = int(capability_contract["threshold_days"])
        gross_by_date: dict[str, Decimal] = defaultdict(Decimal)
        refund_by_date: dict[str, Decimal] = defaultdict(Decimal)
        order_dates: dict[str, str] = {}
        for row in orders:
            order_date = str(row["order_date"])
            order_dates.setdefault(str(row["order_id"]), order_date)
            gross_by_date[order_date] += Decimal(str(row["unit_price"])) * int(row["quantity"])
        for refund in effective_refunds:
            order_date = order_dates[str(refund["order_id"])]
            refund_date = str(refund["refund_date"])
            lag = (date.fromisoformat(refund_date) - date.fromisoformat(order_date)).days
            reporting_date = order_date if lag <= threshold else refund_date
            refund_by_date[reporting_date] += Decimal(str(refund["refund_amount"]))
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


def _capability_instruction(
    family: str,
    metric_contract: dict[str, str],
    style: str,
    threshold_days: int,
) -> tuple[str, list[str], dict[str, object]]:
    if family == "equal_allocation":
        instruction = (
            "生成品类净销售额排行。同一退款订单涉及 N 个品类时，不参考成交额，"
            "退款金额按品类数做 1/N 等额拆分；尾差放到输入顺序中的最后一个品类。"
        )
        tools = ["category_ranking"]
        capability = {"category_refund_allocation_v2": "equal_categories"}
    elif family == "quantity_allocation":
        instruction = (
            "生成品类净销售额排行。跨品类退款按该订单中各品类商品数量之和加权分摊，"
            "权重使用 quantity，不得使用销售额占比。"
        )
        tools = ["category_ranking"]
        capability = {"category_refund_allocation_v2": "quantity_weighted"}
    elif family == "deduplicate_refunds":
        instruction = (
            "输出退款订单率、品类净销售额排行和每日经营汇总。退款文件可能因重复采集"
            "出现相同 refund_id；相同 refund_id 只认第一次，后续重复行不得再次扣减。其余口径："
            + "；".join(_cn_rule(field, metric_contract[field]) for field in metric_contract)
            + "。"
        )
        tools = ["refund_rate", "category_ranking", "daily_net_sales"]
        capability = {"refund_row_identity": "deduplicate_refund_id"}
    elif family == "conditional_time":
        instruction = (
            f"生成每日经营汇总。逐笔计算退款日与原订单日的自然日间隔：间隔不超过"
            f" {threshold_days} 天的退款回写订单日；超过 {threshold_days} 天的退款保留在实际退款日。"
            "该规则需要逐笔判断，不能对整张表统一选择一个日期字段。"
        )
        tools = ["daily_net_sales"]
        capability = {"refund_time_rule": "hybrid_lag", "threshold_days": threshold_days}
    else:
        raise ValueError(f"unknown capability family: {family}")

    if style in {"negated_override", "policy_ticket"}:
        instruction = "审计优先规则（覆盖历史默认口径）：" + instruction
    elif style in {"bilingual", "compact_alias"}:
        instruction += " Treat this row-level rule as binding rather than a global enum choice."
    return instruction, tools, capability


def generate_capability_case(
    public_root: Path,
    oracle_root: Path,
    *,
    split: str,
    index: int,
    seed: int,
) -> dict[str, object]:
    rng = random.Random(seed)
    families = (
        "equal_allocation",
        "quantity_allocation",
        "deduplicate_refunds",
        "conditional_time",
    )
    family = families[(index - 1) % len(families)]
    block_index = (index - 1) // len(families)
    metric_contract = normalize_metric_contract(
        CONTRACT_COMBINATIONS[block_index % len(CONTRACT_COMBINATIONS)]
    )
    style = STYLE_BY_SPLIT[split][((index - 1) + block_index) % 4]
    profile = DATA_PROFILES[((index - 1) + 2 * block_index) % 4]
    case_id = f"cap-{split}-{index:03d}"
    orders, refunds = _generate_rows(case_id, rng, profile)
    threshold_days = 3 + block_index % 3
    instruction, tools, capability_contract = _capability_instruction(
        family, metric_contract, style, threshold_days
    )

    if family == "deduplicate_refunds":
        duplicate = dict(refunds[0])
        refunds.insert(1, duplicate)
    elif family == "conditional_time":
        first_order_dates = {str(row["order_id"]): str(row["order_date"]) for row in orders}
        if len(refunds) >= 2:
            refunds[0]["refund_date"] = (
                date.fromisoformat(first_order_dates[str(refunds[0]["order_id"])])
                + timedelta(days=max(1, threshold_days - 1))
            ).isoformat()
            refunds[-1]["refund_date"] = (
                date.fromisoformat(first_order_dates[str(refunds[-1]["order_id"])])
                + timedelta(days=threshold_days + 2)
            ).isoformat()

    case_root = public_root / split / case_id
    case_root.mkdir(parents=True, exist_ok=False)
    task = {
        "instruction": instruction,
        "tools": tools,
        "benchmark_metadata": {
            "split": split,
            "difficulty": "capability_expansion",
            "case_kind": "capability_expansion",
            "instruction_style": style,
            "data_profile": profile,
        },
    }
    (case_root / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(
        case_root / "orders.csv",
        ["order_id", "order_date", "category", "product", "quantity", "unit_price"],
        orders,
    )
    write_csv(
        case_root / "refunds.csv",
        ["refund_id", "order_id", "refund_date", "refund_amount"],
        refunds,
    )
    oracle = {
        "case_id": case_id,
        "split": split,
        "scenario": family,
        "metric_contract": metric_contract,
        "capability_contract": capability_contract,
        "relevant_contract_fields": _relevant_fields(tools),
        "results": calculate_capability_expected(
            orders, refunds, tools, metric_contract, capability_contract
        ),
    }
    oracle_root.mkdir(parents=True, exist_ok=True)
    (oracle_root / f"{case_id}.json").write_text(
        json.dumps(oracle, ensure_ascii=False, indent=2, default=decimal_default) + "\n",
        encoding="utf-8",
    )
    return {
        "case_id": case_id,
        "split": split,
        "scenario": family,
        "case_kind": "capability_expansion",
        "capability_family": family,
        "instruction_style": style,
        "data_profile": profile,
        "orders": len({str(row["order_id"]) for row in orders}),
        "order_lines": len(orders),
        "refund_rows": len(refunds),
    }


def generate_challenge_case(
    public_root: Path,
    oracle_root: Path,
    *,
    split: str,
    index: int,
    seed: int,
) -> dict[str, object]:
    rng = random.Random(seed)
    scenario_index = (index - 1) % len(SCENARIOS)
    block_index = (index - 1) // len(SCENARIOS)
    scenario, tools = SCENARIOS[scenario_index]
    contract = normalize_metric_contract(
        CONTRACT_COMBINATIONS[block_index % len(CONTRACT_COMBINATIONS)]
    )
    styles = STYLE_BY_SPLIT[split]
    style = styles[(scenario_index + block_index) % len(styles)]
    profile = DATA_PROFILES[(scenario_index + 2 * block_index) % len(DATA_PROFILES)]
    case_id = f"{split}-{index:03d}-{scenario}"
    orders, refunds = _generate_rows(case_id, rng, profile)
    instruction = build_instruction(scenario, tools, contract, style)

    case_root = public_root / split / case_id
    case_root.mkdir(parents=True, exist_ok=False)
    task = {
        "instruction": instruction,
        "tools": tools,
        "benchmark_metadata": {
            "split": split,
            "difficulty": "challenge",
            "case_kind": "contract_selection",
            "instruction_style": style,
            "data_profile": profile,
        },
    }
    (case_root / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(
        case_root / "orders.csv",
        ["order_id", "order_date", "category", "product", "quantity", "unit_price"],
        orders,
    )
    write_csv(
        case_root / "refunds.csv",
        ["refund_id", "order_id", "refund_date", "refund_amount"],
        refunds,
    )
    oracle_root.mkdir(parents=True, exist_ok=True)
    oracle = {
        "case_id": case_id,
        "split": split,
        "scenario": scenario,
        "metric_contract": contract,
        "relevant_contract_fields": _relevant_fields(tools),
        "results": calculate_expected(orders, refunds, tools, contract),
    }
    (oracle_root / f"{case_id}.json").write_text(
        json.dumps(oracle, ensure_ascii=False, indent=2, default=decimal_default) + "\n",
        encoding="utf-8",
    )
    return {
        "case_id": case_id,
        "split": split,
        "scenario": scenario,
        "case_kind": "contract_selection",
        "capability_family": "none",
        "instruction_style": style,
        "data_profile": profile,
        "orders": len({str(row["order_id"]) for row in orders}),
        "order_lines": len(orders),
        "refund_rows": len(refunds),
    }


def migrate_regression_cases(
    source: Path,
    public_root: Path,
    oracle_root: Path,
) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for source_case in sorted(path for path in source.iterdir() if path.is_dir()):
        task = json.loads((source_case / "task.json").read_text(encoding="utf-8"))
        expected = json.loads((source_case / "expected.json").read_text(encoding="utf-8"))
        contract = task.pop("metric_contract", expected["metric_contract"])
        task["benchmark_metadata"] = {
            "split": "regression",
            "difficulty": "legacy",
            "case_kind": "contract_selection",
            "instruction_style": "legacy_dynamic",
            "data_profile": "legacy",
        }
        target = public_root / "regression" / source_case.name
        target.mkdir(parents=True, exist_ok=False)
        (target / "task.json").write_text(
            json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        shutil.copy2(source_case / "orders.csv", target / "orders.csv")
        shutil.copy2(source_case / "refunds.csv", target / "refunds.csv")
        oracle = {
            "case_id": source_case.name,
            "split": "regression",
            "scenario": expected["scenario"],
            "metric_contract": contract,
            "relevant_contract_fields": _relevant_fields(task["tools"]),
            "results": expected["results"],
        }
        destination = oracle_root / "regression" / f"{source_case.name}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(oracle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        with (target / "orders.csv").open(encoding="utf-8-sig", newline="") as handle:
            order_rows = list(csv.DictReader(handle))
        with (target / "refunds.csv").open(encoding="utf-8-sig", newline="") as handle:
            refund_rows = list(csv.DictReader(handle))
        cases.append(
            {
                "case_id": source_case.name,
                "split": "regression",
                "scenario": expected["scenario"],
                "case_kind": "contract_selection",
                "capability_family": "none",
                "instruction_style": "legacy_dynamic",
                "data_profile": "legacy",
                "orders": len({row["order_id"] for row in order_rows}),
                "order_lines": len(order_rows),
                "refund_rows": len(refund_rows),
            }
        )
    return cases


def generate_suite(
    output: Path,
    regression_source: Path,
    *,
    evolution_count: int = 32,
    validation_count: int = 24,
    transfer_count: int = 24,
    capability_count: int = 0,
    seed: int = 2026110305,
) -> dict[str, object]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    for count in (evolution_count, validation_count, transfer_count):
        if count < len(SCENARIOS) or count % len(SCENARIOS):
            raise ValueError("all challenge split counts must be positive multiples of 4")
    if capability_count < 0 or capability_count % 4:
        raise ValueError("capability_count must be zero or a positive multiple of 4")

    public_root = output / "tasks"
    oracle_root = output / "oracle"
    cases = migrate_regression_cases(regression_source, public_root, oracle_root)
    offsets = {"evolution": 0, "validation": 10_000, "transfer": 20_000}
    counts = {
        "evolution": evolution_count,
        "validation": validation_count,
        "transfer": transfer_count,
    }
    for split, count in counts.items():
        for index in range(1, count + 1):
            cases.append(
                generate_challenge_case(
                    public_root,
                    oracle_root / split,
                    split=split,
                    index=index,
                    seed=seed + offsets[split] + index,
                )
            )
        for index in range(1, capability_count + 1):
            cases.append(
                generate_capability_case(
                    public_root,
                    oracle_root / split,
                    split=split,
                    index=index,
                    seed=seed + offsets[split] + 50_000 + index,
                )
            )

    public_hashes = {
        str(path.relative_to(output)).replace("\\", "/"): sha256(path)
        for path in sorted(public_root.rglob("*"))
        if path.is_file()
    }
    oracle_hashes = {
        str(path.relative_to(output)).replace("\\", "/"): sha256(path)
        for path in sorted(oracle_root.rglob("*"))
        if path.is_file()
    }
    split_counts = Counter(str(case["split"]) for case in cases)
    manifest = {
        "name": f"retailops-{output.name}",
        "seed": seed,
        "count": len(cases),
        "split_counts": dict(split_counts),
        "candidate_visible_root": "tasks",
        "external_oracle_root": "oracle",
        "oracle_isolation_note": (
            "Candidate processes must receive only one tasks/<split>/<case> directory. "
            "Never mount or expose the oracle directory to a candidate."
        ),
        "cases": cases,
        "public_sha256": public_hashes,
        "oracle_sha256": oracle_hashes,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a leak-resistant, split RetailOps benchmark for RSI experiments."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/retailops/rsi-v1"),
    )
    parser.add_argument(
        "--regression-source",
        type=Path,
        default=Path("benchmarks/retailops/holdout-v4"),
    )
    parser.add_argument("--evolution-count", type=int, default=32)
    parser.add_argument("--validation-count", type=int, default=24)
    parser.add_argument("--transfer-count", type=int, default=24)
    parser.add_argument(
        "--capability-count",
        type=int,
        default=0,
        help="additional capability-expansion cases per evolution/validation/transfer split",
    )
    parser.add_argument("--seed", type=int, default=2026110305)
    args = parser.parse_args()
    try:
        manifest = generate_suite(
            args.output,
            args.regression_source,
            evolution_count=args.evolution_count,
            validation_count=args.validation_count,
            transfer_count=args.transfer_count,
            capability_count=args.capability_count,
            seed=args.seed,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(
        f"generated {manifest['count']} cases in {args.output}; "
        f"splits={manifest['split_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
