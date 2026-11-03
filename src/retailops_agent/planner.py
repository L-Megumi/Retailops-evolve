from __future__ import annotations

import json
from typing import Protocol

from .models import AnalysisPlan


ALLOWED_TOOLS = ("daily_net_sales", "refund_rate", "category_ranking")


class ChatModel(Protocol):
    def complete_json(self, *, system: str, user: str) -> dict[str, object]: ...


class RulePlanner:
    """Deterministic baseline used before connecting a model planner."""

    def plan(self, task: str) -> AnalysisPlan:
        normalized = task.lower()
        tools: list[str] = []
        if any(
            term in normalized
            for term in ("每日", "按日", "daily", "按订单日期", "退款发生日期")
        ):
            tools.append("daily_net_sales")
        if any(
            term in normalized
            for term in (
                "退款率",
                "退款订单率",
                "退款订单占比",
                "发生退款的订单占比",
                "refund rate",
            )
        ):
            tools.append("refund_rate")
        if any(term in normalized for term in ("品类", "类别", "排行", "category", "ranking")):
            tools.append("category_ranking")
        if not tools:
            tools = list(ALLOWED_TOOLS)
        return AnalysisPlan(task=task, tools=tuple(dict.fromkeys(tools)))


class ModelPlanner:
    def __init__(self, model: ChatModel):
        self.model = model

    def plan(self, task: str) -> AnalysisPlan:
        payload = self.model.complete_json(
            system=(
                "你是电商经营分析规划器。只能选择给定工具，不计算结果。"
                '返回JSON对象，例如 {"tools":["refund_rate"]}；字段tools是字符串数组。'
                "可用工具："
                + ", ".join(ALLOWED_TOOLS)
            ),
            user=task,
        )
        raw_tools = payload.get("tools")
        if not isinstance(raw_tools, list):
            raise ValueError("模型计划缺少 tools 数组")
        tools = tuple(dict.fromkeys(str(name) for name in raw_tools if str(name) in ALLOWED_TOOLS))
        if not tools:
            raise ValueError(f"模型没有选择有效工具: {json.dumps(payload, ensure_ascii=False)}")
        return AnalysisPlan(task=task, tools=tools)

