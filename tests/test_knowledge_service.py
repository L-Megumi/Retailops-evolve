from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:
    raise unittest.SkipTest("install service and test extras")

from retailops_agent.service import create_app


ROOT = Path(__file__).resolve().parents[1]


class KnowledgeServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.app = create_app(data_root=self.root, manifest=ROOT / "plugins/registry.json",
                              state_path=self.root / "tasks.sqlite3", token="test-token",
                              knowledge_path=ROOT / "knowledge/retailops-policies.json")
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.auth = {"Authorization": "Bearer test-token"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def run_task(self, body):
        response = self.client.post("/tasks", headers=self.auth, json={"mode": "assistant", **body})
        self.assertEqual(response.status_code, 202, response.text)
        task_id = response.json()["task_id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = self.client.get(f"/tasks/{task_id}", headers=self.auth).json()
            if task["status"] not in {"running", "queued"}:
                break
            time.sleep(0.02)
        self.assertNotIn(task["status"], {"running", "queued"})
        return task_id, task

    def test_policy_question_without_csv_has_scoped_citations_and_sse(self):
        task_id, task = self.run_task({"task": "苏宁商品确有质量问题，退换货运费由谁承担？",
                                       "platforms": ["suning"]})
        self.assertEqual(task["status"], "succeeded", task)
        self.assertTrue(task["result"]["citations"])
        self.assertEqual({c["platform"] for c in task["result"]["citations"]}, {"suning"})
        stream = self.client.get(f"/tasks/{task_id}/events", headers=self.auth).text
        self.assertIn("event: trace", stream)
        self.assertIn("event: done", stream)

    def test_missing_platform_is_a_clarification_not_worker_failure(self):
        task_id, task = self.run_task({"task": "退货运费由谁承担？"})
        self.assertEqual(task["status"], "needs_input", task)
        self.assertEqual(task["result"]["status"], "needs_clarification")
        self.assertTrue(task["result"]["missing_information"])
        stream = self.client.get(f"/tasks/{task_id}/events", headers=self.auth).text
        self.assertIn("needs_input", stream)
        self.assertIn("event: done", stream)

    def test_scope_schema_auth_and_legacy_input_contract(self):
        body = {"mode": "assistant", "task": "京东退货运费"}
        self.assertEqual(self.client.post("/tasks", json=body).status_code, 401)
        for extra in ({"platforms": ["unknown"]}, {"as_of": "2026-99-99"}, {"orders": "only.csv"}):
            self.assertEqual(self.client.post("/tasks", headers=self.auth, json={**body, **extra}).status_code, 422)
        self.assertEqual(self.client.post("/tasks", headers=self.auth,
                                        json={"task": "计算退款率"}).status_code, 422)
        bad_path = {**body, "orders": "../orders.csv", "refunds": "refunds.csv"}
        self.assertEqual(self.client.post("/tasks", headers=self.auth, json=bad_path).status_code, 400)

    def test_unconfigured_knowledge_rejects_before_queueing(self):
        app = create_app(data_root=self.root, manifest=ROOT / "plugins/registry.json",
                         state_path=self.root / "other.sqlite3", token="test-token")
        with TestClient(app) as client:
            response = client.post("/tasks", headers=self.auth,
                                   json={"mode": "assistant", "task": "京东退货规则"})
            self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
