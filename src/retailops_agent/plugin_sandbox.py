from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path


ALLOWED_BUILTIN_CALLS = {
    "dict",
    "enumerate",
    "int",
    "len",
    "list",
    "max",
    "min",
    "range",
    "sorted",
    "str",
    "sum",
}
ALLOWED_METHOD_CALLS = {"append", "copy", "get", "items", "keys", "setdefault", "values"}
ALLOWED_NODES = {
    ast.Add,
    ast.And,
    ast.Assign,
    ast.Attribute,
    ast.AugAssign,
    ast.BinOp,
    ast.BoolOp,
    ast.Break,
    ast.Call,
    ast.Compare,
    ast.Constant,
    ast.Continue,
    ast.Dict,
    ast.Div,
    ast.Eq,
    ast.Expr,
    ast.FloorDiv,
    ast.For,
    ast.FunctionDef,
    ast.Gt,
    ast.GtE,
    ast.If,
    ast.IfExp,
    ast.In,
    ast.List,
    ast.Load,
    ast.Lt,
    ast.LtE,
    ast.Mod,
    ast.Module,
    ast.Mult,
    ast.Name,
    ast.Not,
    ast.NotEq,
    ast.NotIn,
    ast.Or,
    ast.Pass,
    ast.Return,
    ast.Store,
    ast.Sub,
    ast.Subscript,
    ast.Tuple,
    ast.USub,
    ast.UnaryOp,
    ast.arguments,
    ast.arg,
    ast.keyword,
}


class UnsafePluginError(ValueError):
    pass


def validate_plugin_source(
    source: str,
    *,
    entrypoint: str = "transform_refunds",
    argument_names: tuple[str, ...] = ("order_totals_cents", "refunds"),
) -> ast.Module:
    if len(source) > 8_000:
        raise UnsafePluginError("plugin source exceeds 8000 characters")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UnsafePluginError(f"plugin syntax error: {exc}") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 400:
        raise UnsafePluginError("plugin AST exceeds 400 nodes")
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise UnsafePluginError("plugin must contain exactly one top-level function")
    function = tree.body[0]
    if function.name != entrypoint:
        raise UnsafePluginError(f"plugin function must be named {entrypoint}")
    if function.decorator_list or function.returns is not None:
        raise UnsafePluginError("decorators and return annotations are not allowed")
    actual_argument_names = [argument.arg for argument in function.args.args]
    if actual_argument_names != list(argument_names):
        raise UnsafePluginError(
            f"{entrypoint} arguments must be ({', '.join(argument_names)})"
        )
    if any(argument.annotation is not None for argument in function.args.args):
        raise UnsafePluginError("argument annotations are not allowed")

    for node in nodes:
        if type(node) not in ALLOWED_NODES:
            raise UnsafePluginError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise UnsafePluginError("private or dunder names are not allowed")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in ALLOWED_BUILTIN_CALLS:
                    raise UnsafePluginError(f"disallowed function call: {node.func.id}")
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr not in ALLOWED_METHOD_CALLS:
                    raise UnsafePluginError(f"disallowed method call: {node.func.attr}")
            else:
                raise UnsafePluginError("only direct safe calls are allowed")
    return tree


def run_json_plugin(
    source: str,
    *,
    entrypoint: str,
    argument_names: tuple[str, ...],
    arguments: dict[str, object],
    timeout: float = 2.0,
) -> object:
    validate_plugin_source(
        source, entrypoint=entrypoint, argument_names=argument_names
    )
    if set(arguments) != set(argument_names):
        raise UnsafePluginError("plugin arguments do not match its declared protocol")
    worker = Path(__file__).with_name("plugin_worker.py")
    payload = {
        "source": source,
        "entrypoint": entrypoint,
        "argument_names": list(argument_names),
        "arguments": arguments,
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", str(worker)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise UnsafePluginError("plugin execution timed out") from exc
    if completed.returncode:
        detail = completed.stderr.strip()[-1000:]
        raise UnsafePluginError(f"plugin worker failed: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise UnsafePluginError("plugin worker returned invalid JSON") from exc


def run_refund_plugin(
    source: str,
    *,
    order_totals_cents: dict[str, int],
    refunds: list[dict[str, object]],
    timeout: float = 2.0,
) -> list[dict[str, object]]:
    result = run_json_plugin(
        source,
        entrypoint="transform_refunds",
        argument_names=("order_totals_cents", "refunds"),
        arguments={
            "order_totals_cents": order_totals_cents,
            "refunds": refunds,
        },
        timeout=timeout,
    )
    if not isinstance(result, list):
        raise UnsafePluginError("plugin must return a list of refund rows")
    validated: list[dict[str, object]] = []
    required = {"refund_id", "order_id", "refund_date", "refund_cents"}
    for index, row in enumerate(result):
        if not isinstance(row, dict) or not required.issubset(row):
            raise UnsafePluginError(f"plugin refund row {index} has an invalid schema")
        cents = row["refund_cents"]
        if not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
            raise UnsafePluginError(f"plugin refund row {index} has invalid refund_cents")
        validated.append(
            {
                "refund_id": str(row["refund_id"]),
                "order_id": str(row["order_id"]),
                "refund_date": str(row["refund_date"]),
                "refund_cents": cents,
            }
        )
    return validated
