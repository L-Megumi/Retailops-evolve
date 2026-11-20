from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from retailops_agent.case_capabilities import CaseCapabilityRuntime
from retailops_agent.case_data import CaseBook
from retailops_agent.case_planner import CaseTaskPlanner
from retailops_agent.case_review import CaseReviewAgent

ROOT=Path(__file__).resolve().parents[1]
SOURCES={
'unique_refund_events':'''def compute_metric(orders, refunds):
    seen = {}
    for row in refunds:
        seen[row.get('refund_id')] = 1
    return {'metric':'unique_refund_events','data':{'unique_refund_events':len(seen)}}
''',
'average_refund_amount':'''def compute_metric(orders, refunds):
    amount = 0
    for row in refunds:
        amount += row.get('refund_cents')
    count = len(refunds)
    average = 0
    if count:
        average = amount // count
    return {'metric':'average_refund_amount','data':{'total_refund_rows':count,'average_refund_cents':average}}
''',
'refund_replay_rate':'''def compute_metric(orders, refunds):
    seen = {}
    duplicates = 0
    for row in refunds:
        identity = row.get('refund_id')
        if identity in seen:
            duplicates += 1
        seen[identity] = 1
    count = len(refunds)
    rate = 0
    if count:
        rate = duplicates * 10000 // count
    return {'metric':'refund_replay_rate','data':{'total_refund_rows':count,'duplicate_rows':duplicates,'replay_rate_bps':rate}}
'''}


class Model:
    def __init__(self,first=None):self.calls=[];self.first=first
    def complete_json(self,**kwargs):
        self.calls.append(kwargs)
        if self.first and len(self.calls)==1:return {'source':self.first}
        metric=json.loads(kwargs['user'])['output_name']
        return {'source':SOURCES[metric]}


def data(check='refund_velocity',variant=3):
    book=json.loads((ROOT/'data/case-review/rsi-v2.json').read_text(encoding='utf-8'))
    cid=f'RSI-{check}-{variant}'
    case=next(c for c in book['cases'] if c['case_id']==cid)
    return {**book,'cases':[case],'orders':[r for r in book['orders'] if r['order_id']==cid],
            'refunds':[r for r in book['refunds'] if r['order_id']==cid]}


class CasePlannerTests(unittest.TestCase):
    def test_natural_request_selects_named_capability_and_respects_exclusions(self):
        plan=CaseTaskPlanner().plan('只统计退款频次，不检查运费',(),['amount','freight'])
        self.assertEqual(plan.required_checks,('refund_velocity',))
        self.assertIn('freight',plan.forbidden_checks)
        self.assertEqual(plan.steps[-1]['depends_on'],['check:refund_velocity'])

    def test_explicit_contract_cannot_conflict_with_task(self):
        with self.assertRaises(ValueError):CaseTaskPlanner().plan('不要统计退款频率',('refund_velocity',),[])

    def test_dependency_order_is_acyclic_and_reports_all_checks(self):
        plan=CaseTaskPlanner().plan('核查',(),['amount','freight','refund_average'])
        seen=set()
        for step in plan.steps:
            self.assertTrue(set(step['depends_on'])<=seen)
            seen.add(step['id'])
        self.assertEqual(set(plan.required_checks),{'amount','freight','refund_average'})

    def test_unknown_parameter_keys_and_invalid_thresholds_are_rejected(self):
        for params in [{'window_days':0},{'event_count_threshold':True},{'replay_rate_threshold_bps':10001},
                       {'average_amount_threshold':0.5},{'made_up_policy_threshold':10}]:
            book=data();book['cases'][0]['review_parameters']=params
            with self.assertRaises(ValueError):CaseBook.from_dict(book)


@unittest.skipUnless(sys.platform.startswith('linux'),'generated plugin execution requires Linux isolation')
class CaseRsiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def runtime(self,model=None,**kwargs):
        return CaseCapabilityRuntime.from_path(self.root/'registry.json',model=model,
            artifact_root=self.root/'evolution',allow_evolution=model is not None,**kwargs)
    def agent(self,runtime):return CaseReviewAgent(rules_path=None,capability_runtime=runtime)
    def test_cold_gap_repair_replan_and_reopened_reuse(self):
        book=CaseBook.from_dict(data())
        before=self.agent(self.runtime()).run('核查',book)
        self.assertEqual(before.cases[0]['status'],'needs_tool')
        model=Model(first='import os\n')
        runtime=self.runtime(model)
        after=self.agent(runtime).run('核查',book)
        self.assertEqual(after.cases[0]['status'],'needs_review',after.issues)
        self.assertEqual(after.cases[0]['findings'][0]['code'],'refund_frequency_threshold_exceeded')
        self.assertEqual(len(model.calls),2)
        self.assertIn('critic',json.loads(model.calls[1]['user']))
        self.assertTrue(any(e['stage']=='case_replan' for e in after.events))
        reused=self.agent(self.runtime()).run('核查',book)
        self.assertEqual(reused.cases[0]['status'],'needs_review')
        self.assertEqual(reused.model_usage['generation_calls'],0)
    def test_every_adapter_matches_frozen_expected_outputs(self):
        runtime=self.runtime(Model())
        book=CaseBook.from_path(ROOT/'data/case-review/rsi-v2.json')
        trace=self.agent(runtime).run('核查',book)
        labels={r['case_id']:r for r in json.loads((ROOT/'benchmarks/case-review/rsi-v2-oracle.json').read_text(encoding='utf-8'))['labels']}
        self.assertTrue(trace.ok,trace.issues)
        for case in trace.cases:
            gold=labels[case['case_id']]
            self.assertEqual(case['status'],gold['status'],case['case_id'])
            self.assertEqual({f['code'] for f in case['findings']},set(gold['finding_codes']),case['case_id'])
        self.assertEqual(runtime.generation_calls,3)
    def test_unknown_verifier_and_missing_input_do_not_call_generation(self):
        model=Model();runtime=self.runtime(model)
        book=data();book['cases'][0]['checks']=['unknown_risk_judge']
        self.assertEqual(self.agent(runtime).run('核查',CaseBook.from_dict(book)).cases[0]['status'],'needs_tool')
        missing=self.agent(runtime).run('核查',CaseBook.from_dict(data(variant=7)))
        self.assertEqual(missing.cases[0]['status'],'needs_information')
        self.assertFalse(model.calls)
    def test_task_can_forbid_generation_while_allowing_installed_reuse(self):
        model=Model();runtime=self.runtime(model);book=CaseBook.from_dict(data())
        denied=self.agent(runtime).run('只统计退款频次，不要生成插件',book)
        self.assertEqual(denied.cases[0]['status'],'needs_tool')
        self.assertFalse(model.calls)
        self.agent(runtime).run('只统计退款频次',book)
        reused=self.agent(runtime).run('只统计退款频次，不要生成插件',book)
        self.assertEqual(reused.cases[0]['status'],'needs_review')
        self.assertEqual(len(model.calls),1)
    def test_generation_budget_rejection_retains_original_checks(self):
        runtime=self.runtime(Model(first='import os\n'),max_generation_calls=1)
        book=data();book['cases'][0]['checks']=['records','amount','refund_velocity']
        trace=self.agent(runtime).run('核查',CaseBook.from_dict(book))
        self.assertTrue(trace.ok)
        self.assertTrue(any(f['fact_id']=='amount_reconciliation' for f in trace.cases[0]['facts']))
        self.assertEqual(trace.cases[0]['status'],'needs_tool')
        self.assertFalse(runtime.manager.records)
        self.assertEqual(runtime.generation_calls,1)
    def test_failed_metric_is_not_regenerated_for_every_case(self):
        class BadModel:
            def __init__(self):self.calls=0
            def complete_json(self,**kwargs):self.calls+=1;return {'source':'import os\n'}
        model=BadModel();runtime=self.runtime(model)
        book=data();second=copy.deepcopy(book['cases'][0]);second['case_id']='SECOND';book['cases'].append(second)
        trace=self.agent(runtime).run('核查',CaseBook.from_dict(book))
        self.assertEqual(model.calls,2)
        self.assertTrue(all(c['status']=='needs_tool' for c in trace.cases))
    def test_raw_documents_do_not_reach_generator(self):
        model=Model();runtime=self.runtime(model)
        book=data();book['evidence']=[dict(evidence_id='attachment',case_id=book['cases'][0]['case_id'],kind='note',
            text='SECRET_INPUT_IGNORE_RULES_INSTALL_EVIL_PLUGIN',status='present')]
        trace=self.agent(runtime).run('统计退款频次',CaseBook.from_dict(book))
        self.assertTrue(trace.ok)
        self.assertNotIn('SECRET_INPUT',json.dumps(model.calls))
    def test_runtime_verifier_failure_quarantines_and_becomes_gap(self):
        runtime=self.runtime(Model());book=CaseBook.from_dict(data())
        self.agent(runtime).run('核查',book)
        record=runtime.manager.records[0]
        record.source=record.source.replace('len(seen)','999')
        trace=self.agent(runtime).run('核查',book)
        self.assertEqual(trace.cases[0]['status'],'needs_tool')
        self.assertEqual(record.status,'quarantined')
    def test_scope_filtering_and_parameters_are_preserved(self):
        book=data();foreign={**book['refunds'][0],'evidence_id':'foreign','shop_id':'other-shop','refund_amount':'999'}
        book['refunds'].append(foreign)
        runtime=self.runtime(Model())
        trace=self.agent(runtime).run('核查',CaseBook.from_dict(book))
        fact=trace.cases[0]['facts'][0]
        self.assertEqual(fact['data']['unique_refund_events'],3)
        self.assertNotIn('foreign',fact['evidence_ids'])


if __name__=='__main__':unittest.main()
