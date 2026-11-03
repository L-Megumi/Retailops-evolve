from __future__ import annotations

import json
import sys


SAFE_BUILTINS = {
    "dict": dict,
    "enumerate": enumerate,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "sorted": sorted,
    "str": str,
    "sum": sum,
}


def _apply_limits() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (1, 1))
        memory = 128 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    except (ImportError, OSError, ValueError):
        pass


def main() -> int:
    _apply_limits()
    payload = json.loads(sys.stdin.read())
    namespace: dict[str, object] = {"__builtins__": SAFE_BUILTINS}
    exec(compile(payload["source"], "<candidate-plugin>", "exec"), namespace, namespace)
    entrypoint = str(payload["entrypoint"])
    function = namespace[entrypoint]
    result = function(*(payload["arguments"][name] for name in payload["argument_names"]))
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
