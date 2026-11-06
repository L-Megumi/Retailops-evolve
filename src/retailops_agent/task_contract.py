from __future__ import annotations

import re
from dataclasses import dataclass

from .metric_catalog import METRICS


BUILTIN_RESULTS = {"daily_net_sales", "refund_rate", "category_ranking"}
CAPABILITIES = {"refund_dedup", "refund_cap"}
NEGATION = re.compile(r"\b(?:do\s+not|don't|never|without|no|not)\b|不要|不得|禁止|无需|不需要|不做|不能|勿", re.I)
BOUNDARY = re.compile(r"[;；。.!！?？]|\b(?:but|then|and)\b|然后|再|但是|但", re.I)


def affirmative_mentions(task: str, pattern: str) -> tuple[bool, bool]:
    """Scope negation to a clause, retaining an exclusion when clauses disagree."""
    positive = negative = False
    for clause in BOUNDARY.split(task):
        for match in re.finditer(pattern, clause, re.I):
            prefix = clause[:match.start()]
            denied = bool(NEGATION.search(prefix))
            if re.search(r"(?:do\s+not|don't|never)\s+(?:exceed|surpass)|(?:不得|不能|不允许|不要)\s*(?:超过|大于|越过)", prefix, re.I):
                denied = False
            if denied:
                negative = True
            else:
                positive = True
    return positive and not negative, negative


@dataclass(frozen=True)
class TaskContract:
    required_results: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    forbidden_capabilities: tuple[str, ...] = ()
    source: str = "rule"

    def __post_init__(self):
        if any(not name.isidentifier() or name.startswith("_") for name in self.required_results):
            raise ValueError("required result names must be public identifiers")
        if any(len(name) > 80 for name in self.required_results):
            raise ValueError("required result names may contain at most 80 characters")
        if not set(self.required_capabilities + self.forbidden_capabilities) <= CAPABILITIES:
            raise ValueError("unknown task capability")
        if set(self.required_capabilities) & set(self.forbidden_capabilities):
            raise ValueError("task requires and forbids the same capability")

    def to_dict(self) -> dict[str, object]:
        return {"required_results": list(self.required_results),
                "required_capabilities": list(self.required_capabilities),
                "forbidden_capabilities": list(self.forbidden_capabilities), "source": self.source}

    def with_results(self, values: tuple[str, ...]) -> "TaskContract":
        return TaskContract(tuple(sorted(set(self.required_results) | set(values))),
                            self.required_capabilities, self.forbidden_capabilities, self.source)


def infer_task_contract(task: str) -> TaskContract:
    results = []
    builtin_patterns = {
        "daily_net_sales": r"每日|按日|daily|按订单日期|退款发生日期",
        "refund_rate": r"退款率|退款订单率|退款订单占比|发生退款的订单占比|refund rate",
        "category_ranking": r"品类|类别|排行|category|ranking",
    }
    for name, pattern in builtin_patterns.items():
        if affirmative_mentions(task, pattern)[0]:
            results.append(name)
    for definition in METRICS.values():
        pattern = "|".join(re.escape(alias) for alias in definition.aliases)
        if affirmative_mentions(task, pattern)[0]:
            results.append(definition.name)
    patterns = {
        "refund_dedup": r"(?<!不)去重(?!退款事件数)|deduplicat\w*|remove\s+(?:replayed\s+or\s+)?duplicate|只(?:采信|保留).*?(?:首|最早|第一)|相同.*?保留.*?(?:首|最早|第一)",
        "refund_cap": r"累计退款上限|累计有效退款|订单级余额池|累计可退款额度|订单级退款封顶|cumulative refund cap|running (?:refund )?allowance|(?:cap|limit|ceiling).*?(?:order|refund)|(?:order|refund).*?(?:cap|limit|ceiling)|退款.*?(?:不能大于|不超过|不得超过).*?(?:成交额|销售额|货款)",
    }
    required, forbidden = [], []
    for name, pattern in patterns.items():
        positive, negative = affirmative_mentions(task, pattern)
        if negative:
            forbidden.append(name)
        elif positive:
            required.append(name)
    # Report-only requests must not mutate data merely because a cue is quoted.
    if re.search(r"report.only|only (?:report|detect)|仅(?:报告|检测)|只(?:报告|检测)|不要修改|不修改", task, re.I):
        forbidden = list(CAPABILITIES)
        required = []
    return TaskContract(tuple(sorted(set(results))), tuple(sorted(required)), tuple(sorted(forbidden)))
