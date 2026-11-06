from __future__ import annotations

import json
from typing import Protocol

from .models import AnalysisPlan
from .task_contract import CAPABILITIES, TaskContract, infer_task_contract


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
        contract = infer_task_contract(task)
        tools = [name for name in tools if name in contract.required_results]
        return AnalysisPlan(task=task, tools=tuple(dict.fromkeys(tools)), contract=contract)


class ModelPlanner:
    def __init__(self, model: ChatModel):
        self.model = model

    def plan(self, task: str) -> AnalysisPlan:
        payload = self.model.complete_json(
            system=(
                "你是电商经营分析规划器。只能选择给定工具，不计算结果。"
                '返回JSON对象，字段 tools、required_results、required_capabilities、forbidden_capabilities 都是字符串数组。'
                'required_results 为任务必须返回的指标名；未知指标也要声明，不要用旧工具替代。'
                '转换能力只允许 refund_dedup 和 refund_cap；否定要求写入 forbidden_capabilities。'
                '已知扩展指标 refund_replay_rate、unique_refund_events、average_refund_amount。'
                "可用工具："
                + ", ".join(ALLOWED_TOOLS)
            ),
            user=task,
        )
        raw_tools = payload.get("tools")
        if not isinstance(raw_tools, list):
            raise ValueError("模型计划缺少 tools 数组")
        tools = tuple(dict.fromkeys(str(name) for name in raw_tools if str(name) in ALLOWED_TOOLS))
        inferred = infer_task_contract(task)
        raw_results = payload.get("required_results", [])
        raw_required = payload.get("required_capabilities", [])
        raw_forbidden = payload.get("forbidden_capabilities", [])
        if any(not isinstance(raw, list) or any(not isinstance(value, str) for value in raw)
               for raw in (raw_results, raw_required, raw_forbidden)):
            raise ValueError("model contract fields must be string arrays")
        forbidden = set(raw_forbidden) | set(inferred.forbidden_capabilities)
        if not set(raw_required + raw_forbidden) <= CAPABILITIES:
            raise ValueError("model contract contains an unknown transform capability")
        contract = TaskContract(
            tuple(sorted(set(raw_results) | set(inferred.required_results) | set(tools))),
            tuple(sorted((set(raw_required) | set(inferred.required_capabilities)) - forbidden)),
            tuple(sorted(forbidden)), "model",
        )
        if not tools and not (contract.required_results or contract.required_capabilities):
            raise ValueError(f"模型没有选择有效工具: {json.dumps(payload, ensure_ascii=False)}")
        return AnalysisPlan(task=task, tools=tools, contract=contract)

