from __future__ import annotations

from decimal import Decimal

from .models import RunTrace


def _display(value: object) -> str:
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    return str(value)


def _table(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "_无数据_"
    columns = list(rows[0])
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(_display(row[column]) for column in columns) + " |" for row in rows]
    return "\n".join([header, separator, *body])


def render_markdown(trace: RunTrace) -> str:
    lines = ["# 经营分析报告", "", f"任务：{trace.task}", ""]
    if trace.issues:
        lines.extend(["## 校验问题", ""])
        lines.extend(f"- `{issue.code}`：{issue.message}" for issue in trace.issues)
        lines.append("")

    for name, result in trace.results.items():
        lines.extend([f"## {name}", ""])
        rows = result.data if isinstance(result.data, list) else [result.data]
        lines.extend([_table(rows), ""])

    lines.extend(["## 执行摘要", "", f"- 执行尝试：{trace.attempts}"])
    lines.append(f"- 最终状态：{'通过' if trace.ok else '未通过'}")
    lines.append(f"- 使用工具：{', '.join(trace.plan.tools)}")
    if trace.plugin:
        pipeline = trace.plugin.get("pipeline")
        if isinstance(pipeline, list) and len(pipeline) > 1:
            identities = [
                f"{step.get('plugin_id')}@{step.get('version')}"
                for step in pipeline
                if isinstance(step, dict)
            ]
            lines.append(f"- 插件流水线：{' → '.join(identities)}")
            lines.append(f"- 插件路由：{trace.plugin.get('route_source', 'unknown')}")
            lines.append(f"- 路由缓存：{'命中' if trace.plugin.get('cache_hit') else '未命中'}")
        elif trace.plugin.get("plugin_id"):
            lines.append(
                "- 代码插件："
                f"{trace.plugin['plugin_id']}@{trace.plugin.get('plugin_version', '')}"
            )
            lines.append(f"- 插件路由：{trace.plugin.get('route_source', 'unknown')}")
            lines.append(f"- 路由缓存：{'命中' if trace.plugin.get('cache_hit') else '未命中'}")
            if trace.plugin.get("rollback_from"):
                lines.append(
                    f"- 自动回滚：{trace.plugin['rollback_from']} → "
                    f"{trace.plugin.get('plugin_version')}"
                )
        else:
            lines.append(
                f"- 代码插件：未匹配（{trace.plugin.get('route_source', 'no_match')}）"
            )
    if trace.policies:
        lines.append(f"- 初始策略：{trace.policies[0].to_dict()}")
        lines.append(f"- 最终策略：{trace.policies[-1].to_dict()}")
    repairs = [event for event in trace.events if event.get("stage") == "repair"]
    for index, repair in enumerate(repairs, start=1):
        lines.append(f"- 第{index}轮修复：{'；'.join(repair['changes'])}")
    return "\n".join(lines) + "\n"

