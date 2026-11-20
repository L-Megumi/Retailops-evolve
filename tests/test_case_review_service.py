from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:
    raise unittest.SkipTest('install service and test extras')

from retailops_agent.service import create_app
from test_case_review import basic

ROOT=Path(__file__).resolve().parents[1]


class CaseReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.book_path=self.root/'cases.json'
        self.book_path.write_text(json.dumps(basic()),encoding='utf-8')
        self.app=create_app(data_root=self.root,manifest=ROOT/'plugins/registry.json',
                            state_path=self.root/'tasks.sqlite3',token='test-token',
                            knowledge_path=ROOT/'knowledge/retailops-policies.json')
        self.client=TestClient(self.app);self.client.__enter__()
        self.auth={'Authorization':'Bearer test-token'}

    def tearDown(self):
        self.client.__exit__(None,None,None);self.temp.cleanup()

    def submit(self, **values):
        response=self.client.post('/tasks',headers=self.auth,json={
            'mode':'case_review','task':'核查售后案例','casebook':'cases.json',**values})
        self.assertEqual(response.status_code,202,response.text)
        tid=response.json()['task_id']
        for _ in range(200):
            result=self.client.get('/tasks/'+tid,headers=self.auth).json()
            if result['status'] not in {'queued','running'}:return tid,result
            time.sleep(.01)
        self.fail('case review did not terminate')

    def test_review_runs_durably_and_sse_finishes(self):
        tid,result=self.submit()
        self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(result['result']['cases'][0]['status'],'clear')
        events=self.client.get('/tasks/'+tid+'/events',headers=self.auth).text
        self.assertIn('case_plan',events);self.assertIn('event: done',events)
        self.assertEqual(self.app.state.store.get(tid)['result']['summary']['case_count'],1)

    def test_business_anomaly_is_a_completed_review_not_runtime_failure(self):
        data=basic();data['refunds'][0]['refund_amount']='110'
        self.book_path.write_text(json.dumps(data),encoding='utf-8')
        _,result=self.submit()
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['result']['cases'][0]['status'],'needs_review')

    def test_path_scope_auth_and_mode_contract(self):
        body={'mode':'case_review','task':'核查','casebook':'cases.json'}
        self.assertEqual(self.client.post('/tasks',json=body).status_code,401)
        for value in ['../outside.json','cases.csv','missing.json']:
            response=self.client.post('/tasks',headers=self.auth,json={**body,'casebook':value})
            self.assertEqual(response.status_code,400,response.text)
        for extra in [{'casebook':None},{'orders':'orders.csv','refunds':'refunds.csv'},
                      {'platforms':['jd']},{'business_scope':'marketplace'},{'evolve':True}]:
            response=self.client.post('/tasks',headers=self.auth,json={**body,**extra})
            self.assertEqual(response.status_code,422,response.text)

    def test_requested_check_and_case_selection_survive_queue(self):
        tid,result=self.submit(required_results=['amount'],case_ids=['C1'])
        self.assertEqual(result['result']['cases'][0]['checks'],['amount'])
        _,failed=self.submit(case_ids=['UNKNOWN'])
        self.assertEqual(failed['status'],'failed')

    def test_malformed_casebook_is_a_reported_failure(self):
        self.book_path.write_text('{bad json',encoding='utf-8')
        _,result=self.submit()
        self.assertEqual(result['status'],'failed')
        self.assertTrue(result['result']['issues'])


if __name__=='__main__':unittest.main()
