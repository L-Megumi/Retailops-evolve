from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PluginKind = Literal["refund_transform", "metric_tool"]


@dataclass(frozen=True)
class PluginSpec:
    """Runtime contract independent of a plugin's generated source code."""

    kind: PluginKind
    entrypoint: str
    arguments: tuple[str, ...]
    input_schema: str
    output_schema: str
    output_name: str = ""
    invariants: tuple[str, ...] = ()
    order_before: tuple[str, ...] = ()
    order_after: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    timeout_seconds: float = 2.0
    input_view: str = "effective"
    provides: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {"refund_transform", "metric_tool"}:
            raise ValueError(f"unsupported plugin kind: {self.kind}")
        if not self.entrypoint.isidentifier() or self.entrypoint.startswith("_"):
            raise ValueError("plugin entrypoint must be a public Python identifier")
        if not self.arguments or any(
            not argument.isidentifier() or argument.startswith("_")
            for argument in self.arguments
        ):
            raise ValueError("plugin arguments must be public Python identifiers")
        if len(self.arguments) != len(set(self.arguments)):
            raise ValueError("plugin arguments must be unique")
        if not self.input_schema or not self.output_schema:
            raise ValueError("plugin schemas must be named")
        schemas = {
            "refund_transform": ("retailops.refund-transform-input.v1", "retailops.refund-rows.v1"),
            "metric_tool": ("retailops.metric-input.v1", "retailops.metric-result.v1"),
        }
        if (self.input_schema, self.output_schema) != schemas[self.kind]:
            raise ValueError("plugin schema version is not supported by its adapter")
        if self.input_view not in {"raw", "effective"}:
            raise ValueError("input_view must be raw or effective")
        if self.kind == "refund_transform" and self.input_view != "effective":
            raise ValueError("refund transforms must consume the effective view")
        if not set(self.provides) <= {"refund_cap", "refund_dedup"}:
            raise ValueError("plugin declares an unsupported transform capability")
        if self.kind == "metric_tool" and self.provides:
            raise ValueError("a read-only metric cannot claim a transforming capability")
        if self.kind == "metric_tool" and not self.output_name:
            raise ValueError("metric_tool plugins require output_name")
        if self.kind == "refund_transform" and self.output_name:
            raise ValueError("refund_transform plugins cannot declare output_name")
        expected_interface = {
            "refund_transform": ("transform_refunds", ("order_totals_cents", "refunds")),
            "metric_tool": ("compute_metric", ("orders", "refunds")),
        }[self.kind]
        if (self.entrypoint, self.arguments) != expected_interface:
            raise ValueError(f"{self.kind} plugin interface does not match its adapter")
        if not 0.05 <= self.timeout_seconds <= 10:
            raise ValueError("plugin timeout must be between 0.05 and 10 seconds")
        allowed_invariants = {"order_gross_cap"}
        if any(value not in allowed_invariants for value in self.invariants):
            raise ValueError("plugin spec contains an unknown runtime invariant")
        relationships = self.order_before + self.order_after + self.conflicts
        if any(not value or len(value) > 80 for value in relationships):
            raise ValueError("plugin relationship ids must contain 1-80 characters")

    @classmethod
    def refund_transform(
        cls,
        *,
        order_before: tuple[str, ...] = (),
        order_after: tuple[str, ...] = (),
        conflicts: tuple[str, ...] = (),
        invariants: tuple[str, ...] = ("order_gross_cap",),
        timeout_seconds: float = 2.0,
        provides: tuple[str, ...] | None = None,
    ) -> "PluginSpec":
        return cls(
            kind="refund_transform",
            entrypoint="transform_refunds",
            arguments=("order_totals_cents", "refunds"),
            input_schema="retailops.refund-transform-input.v1",
            output_schema="retailops.refund-rows.v1",
            invariants=invariants,
            order_before=order_before,
            order_after=order_after,
            conflicts=conflicts,
            timeout_seconds=timeout_seconds,
            provides=provides if provides is not None else (("refund_cap",) if "order_gross_cap" in invariants else ("refund_dedup",)),
        )

    @classmethod
    def metric_tool(
        cls,
        output_name: str,
        *,
        order_before: tuple[str, ...] = (),
        order_after: tuple[str, ...] = (),
        conflicts: tuple[str, ...] = (),
        timeout_seconds: float = 2.0,
        input_view: str = "raw",
    ) -> "PluginSpec":
        return cls(
            kind="metric_tool",
            entrypoint="compute_metric",
            arguments=("orders", "refunds"),
            input_schema="retailops.metric-input.v1",
            output_schema="retailops.metric-result.v1",
            output_name=output_name,
            order_before=order_before,
            order_after=order_after,
            conflicts=conflicts,
            timeout_seconds=timeout_seconds,
            input_view=input_view,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "entrypoint": self.entrypoint,
            "arguments": list(self.arguments),
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "output_name": self.output_name,
            "invariants": list(self.invariants),
            "order_before": list(self.order_before),
            "order_after": list(self.order_after),
            "conflicts": list(self.conflicts),
            "timeout_seconds": self.timeout_seconds,
            "input_view": self.input_view,
            "provides": list(self.provides),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "PluginSpec":
        def strings(name: str) -> tuple[str, ...]:
            raw = payload.get(name, [])
            if not isinstance(raw, list):
                raise ValueError(f"plugin spec {name} must be a list")
            return tuple(str(value) for value in raw)

        return cls(
            kind=str(payload.get("kind", "")),  # type: ignore[arg-type]
            entrypoint=str(payload.get("entrypoint", "")),
            arguments=strings("arguments"),
            input_schema=str(payload.get("input_schema", "")),
            output_schema=str(payload.get("output_schema", "")),
            output_name=str(payload.get("output_name", "")),
            invariants=strings("invariants"),
            order_before=strings("order_before"),
            order_after=strings("order_after"),
            conflicts=strings("conflicts"),
            timeout_seconds=float(payload.get("timeout_seconds", 2.0)),
            input_view=str(payload.get("input_view", "raw" if payload.get("kind") == "metric_tool" else "effective")),
            provides=strings("provides") if "provides" in payload else (
                ("refund_cap",) if "order_gross_cap" in strings("invariants") else
                (("refund_dedup",) if payload.get("kind") == "refund_transform" else ())
            ),
        )
