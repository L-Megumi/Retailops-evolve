"""Component timings only: no Agent end-to-end run and no LLM requests."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from retailops_agent.data import load_orders, load_refunds
from retailops_agent.persistence import atomic_json
from retailops_agent.plugin_manager import PluginManager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("plugins/registry.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=24)
    args = parser.parse_args()
    if not 4 <= args.count <= 100:
        parser.error("count must be between 4 and 100")
    started = time.perf_counter()
    manager = PluginManager.from_manifest(args.manifest)
    manager.manifest_path = None
    cold_load = (time.perf_counter() - started) * 1000
    orders = load_orders(Path("data/composition-demo-orders.csv"))
    refunds = load_refunds(Path("data/composition-demo-refunds.csv"))

    def invoke(index):
        started = time.perf_counter()
        result = manager.apply("Deduplicate refunds by refund_id, apply cumulative refund cap, and calculate refund replay rate", orders, refunds)
        assert result.results["refund_replay_rate"].data["replay_rate_bps"] == 3333
        return (time.perf_counter() - started) * 1000

    rows = []
    for workers in (1, 4):
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            timings = list(pool.map(invoke, range(args.count)))
        elapsed = time.perf_counter() - started
        rows.append({"workers": workers, "count": args.count,
                     "p50_ms": round(statistics.median(timings), 3),
                     "p95_ms": round(sorted(timings)[math.ceil(len(timings) * .95) - 1], 3),
                     "throughput_per_second": round(args.count / elapsed, 3),
                     "failures": 0})
    result = {"scope": "shared-manager component test; excludes LLM, HTTP and full Agent workflow",
              "input_refund_rows": len(refunds), "model_requests": 0,
              "cold_registry_load_ms": round(cold_load, 3), "measurements": rows,
              "limitation": "one manager serializes atomic pipelines; four workers measure contention, not parallel speedup"}
    atomic_json(args.output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
