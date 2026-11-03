from __future__ import annotations

import argparse
import json
from pathlib import Path

from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.plugin_manager import PluginManager


def _evaluate(
    manager: PluginManager, cases: list[dict[str, object]]
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for case in cases:
        record, route = manager.resolve(str(case["instruction"]))
        actual = record.plugin_id if record else None
        expected = case.get("expected_plugin_id")
        rows.append(
            {
                "id": case["id"],
                "expected_plugin_id": expected,
                "actual_plugin_id": actual,
                "route_source": route["route_source"],
                "cache_hit": route["cache_hit"],
                "route_reason": route.get("route_reason", ""),
                "ok": actual == expected,
            }
        )
    return {
        "passed": sum(bool(row["ok"]) for row in rows),
        "count": len(rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen multi-plugin semantic selection and cache reuse."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--development",
        type=Path,
        action="append",
        help="previously opened audit that must pass before the new audit is read",
    )
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)
    model = OpenAICompatibleModel.deepseek_from_env()
    manager = PluginManager.from_manifest(
        args.manifest,
        semantic_model=model,
        cache_path=args.output / "route-cache.json",
    )
    development = None
    if args.development:
        development_cases = [
            case
            for suite in args.development
            for case in json.loads((suite / "cases.json").read_text(encoding="utf-8"))
        ]
        development = _evaluate(manager, development_cases)
    development_passed = bool(
        development is None or development["passed"] == development["count"]
    )
    first = None
    second = None
    calls_before_audit = model.request_count
    repeat_calls = 0
    if development_passed:
        cases = json.loads((args.audit / "cases.json").read_text(encoding="utf-8"))
        first = _evaluate(manager, cases)
        calls_after_first = model.request_count
        second = _evaluate(manager, cases)
        repeat_calls = model.request_count - calls_after_first
    first_calls = model.request_count - calls_before_audit - repeat_calls
    accepted = (
        development_passed
        and first is not None
        and first["passed"] == first["count"]
        and second is not None
        and second["passed"] == second["count"]
        and repeat_calls == 0
    )
    result = {
        "accepted": accepted,
        "development": development,
        "development_suites": [str(path) for path in (args.development or [])],
        "development_model_calls": calls_before_audit,
        "first_pass": first,
        "cached_repeat": second,
        "first_pass_model_calls": first_calls,
        "cached_repeat_model_calls": repeat_calls,
        "model_usage": model.total_usage(),
        "registry_version": manager.registry_version,
    }
    (args.output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    development_text = (
        f"{development['passed']}/{development['count']}" if development else "not_used"
    )
    first_text = f"{first['passed']}/{first['count']}" if first else "not_opened"
    second_text = f"{second['passed']}/{second['count']}" if second else "not_opened"
    print(
        f"multi_plugin_route_accepted={accepted}, development={development_text}, "
        f"first={first_text}, repeat={second_text}, first_calls={first_calls}, "
        f"repeat_calls={repeat_calls} -> {args.output}"
    )
    return int(not accepted)


if __name__ == "__main__":
    raise SystemExit(main())
