from __future__ import annotations

import multiprocessing
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from retailops_agent.plugin_manager import PluginManager
from retailops_agent.task_store import TaskStore
from tests.test_plugin_manager import GOOD_SOURCE, install


def cache_writer(values):
    path, index = values
    manager = PluginManager(cache_path=Path(path))
    install(manager, GOOD_SOURCE, "1")
    for iteration in range(4):
        manager.prime_route_cache(f"worker-{index}-task-{iteration}", ("refund-cap",), reason="process test")
    return True


class TaskStoreTests(unittest.TestCase):
    def test_only_one_worker_can_claim_a_task(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.sqlite3")
            task_id = store.create({"task": "fixture"})
            with ThreadPoolExecutor(max_workers=8) as pool:
                claimed = list(pool.map(lambda index: store.claim(task_id, str(index)), range(16)))
            self.assertEqual(sum(claimed), 1)

    def test_events_and_results_survive_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite3"
            store = TaskStore(path)
            task_id = store.create({"task": "fixture"})
            self.assertTrue(store.claim(task_id, "owner"))
            for index in range(3):
                store.append(task_id, {"step": index}, "owner")
            self.assertFalse(store.finish(task_id, "other", result={"ok": True}))
            self.assertTrue(store.finish(task_id, "owner", result={"ok": True}))
            reopened = TaskStore(path)
            self.assertEqual(reopened.get(task_id)["status"], "succeeded")
            self.assertEqual([row["id"] for row in reopened.events(task_id, after=1)], [2, 3])

    def test_recovery_marks_stale_jobs_without_replaying_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.sqlite3")
            stale, live = store.create({}), store.create({})
            store.claim(stale, "dead")
            store.claim(live, "alive")
            with store.connect() as db:
                db.execute("UPDATE tasks SET updated=? WHERE id=?", (time.time() - 1000, stale))
            self.assertEqual(store.recover_stale(), 1)
            self.assertEqual(store.get(stale)["status"], "interrupted")
            self.assertEqual(store.get(live)["status"], "running")
            self.assertFalse(store.claim(stale, "new"))
            with self.assertRaises(RuntimeError):
                store.append(stale, {"unexpected": True}, "dead")

    def test_json_route_cache_merges_independent_process_writes(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            with multiprocessing.get_context("spawn").Pool(4) as pool:
                result = pool.map(cache_writer, [(str(path), index) for index in range(4)])
            self.assertTrue(all(result))
            self.assertEqual(len(json.loads(path.read_text())["entries"]), 16)


if __name__ == "__main__":
    unittest.main()
