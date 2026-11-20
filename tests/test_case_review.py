from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from retailops_agent.case_data import CaseBook
from retailops_agent.case_review import CaseReviewAgent, render_case_review
from retailops_agent.knowledge import KnowledgeStore

ROOT = Path(__file__).resolve().parents[1]


def basic():
    return {'schema_version': 1, 'dataset_kind': 'synthetic',
            'cases': [{'case_id': 'C1', 'platform': 'tmall', 'shop_id': 'S1', 'order_id': 'O1',
                       'checks': ['records', 'amount'], 'business_scope': 'platform', 'reason': 'quality'}],
            'orders': [{'evidence_id': 'E-order', 'platform': 'tmall', 'shop_id': 'S1', 'order_id': 'O1', 'paid_amount': '100'}],
            'refunds': [{'evidence_id': 'E-refund', 'platform': 'tmall', 'shop_id': 'S1', 'order_id': 'O1',
                         'refund_id': 'R1', 'refund_amount': '40', 'refund_date': '2026-11-19', 'status': 'succeeded'}],
            'evidence': []}


class Model:
    def __init__(self, response):
        self.response, self.calls, self.last_usage = response, [], {}
    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class CaseReviewTests(unittest.TestCase):
    def setUp(self):
        self.store = KnowledgeStore.from_path(ROOT/'knowledge/retailops-policies.json')

    def run_book(self, data, *, task='核查售后案例', model=None, rules=True, **kwargs):
        return CaseReviewAgent(self.store, rules_path=ROOT/'knowledge/review-rules.json' if rules else None,
                               model=model).run(task, CaseBook.from_dict(data), **kwargs)

    def family(self, number):
        book=json.loads((ROOT/'data/case-review/benchmark.json').read_text(encoding='utf-8'))
        case=next(c for c in book['cases'] if c['case_id']==f'CASE-{number:02}-0')
        key=lambda x:(x['platform'],x['shop_id'],x['order_id'])
        return {**book,'cases':[case],'orders':[o for o in book['orders'] if key(o)==key(case)],
                'refunds':[r for r in book['refunds'] if key(r)==key(case)],
                'evidence':[e for e in book['evidence'] if e['case_id']==case['case_id']]}

    def test_legitimate_partial_refunds_are_not_duplicates_or_overpayment(self):
        data=basic();data['refunds'].append({**data['refunds'][0],'evidence_id':'E2','refund_id':'R2','refund_amount':'60'})
        result=self.run_book(data)
        self.assertTrue(result.ok)
        self.assertEqual(result.cases[0]['status'],'clear')
        self.assertEqual(result.cases[0]['facts'][-1]['succeeded_refund_total'],'100')

    def test_identical_rows_are_reported_once_without_inflating_amount(self):
        data=basic();data['refunds'].append({**data['refunds'][0],'evidence_id':'E2','export_batch':'another'})
        before=copy.deepcopy(data);result=self.run_book(data).cases[0]
        self.assertEqual([f['code'] for f in result['findings']],['duplicate_refund_record'])
        self.assertEqual(result['facts'][-1]['succeeded_refund_total'],'40')
        self.assertEqual(data,before)
        self.assertNotIn('重复付款',result['findings'][0]['message'])

    def test_conflicting_amount_or_status_does_not_choose_a_record(self):
        for change in ({'refund_amount':'80'},{'status':'failed'}):
            data=basic();data['refunds'].append({**data['refunds'][0],**change,'evidence_id':'E2'})
            result=self.run_book(data).cases[0]
            self.assertIn('refund_record_conflict',[f['code'] for f in result['findings']])
            self.assertFalse(any(f['fact_id']=='amount_reconciliation' for f in result['facts']))

    def test_equivalent_decimal_format_is_not_a_conflict(self):
        data=basic();data['refunds'].append({**data['refunds'][0],'refund_amount':'40.00','evidence_id':'E2'})
        self.assertEqual([f['code'] for f in self.run_book(data).cases[0]['findings']],['duplicate_refund_record'])

    def test_refund_transaction_reuse_is_checked_across_orders(self):
        data=basic();data['refunds'][0]['refund_transaction_id']='TX1'
        data['refunds'].append({**data['refunds'][0],'refund_id':'R2','order_id':'O2','evidence_id':'E2'})
        case=self.run_book(data).cases[0]
        self.assertIn('refund_transaction_reused',[f['code'] for f in case['findings']])
        self.assertFalse(any(f['fact_id']=='amount_reconciliation' for f in case['facts']))

    def test_same_refund_id_on_different_orders_is_conflict(self):
        data=basic();data['refunds'].append({**data['refunds'][0],'order_id':'OTHER','evidence_id':'E2'})
        self.assertIn('refund_record_conflict',[f['code'] for f in self.run_book(data).cases[0]['findings']])

    def test_other_shops_and_platforms_do_not_join(self):
        for fields in ({'shop_id':'S2'},{'platform':'jd'}):
            data=basic();data['refunds'].append({**data['refunds'][0],**fields,'evidence_id':'E2','refund_amount':'1000'})
            result=self.run_book(data).cases[0]
            self.assertEqual(result['status'],'clear')
            self.assertEqual(result['facts'][-1]['succeeded_refund_total'],'40')

    def test_requested_failed_and_cancelled_refunds_are_not_paid(self):
        data=basic()
        for n,state in enumerate(['requested','approved','failed','cancelled']):
            data['refunds'].append({**data['refunds'][0],'evidence_id':f'E{n}','refund_id':f'R-{n}','refund_amount':'1000','status':state})
        result=self.run_book(data).cases[0]
        self.assertEqual(result['status'],'clear')
        self.assertEqual(result['facts'][-1]['succeeded_refund_total'],'40')

    def test_amount_uses_paid_amount_and_decimal_exactness(self):
        data=basic();data['orders'][0].update(paid_amount='0.30',unit_price='9999')
        data['refunds'][0]['refund_amount']='0.10'
        data['refunds'].append({**data['refunds'][0],'evidence_id':'E2','refund_id':'R2','refund_amount':'0.20'})
        self.assertEqual(self.run_book(data).cases[0]['status'],'clear')
        data['refunds'][1]['refund_amount']='0.21'
        finding=self.run_book(data).cases[0]['findings'][0]
        self.assertEqual(finding['details']['difference'],'0.01')

    def test_missing_paid_amount_does_not_use_list_price(self):
        data=basic();data['orders'][0].pop('paid_amount');data['orders'][0]['unit_price']='100'
        case=self.run_book(data).cases[0]
        self.assertEqual(case['status'],'needs_information')
        self.assertIn('paid_amount',[g['name'] for g in case['gaps']])

    def test_multiple_order_master_records_need_resolution(self):
        data=basic();data['orders'].append({**data['orders'][0],'evidence_id':'E2','paid_amount':'200'})
        case=self.run_book(data).cases[0]
        self.assertIn('order_record_conflict',[f['code'] for f in case['findings']])
        self.assertFalse(any(f['fact_id']=='amount_reconciliation' for f in case['facts']))

    def test_pdd_quality_condition_and_proposed_party_have_evidence(self):
        result=self.run_book(self.family(11)).cases[0]
        self.assertEqual(result['guidance'][0]['party'],'merchant')
        finding=result['findings'][0]
        self.assertEqual(finding['code'],'freight_responsibility_mismatch')
        self.assertTrue(finding['record_evidence_ids'])
        self.assertTrue(finding['policy_citation_ids'])

    def test_missing_pdd_proof_does_not_infer_audit_passed(self):
        result=self.run_book(self.family(12)).cases[0]
        self.assertFalse(result['guidance'])
        self.assertEqual({g['name'] for g in result['gaps']},{'quality_proof','platform_audit'})

    def test_other_case_evidence_cannot_satisfy_materials(self):
        data=self.family(12);other=copy.deepcopy(data['cases'][0]);other['case_id']='OTHER';data['cases'].append(other)
        data['evidence']=[dict(evidence_id='foreign-proof',case_id='OTHER',kind='quality_proof',status='confirmed'),
                          dict(evidence_id='foreign-audit',case_id='OTHER',kind='platform_audit',status='confirmed')]
        self.assertFalse(self.run_book(data).cases[0]['guidance'])

    def test_changed_policy_text_invalidates_rule_binding(self):
        corpus=json.loads((ROOT/'knowledge/retailops-policies.json').read_text(encoding='utf-8'))
        for doc in corpus['documents']:
            if doc['id']=='pdd-protection':
                for clause in doc['clauses']:
                    if clause['id']=='pdd-protection-freight':clause['text']='新版本待复核'
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'knowledge.json';path.write_text(json.dumps(corpus),encoding='utf-8')
            agent=CaseReviewAgent(KnowledgeStore.from_path(path),rules_path=ROOT/'knowledge/review-rules.json')
            result=agent.run('核查',CaseBook.from_dict(self.family(11))).cases[0]
        self.assertEqual(result['status'],'needs_policy')
        self.assertFalse(result['guidance'])

    def test_historical_date_does_not_use_undated_policy(self):
        data=self.family(13);data['cases'][0]['policy_as_of']='2025-01-01'
        self.assertEqual(self.run_book(data).cases[0]['status'],'needs_policy')

    def test_policy_metadata_change_requires_review_even_with_same_text(self):
        original=json.loads((ROOT/'knowledge/retailops-policies.json').read_text(encoding='utf-8'))
        for field,value in [('version','new-version'),('source_type','demo_sop'),('source_url','https://www.yangkeduo.com/other/')]:
            corpus=copy.deepcopy(original)
            for doc in corpus['documents']:
                if doc['id']=='pdd-protection':doc[field]=value
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'knowledge.json';path.write_text(json.dumps(corpus),encoding='utf-8')
                agent=CaseReviewAgent(KnowledgeStore.from_path(path),rules_path=ROOT/'knowledge/review-rules.json')
                result=agent.run('核查',CaseBook.from_dict(self.family(11))).cases[0]
            self.assertEqual(result['status'],'needs_policy')
            self.assertFalse(result['guidance'])

    def test_seven_day_window_requires_dates_and_intact_eligible_goods(self):
        for field,value in [('supports_seven_day',False),('goods_condition','damaged'),('received_date',None)]:
            data=self.family(13);data['orders'][0][field]=value
            result=self.run_book(data).cases[0]
            self.assertEqual(result['status'],'needs_information')
            self.assertFalse(result['guidance'])
        data=self.family(13);data['cases'][0]['application_date']='2026-11-30'
        self.assertEqual(self.run_book(data).cases[0]['status'],'needs_policy')

    def test_quality_exchange_does_not_require_return_gifts(self):
        result=self.run_book(self.family(18)).cases[0]
        self.assertEqual(result['status'],'clear')
        self.assertEqual(result['guidance'][0]['kind'],'materials')

    def test_no_gifts_or_paper_invoice_do_not_require_nonexistent_materials(self):
        data=self.family(17);data['orders'][0].update(has_gifts=False,has_paper_invoice=False)
        self.assertEqual(self.run_book(data).cases[0]['status'],'clear')

    def test_unknown_policy_and_tool_gaps_are_distinct(self):
        policy=self.run_book(self.family(19)).cases[0];tool=self.run_book(self.family(20)).cases[0]
        self.assertEqual(policy['status'],'needs_policy')
        self.assertEqual(tool['status'],'needs_tool')
        self.assertEqual(tool['gaps'][0]['next_action'],'evolution_candidate_requires_spec_and_independent_validation')

    def test_task_restrictions_and_explicit_checks_are_respected(self):
        data=self.family(11)
        result=self.run_book(data,task='只检查金额，不查政策').cases[0]
        self.assertEqual(result['checks'],['amount'])
        self.assertFalse(result['citations'])
        self.assertFalse(self.run_book(data,task='不要检查运费',required_results=('freight',)).ok)

    def test_model_cannot_change_findings_or_cross_reference_cases(self):
        model=Model({'case_id':'C1','summary':'核查完成','record_evidence_ids':['OTHER'],'policy_citation_ids':[]})
        result=self.run_book(basic(),model=model)
        self.assertEqual(result.cases[0]['status'],'clear')
        self.assertIsNone(result.cases[0]['draft_explanation'])
        self.assertTrue(any(e['stage']=='explanation_fallback' for e in result.events))

    def test_model_failure_falls_back_to_deterministic_report(self):
        result=self.run_book(basic(),model=Model(RuntimeError('provider unavailable')))
        self.assertTrue(result.ok)
        self.assertEqual(result.cases[0]['status'],'clear')

    def test_unfinished_tool_check_cannot_be_described_as_no_anomaly(self):
        data=self.family(20);cid=data['cases'][0]['case_id']
        model=Model({'case_id':cid,'summary':'本次检查未发现异常，但没有工具。','record_evidence_ids':[],'policy_citation_ids':[]})
        case=self.run_book(data,model=model).cases[0]
        self.assertEqual(case['status'],'needs_tool')
        self.assertIsNone(case['draft_explanation'])
        self.assertFalse(model.calls)
        data=self.family(11);cid=data['cases'][0]['case_id']
        model=Model({'case_id':cid,'summary':'本次未发现异常。','record_evidence_ids':[],'policy_citation_ids':[]})
        self.assertIsNone(self.run_book(data,model=model).cases[0]['draft_explanation'])

    def test_normal_review_does_not_become_payment_approval(self):
        for summary in ['审批通过，同意退款。','不代表审批通过，但已执行退款。']:
            model=Model({'case_id':'C1','summary':summary,'record_evidence_ids':['E-order'],'policy_citation_ids':[]})
            case=self.run_book(basic(),model=model).cases[0]
            self.assertEqual(case['status'],'clear')
            self.assertIsNone(case['draft_explanation'])

    def test_policy_explanation_only_receives_conditions_used_by_rule(self):
        model=Model(RuntimeError('skip narrative'))
        self.run_book(self.family(10),model=model)
        payload=json.loads(model.calls[0]['user'])
        self.assertEqual(payload['checks'],['freight'])
        self.assertNotIn('supports_seven_day',payload['facts'][0]['order_conditions'])
        self.assertIsNone(payload['facts'][0]['application_date'])

    def test_unrecognized_model_fields_do_not_enter_report(self):
        model=Model({'case_id':'C1','summary':'本次记录检查未见异常。','record_evidence_ids':['E-order'],
                     'policy_citation_ids':[],'status':'approved','run_command':'fake'})
        case=self.run_book(basic(),model=model).cases[0]
        self.assertIsNotNone(case['draft_explanation'])
        self.assertNotIn('status',case['draft_explanation'])
        self.assertNotIn('run_command',case['draft_explanation'])

    def test_model_budget_and_case_selection(self):
        data=basic();data['cases'].append({**data['cases'][0],'case_id':'C2'})
        model=Model({'case_id':'C1','summary':'记录未见异常','record_evidence_ids':['E-order'],'policy_citation_ids':[]})
        agent=CaseReviewAgent(self.store,rules_path=None,model=model,max_model_calls=1)
        result=agent.run('核查',CaseBook.from_dict(data))
        self.assertEqual(len(model.calls),1)
        self.assertEqual(result.model_usage['calls'],1)
        self.assertEqual(self.run_book(data,case_ids=('C2',)).cases[0]['case_id'],'C2')
        self.assertFalse(self.run_book(data,case_ids=('UNKNOWN',)).ok)

    def test_report_and_events_link_to_cases_without_modifying_source(self):
        trace=self.run_book(self.family(11))
        report=render_case_review(trace)
        self.assertIn('pdd-protection-freight',report)
        self.assertEqual(trace.summary['source_records_modified'],0)
        self.assertEqual(trace.summary['payment_actions'],0)
        self.assertEqual(trace.events[-1]['stage'],'review_complete')


class CaseDataTests(unittest.TestCase):
    def test_bad_money_dates_states_and_duplicate_ids_are_rejected(self):
        for bad in ['NaN','Infinity','-1','0.001',0.1,True]:
            data=basic();data['refunds'][0]['refund_amount']=bad
            with self.assertRaises(ValueError):CaseBook.from_dict(data)
        for change in [{'refund_date':'2026-02-30'},{'status':'paid_maybe'},{'evidence_id':'E-order'}]:
            data=basic();data['refunds'][0].update(change)
            with self.assertRaises(ValueError):CaseBook.from_dict(data)

    def test_missing_scope_keys_and_foreign_case_evidence_are_rejected(self):
        data=basic();data['cases'][0].pop('shop_id')
        with self.assertRaises(ValueError):CaseBook.from_dict(data)
        data=basic();data['evidence']=[dict(evidence_id='E',case_id='FOREIGN',kind='proof')]
        with self.assertRaises(ValueError):CaseBook.from_dict(data)

    def test_corpus_hash_includes_input_changes(self):
        data=basic();a=CaseBook.from_dict(data);data['orders'][0]['paid_amount']='90'
        self.assertNotEqual(a.content_hash,CaseBook.from_dict(data).content_hash)


if __name__=='__main__':unittest.main()
