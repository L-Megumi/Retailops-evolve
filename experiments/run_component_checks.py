"""Run the explicitly selected component suite, never the end-to-end benchmarks."""
from __future__ import annotations

import argparse
import importlib.metadata
import platform
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retailops_agent.persistence import atomic_json


MODULES = (
    "tests.test_plugin_sandbox", "tests.test_plugin_manager", "tests.test_planner",
    "tests.test_code_plugin_registry", "tests.test_semantic_plugin_router", "tests.test_llm",
    "tests.test_hardening", "tests.test_online_evolution", "tests.test_task_store", "tests.test_service",
)


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_ids = []

    def addSuccess(self, test):
        self.passed_ids.append(test.id())
        super().addSuccess(test)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
    versions = {}
    for name in ("fastapi", "uvicorn", "pydantic", "httpx", "starlette"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not installed"
    atomic_json(args.output, {
        "scope": "selected component, security, concurrency and mocked-service tests only",
        "excluded": ["full Agent end-to-end regression", "historical benchmark re-scoring", "live model calls"],
        "modules": MODULES, "python": platform.python_version(), "dependencies": versions,
        "tests_run": result.testsRun, "passed": len(result.passed_ids),
        "failures": [{"id": test.id(), "detail": detail} for test, detail in result.failures],
        "errors": [{"id": test.id(), "detail": detail} for test, detail in result.errors],
        "skipped": [{"id": test.id(), "reason": reason} for test, reason in result.skipped],
        "passed_ids": result.passed_ids, "elapsed_seconds": round(time.monotonic() - started, 3),
    })
    return int(not result.wasSuccessful())


if __name__ == "__main__":
    raise SystemExit(main())
