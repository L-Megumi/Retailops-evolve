from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .code_plugin_registry import CodePluginRegistration


class JsonModel(Protocol):
    def complete_json(self, *, system: str, user: str) -> dict[str, object]: ...


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str
    positive_definition: str
    exclusions: tuple[str, ...]


def _semantic_batch(
    model: JsonModel,
    spec: CapabilitySpec,
    items: list[tuple[str, str]],
) -> list[dict[str, object]]:
    response = model.complete_json(
        system=(
            "You are a conservative capability router. Decide whether each instruction explicitly "
            "requires the capability definition. Related vocabulary is insufficient: every essential "
            "semantic condition must be present or clearly entailed. Return one JSON object with a "
            "decisions array. Every decision must contain exactly the supplied id, a JSON boolean "
            "use_plugin, and a short reason. Preserve all ids and their order."
        ),
        user=json.dumps(
            {
                "capability_id": spec.capability_id,
                "positive_definition": spec.positive_definition,
                "explicit_exclusions": list(spec.exclusions),
                "items": [
                    {"id": item_id, "instruction": instruction}
                    for item_id, instruction in items
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    raw = response.get("decisions")
    if not isinstance(raw, list):
        raise ValueError("semantic router response must contain a decisions list")
    expected_ids = [item_id for item_id, _ in items]
    if len(raw) != len(expected_ids):
        raise ValueError("semantic router returned the wrong decision count")
    parsed: list[dict[str, object]] = []
    for expected_id, decision in zip(expected_ids, raw):
        if not isinstance(decision, dict):
            raise ValueError("semantic router decisions must be objects")
        item_id = decision.get("id")
        use_plugin = decision.get("use_plugin")
        if item_id != expected_id:
            raise ValueError("semantic router changed an item id or order")
        if not isinstance(use_plugin, bool):
            raise ValueError("semantic router use_plugin must be a JSON boolean")
        parsed.append(
            {
                "id": expected_id,
                "use_plugin": use_plugin,
                "reason": str(decision.get("reason", ""))[:300],
                "route_source": "semantic",
            }
        )
    return parsed


def route_items(
    registration: CodePluginRegistration,
    model: JsonModel,
    spec: CapabilitySpec,
    items: list[tuple[str, str]],
    *,
    batch_size: int = 24,
) -> list[dict[str, object]]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    decisions: dict[str, dict[str, object]] = {}
    unmatched: list[tuple[str, str]] = []
    for item_id, instruction in items:
        if item_id in decisions:
            raise ValueError(f"duplicate route item id: {item_id}")
        if registration.matches(instruction):
            decisions[item_id] = {
                "id": item_id,
                "use_plugin": True,
                "reason": "matched frozen literal/keyword registration",
                "route_source": "deterministic",
            }
        else:
            unmatched.append((item_id, instruction))
    for start in range(0, len(unmatched), batch_size):
        for decision in _semantic_batch(model, spec, unmatched[start : start + batch_size]):
            decisions[str(decision["id"])] = decision
    return [decisions[item_id] for item_id, _ in items]
