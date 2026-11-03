from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.plugin_sandbox import UnsafePluginError, run_refund_plugin, validate_plugin_source


SYSTEM = (
    "Generate one safe Python refund-transform plugin. Return a JSON object with fields source "
    "and reason. source must define exactly one function named "
    "transform_refunds(order_totals_cents, refunds). It must not use imports, annotations, "
    "exceptions, helper functions, comprehensions, lambda, private names, or set(). Use a dict "
    "when membership state is needed. Allowed direct calls are dict, enumerate, int, len, list, "
    "max, min, range, sorted, str, sum. Allowed methods are append, copy, get, items, keys, "
    "setdefault, values. Return JSON-compatible refund rows with the original schema."
)


SPEC = (
    "Remove duplicate refund events by refund_id. Process input order from first to last and keep "
    "only the first occurrence of each refund_id. Preserve every retained row without changing "
    "refund_id, order_id, refund_date, or refund_cents. Do not cap amounts or group by order_id."
)


def _cases(seed: int, count: int) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for case_index in range(count):
        rng = random.Random(seed + case_index)
        unique_count = rng.randint(3, 8)
        events: list[dict[str, object]] = []
        for index in range(unique_count):
            refund_id = f"C{case_index:03d}-R{index:02d}"
            event = {
                "refund_id": refund_id,
                "order_id": f"O{rng.randint(1, 4)}",
                "refund_date": f"2026-11-{rng.randint(1, 28):02d}",
                "refund_cents": rng.randint(100, 50_000),
            }
            events.append(event)
            for _ in range(rng.randint(0, 2)):
                duplicate = dict(event)
                events.append(duplicate)
        rng.shuffle(events)
        expected: list[dict[str, object]] = []
        seen: set[str] = set()
        for event in events:
            refund_id = str(event["refund_id"])
            if refund_id not in seen:
                seen.add(refund_id)
                expected.append(dict(event))
        cases.append(
            {
                "case_id": f"dedup-{case_index + 1:03d}",
                "order_totals_cents": {f"O{index}": 10_000_000 for index in range(1, 5)},
                "refunds": events,
                "expected": expected,
            }
        )
    return cases


def _evaluate(source: str, cases: list[dict[str, object]]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    try:
        validate_plugin_source(source)
    except UnsafePluginError as exc:
        return {
            "passed": 0,
            "count": len(cases),
            "static_error": str(exc),
            "rows": [],
        }
    for case in cases:
        error = ""
        try:
            actual = run_refund_plugin(
                source,
                order_totals_cents=dict(case["order_totals_cents"]),
                refunds=list(case["refunds"]),
            )
            ok = actual == case["expected"]
            if not ok:
                error = "output did not equal first-occurrence oracle"
        except (UnsafePluginError, ValueError) as exc:
            ok = False
            error = str(exc)
        rows.append({"case_id": case["case_id"], "ok": ok, "error": error})
    return {
        "passed": sum(bool(row["ok"]) for row in rows),
        "count": len(rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate and gate a second, structurally different refund plugin."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=2026110347)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    development = _cases(args.seed, 20)
    audit = _cases(args.seed + 10_000, 20)
    model = OpenAICompatibleModel.deepseek_from_env()
    attempts: list[dict[str, object]] = []
    previous_source = ""
    previous_error = ""
    accepted_source = ""
    final_audit = None
    for attempt in range(1, 3):
        user_payload: dict[str, object] = {"capability": SPEC}
        if previous_source:
            user_payload.update(
                {
                    "previous_source": previous_source,
                    "critic_error": previous_error,
                    "repair_requirement": (
                        "Repair only the reported issue while preserving first-occurrence semantics."
                    ),
                }
            )
        response = model.complete_json(
            system=SYSTEM,
            user=json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
        )
        source = str(response.get("source", ""))
        attempt_root = args.output / f"attempt-{attempt}"
        attempt_root.mkdir(parents=True)
        (attempt_root / "proposal.json").write_text(
            json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (attempt_root / "candidate.py").write_text(source.rstrip() + "\n", encoding="utf-8")
        development_score = _evaluate(source, development)
        audit_score = None
        if development_score["passed"] == development_score["count"]:
            audit_score = _evaluate(source, audit)
        attempt_result = {
            "attempt": attempt,
            "development": development_score,
            "audit": audit_score,
        }
        (attempt_root / "result.json").write_text(
            json.dumps(attempt_result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        attempts.append(attempt_result)
        if audit_score and audit_score["passed"] == audit_score["count"]:
            accepted_source = source.rstrip() + "\n"
            final_audit = audit_score
            break
        previous_source = source
        if development_score.get("static_error"):
            previous_error = str(development_score["static_error"])
        else:
            failed = next(
                (row for row in development_score["rows"] if not row["ok"]), None
            )
            previous_error = str(failed["error"] if failed else "development gate failed")

    accepted = bool(accepted_source)
    if accepted:
        (args.output / "candidate.py").write_text(accepted_source, encoding="utf-8")
    result = {
        "accepted": accepted,
        "capability": SPEC,
        "attempts": attempts,
        "final_audit": final_audit,
        "candidate_sha256": (
            hashlib.sha256(accepted_source.encode("utf-8")).hexdigest()
            if accepted
            else None
        ),
        "model_calls": model.request_count,
        "model_usage": model.total_usage(),
    }
    (args.output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    audit_text = (
        f"{final_audit['passed']}/{final_audit['count']}" if final_audit else "not_opened"
    )
    print(
        f"dedup_plugin_accepted={accepted}, attempts={len(attempts)}, "
        f"audit={audit_text}, calls={model.request_count} -> {args.output}"
    )
    return int(not accepted)


if __name__ == "__main__":
    raise SystemExit(main())
