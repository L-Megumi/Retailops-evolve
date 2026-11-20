"""Run the full offline regression suite and retain machine-readable results."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
import time
import unittest


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    suite = unittest.TestLoader().discover("tests")
    start = time.monotonic()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.output.with_suffix(".log")
    with log_path.open("w", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    paths = sorted(Path("src/retailops_agent").glob("*.py")) + sorted(Path("tests").glob("test*.py"))
    report = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
              "skipped": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
              "elapsed_seconds": round(time.monotonic() - start, 3),
              "failure_details": [{"test": str(test), "traceback": detail} for test, detail in result.failures + result.errors],
              "source_hashes": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "source_hashes"}, ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
