from __future__ import annotations

import hashlib
import unittest

from retailops_agent.code_plugin_registry import CodePluginRegistration
from retailops_agent.semantic_plugin_router import CapabilitySpec, route_items


class FakeModel:
    def __init__(self, responses: list[dict[str, object]]):
        self.responses = list(responses)
        self.calls = 0

    def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        self.calls += 1
        return self.responses.pop(0)


def registration() -> CodePluginRegistration:
    return CodePluginRegistration(
        plugin_id="refund-cap",
        cues=("cumulative refund cap",),
        source_sha256=hashlib.sha256(b"plugin").hexdigest(),
        source_path="plugin.py",
    )


class SemanticPluginRouterTests(unittest.TestCase):
    def test_uses_deterministic_route_then_batches_unmatched_items(self) -> None:
        model = FakeModel(
            [
                {
                    "decisions": [
                        {"id": "b", "use_plugin": False, "reason": "not cumulative"},
                        {"id": "c", "use_plugin": True, "reason": "same semantics"},
                    ]
                }
            ]
        )
        decisions = route_items(
            registration(),
            model,
            CapabilitySpec("refund-cap", "definition", ("per-row cap",)),
            [
                ("a", "Use a cumulative refund cap"),
                ("b", "Alert on large refunds"),
                ("c", "Do not refund beyond the original order value"),
            ],
        )

        self.assertEqual(model.calls, 1)
        self.assertEqual(
            [row["route_source"] for row in decisions],
            ["deterministic", "semantic", "semantic"],
        )
        self.assertEqual([row["use_plugin"] for row in decisions], [True, False, True])

    def test_rejects_non_boolean_decisions(self) -> None:
        model = FakeModel(
            [{"decisions": [{"id": "x", "use_plugin": "true", "reason": "bad"}]}]
        )
        with self.assertRaisesRegex(ValueError, "JSON boolean"):
            route_items(
                registration(),
                model,
                CapabilitySpec("refund-cap", "definition", ()),
                [("x", "unmatched")],
            )


if __name__ == "__main__":
    unittest.main()
