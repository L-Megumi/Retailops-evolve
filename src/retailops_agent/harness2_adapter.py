from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from harness2.core import tree
from harness2.core.proposer import Proposer, ProposerError, Reply
from harness2.core.spec import BenchPrompts, BenchSpec, ChannelSpec
from harness2.substrate.opencode import OpencodeSubstrate

from .llm import OpenAICompatibleModel


BASE_SCRIPT = '''from pathlib import Path
import sys

from retailops_agent.harness_runner import execute_policy
from retailops_agent.models import ExecutionPolicy

source = Path(sys.argv[1])
output = Path(sys.argv[2])
policy = ExecutionPolicy(
    refund_rate_denominator="order_lines",
    category_refund_allocation="first_category",
    refund_time_attribution="refund_date",
)
execute_policy(
    orders_path=source / "orders.csv",
    refunds_path=source / "refunds.csv",
    task_path=source / "task.json",
    output_path=output,
    policy=policy,
)
'''


class RetailOpsHarnessAdapter:
    """Harness² benchmark seam for verifiable retail analytics tasks."""

    def __init__(
        self,
        root: Path,
        task_root: Path,
        project_root: Path,
        channel: ChannelSpec | None = None,
    ):
        self.root = root.resolve()
        self.task_root = task_root.resolve()
        self.project_root = project_root.resolve()
        self.substrate = OpencodeSubstrate()
        channel = channel or ChannelSpec("opencode", "local/deterministic")
        self.spec = BenchSpec(
            name="retailops",
            runs_root=self.root / "runs",
            install_mode="workspace_config",
            prompts=BenchPrompts(
                work="produce correct retail metrics from order and refund files",
                inv_levers=(
                    "The editable analysis policy controls metric definitions and refund "
                    "allocation. Use execution feedback to repair business-rule violations."
                ),
                yardstick="Metrics must satisfy the stated business definitions and reconcile.",
                selection_checks="Prefer a delivered report with ok=true and no issues.",
            ),
            channels={"proposer": channel, "selector": channel},
        )
        self.execution_count = 0
        base = {name: "" for name in self.substrate.surface().always_on}
        base["systemprompt.md"] = (
            "Analyze retail order and refund data. Run scripts/analyze.py. "
            "Treat validator issues as evidence about incorrect business definitions.\n"
        )
        base["scripts/analyze.py"] = BASE_SCRIPT
        tree.write_tree(self.substrate.surface(), base, self.base_harness(), stubs=False)

    def tasks(self, split: str = "all") -> list[str]:
        return sorted(path.name for path in self.task_root.iterdir() if path.is_dir())

    def domain_of(self, task_id: str) -> str:
        return "retail-analytics"

    def task_prompt(self, task_id: str) -> str:
        task = json.loads((self.task_files(task_id) / "task.json").read_text(encoding="utf-8"))
        return str(task["instruction"])

    def task_files(self, task_id: str) -> Path:
        return self.task_root / task_id

    def base_harness(self) -> Path:
        return self.root / "base_harness"

    def validate_tree(self, harness: tree.Tree) -> None:
        script = harness.get("scripts/analyze.py", "")
        if not script:
            raise ValueError("scripts/analyze.py is required")
        if "execute_policy" not in script or "ExecutionPolicy" not in script:
            raise ValueError("scripts/analyze.py must execute a RetailOps ExecutionPolicy")

    def execute(
        self, task_id: str, harness_dir: Path | None, run_id: str, *, timeout: int | None = None
    ) -> Path:
        self.execution_count += 1
        run_dir = self.root / "executions" / run_id
        if (run_dir / "answer.json").is_file():
            return run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        script = (harness_dir or self.base_harness()) / "scripts/analyze.py"
        env = os.environ.copy()
        src = str(self.project_root / "src")
        env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                str(self.task_files(task_id)),
                str(run_dir / "answer.json"),
            ],
            capture_output=True,
            text=True,
            timeout=timeout or 20,
            env=env,
        )
        answer = (run_dir / "answer.json").read_text(encoding="utf-8") if self.delivered(run_dir) else ""
        trajectory = (
            "[ACTION] Run scripts/analyze.py against orders.csv, refunds.csv and task.json\n"
            f"[OBSERVATION]\n{answer}\n"
        )
        if result.returncode:
            trajectory += f"[ERROR]\n{result.stderr[-4000:]}\n"
        (run_dir / "trajectory.md").write_text(trajectory, encoding="utf-8")
        return run_dir

    def delivered(self, run_dir: Path) -> bool:
        return (run_dir / "answer.json").is_file()

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        return (run_dir / "trajectory.md").read_text(encoding="utf-8")[-obs_trunc:]

    def deliverable_text(self, run_dir: Path) -> str:
        return (run_dir / "answer.json").read_text(encoding="utf-8")

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        return {"answer.json": self.deliverable_text(run_dir)}

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        if not self.delivered(run_dir):
            return None
        answer = json.loads(self.deliverable_text(run_dir))
        return {"pass": bool(answer.get("ok")), "issue_count": len(answer.get("issues", []))}


class DeterministicRetailOpsProposer(Proposer):
    """Local editor that verifies the adapter before using a paid model channel."""

    def call(self, mission, context_dir, label, **kwargs) -> Reply:
        context = Path(context_dir)
        workspace = None
        if label.startswith("select."):
            candidates = sorted((context / "candidates").glob("*.md"))
            chosen = next(path.stem for path in candidates if '"ok": true' in path.read_text())
            text = f"<why>The candidate passes all business checks.</why>\n```choice\n{chosen}\n```"
        elif label.startswith("experience."):
            text = (
                "<edits>\ncomponent: scripts/analyze.py\ncandidates: inv1\n"
                "change: use distinct orders and proportional refund allocation\n"
                "observed: validator issues disappeared\nverdict: helped\n</edits>\n\n"
                "<experience>\ncue: retail refund metrics fail business checks\n"
                "lesson: use distinct order IDs and allocate cross-category refunds "
                "proportionally by line gross.\nevidence: corrected policy returned ok=true.\n"
                "confidence: high for the tested tasks\n</experience>"
            )
        else:
            workspace = context / "harness"
            script = workspace / "scripts/analyze.py"
            updated = script.read_text(encoding="utf-8")
            updated = updated.replace('refund_rate_denominator="order_lines"', 'refund_rate_denominator="distinct_orders"')
            updated = updated.replace('category_refund_allocation="first_category"', 'category_refund_allocation="proportional"')
            script.write_text(updated, encoding="utf-8")
            text = "<notes>Align the policy with the validator's explicit business definitions.</notes>"
        reply = Reply(label=label, text=text, ok=True, attempts=1, workspace=workspace)
        self._calls.append(reply)
        return reply


class DeepSeekRetailOpsProposer:
    """Direct DeepSeek policy editor that satisfies Harness2's proposer seam."""

    def __init__(
        self,
        spec: ChannelSpec,
        model: OpenAICompatibleModel,
        *,
        audit_dir: Path | None = None,
    ):
        self.spec = spec
        self.model = model
        self.audit_dir = audit_dir
        self.attempts = 2
        self.project = ""
        self.region = ""
        self._calls: list[Reply] = []

    def _record(self, reply: Reply, payload: dict[str, Any] | None = None) -> Reply:
        self._calls.append(reply)
        if self.audit_dir:
            self.audit_dir.mkdir(parents=True, exist_ok=True)
            safe_label = reply.label.replace("/", "_").replace("\\", "_")
            (self.audit_dir / f"{safe_label}.json").write_text(
                json.dumps(
                    {
                        "label": reply.label,
                        "ok": reply.ok,
                        "response": payload,
                        "usage": self.model.last_usage,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        return reply

    @staticmethod
    def _policy_from_script(script: str) -> dict[str, str]:
        allowed = {
            "refund_rate_denominator": ("order_lines", "distinct_orders"),
            "category_refund_allocation": ("first_category", "proportional"),
            "refund_time_attribution": ("refund_date", "order_date"),
        }
        policy: dict[str, str] = {}
        for field, values in allowed.items():
            match = re.search(rf'{field}="([^"]+)"', script)
            if not match or match.group(1) not in values:
                raise ProposerError(f"cannot read valid {field} from harness", attempts=1)
            policy[field] = match.group(1)
        return policy

    @staticmethod
    def _compact_evidence(context: Path) -> tuple[str, list[dict[str, object]], set[str]]:
        task_path = context / "task.md"
        task_text = (
            task_path.read_text(encoding="utf-8", errors="replace")[-1200:]
            if task_path.is_file()
            else ""
        )
        evidence: list[dict[str, object]] = []
        issue_codes: set[str] = set()
        for path in sorted((context / "rollouts").glob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                continue
            try:
                answer = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
            issues = answer.get("issues") or []
            compact_issues = [
                {
                    "code": issue.get("code"),
                    "message": issue.get("message"),
                    "expected": issue.get("expected"),
                }
                for issue in issues
                if isinstance(issue, dict)
            ]
            issue_codes.update(
                str(issue["code"]) for issue in compact_issues if issue.get("code")
            )
            evidence.append(
                {
                    "ok": answer.get("ok"),
                    "policy": answer.get("policy"),
                    "issues": compact_issues,
                }
            )
        return task_text, evidence, issue_codes

    def call(self, mission, context_dir, label, **kwargs) -> Reply:
        context = Path(context_dir)
        workspace = None
        if label.startswith("select."):
            candidates = sorted((context / "candidates").glob("*.md"))
            try:
                chosen = next(path.stem for path in candidates if '"ok": true' in path.read_text())
            except StopIteration as exc:
                raise ProposerError("no candidate passed the business validator", attempts=1) from exc
            return self._record(
                Reply(
                    label=label,
                    text=f"<why>The candidate passes all checks.</why>\n```choice\n{chosen}\n```",
                    ok=True,
                    attempts=1,
                )
            )
        if label.startswith("experience."):
            return self._record(
                Reply(
                    label=label,
                    text=(
                        "<edits>\ncomponent: scripts/analyze.py\n"
                        "change: selected policy values from validator evidence\n"
                        "observed: retained only changes that produced ok=true\n"
                        "verdict: helped\n</edits>\n\n"
                        "<experience>\ncue: retail metric validation fails\n"
                        "lesson: align policy definitions with explicit validator evidence.\n"
                        "evidence: the adopted rollout passed all checks.\n"
                        "confidence: limited to observed tasks\n</experience>"
                    ),
                    ok=True,
                    attempts=1,
                )
            )

        workspace = context / "harness"
        script = workspace / "scripts/analyze.py"
        current_policy = self._policy_from_script(script.read_text(encoding="utf-8"))
        task_text, evidence, issue_codes = self._compact_evidence(context)
        payload = self.model.complete_json(
            system=(
                "你是经营分析策略编辑器。根据执行轨迹中的校验错误选择新策略。"
                "只返回非空JSON对象，示例："
                '{"refund_rate_denominator":"distinct_orders",'
                '"category_refund_allocation":"proportional",'
                '"refund_time_attribution":"order_date","reason":"..."}。'
                "refund_rate_denominator只能是order_lines或distinct_orders；"
                "category_refund_allocation只能是first_category或proportional；"
                "refund_time_attribution只能是refund_date或order_date。"
                "只根据expected字段修复被指出的口径，未报错字段保持当前值。"
            ),
            user=json.dumps(
                {
                    "task": task_text,
                    "current_policy": current_policy,
                    "validator_evidence": evidence,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        denominator = str(payload.get("refund_rate_denominator", ""))
        allocation = str(payload.get("category_refund_allocation", ""))
        attribution = str(payload.get("refund_time_attribution", ""))
        if (
            "refund_rate_definition" in issue_codes
            and denominator not in {"order_lines", "distinct_orders"}
        ):
            raise ProposerError(f"DeepSeek returned invalid denominator: {denominator!r}", attempts=1)
        if (
            "category_refund_allocation" in issue_codes
            and allocation not in {"first_category", "proportional"}
        ):
            raise ProposerError(f"DeepSeek returned invalid allocation: {allocation!r}", attempts=1)
        if (
            "refund_time_attribution" in issue_codes
            and attribution not in {"refund_date", "order_date"}
        ):
            raise ProposerError(f"DeepSeek returned invalid attribution: {attribution!r}", attempts=1)
        selected = {
            "refund_rate_denominator": (
                denominator
                if "refund_rate_definition" in issue_codes
                else current_policy["refund_rate_denominator"]
            ),
            "category_refund_allocation": (
                allocation
                if "category_refund_allocation" in issue_codes
                else current_policy["category_refund_allocation"]
            ),
            "refund_time_attribution": (
                attribution
                if "refund_time_attribution" in issue_codes
                else current_policy["refund_time_attribution"]
            ),
        }
        updated = script.read_text(encoding="utf-8")
        for current in ("order_lines", "distinct_orders"):
            updated = updated.replace(
                f'refund_rate_denominator="{current}"',
                f'refund_rate_denominator="{selected["refund_rate_denominator"]}"',
            )
        for current in ("first_category", "proportional"):
            updated = updated.replace(
                f'category_refund_allocation="{current}"',
                f'category_refund_allocation="{selected["category_refund_allocation"]}"',
            )
        for current in ("refund_date", "order_date"):
            updated = updated.replace(
                f'refund_time_attribution="{current}"',
                f'refund_time_attribution="{selected["refund_time_attribution"]}"',
            )
        script.write_text(updated, encoding="utf-8")
        reply = Reply(
            label=label,
            text=f"<notes>{payload.get('reason', 'DeepSeek selected a validated policy.')}</notes>",
            ok=True,
            attempts=1,
            workspace=workspace,
        )
        return self._record(
            reply,
            {
                "model_response": payload,
                "applied_policy": selected,
                "issue_codes": sorted(issue_codes),
            },
        )

