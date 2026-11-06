from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:
    raise unittest.SkipTest("install service and test extras for API contract tests")

from retailops_agent.models import AnalysisPlan, RunTrace
from retailops_agent.service import create_app


class FixtureAgent:
    def run(self, task, orders, refunds, *, required_results=(), on_event=None):
        if on_event:
            on_event({"stage": "fixture", "elapsed_ms": 1})
        return RunTrace(task=task, plan=AnalysisPlan(task, ()), elapsed_ms=1)


class ServiceContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "orders.csv").write_text("fixture", encoding="utf-8")
        (self.root / "refunds.csv").write_text("fixture", encoding="utf-8")
        self.app = create_app(data_root=self.root, manifest=self.root / "unused.json",
                              state_path=self.root / "tasks.sqlite3", token="unit-token",
                              agent_factory=lambda request: FixtureAgent())
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.auth = {"Authorization": "Bearer unit-token"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def test_submit_query_and_resume_sse_events_with_fixture_agent(self):
        response = self.client.post("/tasks", headers=self.auth, json={
            "task": "fixture", "orders": "orders.csv", "refunds": "refunds.csv"})
        self.assertEqual(response.status_code, 202)
        task_id = response.json()["task_id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            item = self.client.get(f"/tasks/{task_id}", headers=self.auth).json()
            if item["status"] == "succeeded":
                break
            time.sleep(0.01)
        self.assertEqual(item["status"], "succeeded")
        self.assertEqual(item["result"]["elapsed_ms"], 1)
        stream = self.client.get(f"/tasks/{task_id}/events", headers=self.auth).text
        self.assertIn("id: 1", stream)
        self.assertIn("event: done", stream)
        resumed = self.client.get(f"/tasks/{task_id}/events", headers={**self.auth, "Last-Event-ID": "1"}).text
        self.assertNotIn("id: 1", resumed)
        self.assertIn("event: done", resumed)

    def test_auth_schema_and_data_boundary(self):
        body = {"task": "fixture", "orders": "orders.csv", "refunds": "refunds.csv"}
        self.assertEqual(self.client.post("/tasks", json=body).status_code, 401)
        self.assertEqual(self.client.post("/tasks", headers=self.auth, json={**body, "extra": 1}).status_code, 422)
        self.assertEqual(self.client.post("/tasks", headers=self.auth, json={**body, "orders": "../escape.csv"}).status_code, 400)
        self.assertEqual(self.client.post("/tasks", headers=self.auth, json={**body, "evolve": True}).status_code, 403)

    def test_status_and_unknown_task(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/tasks/unknown", headers=self.auth).status_code, 404)


if __name__ == "__main__":
    unittest.main()
