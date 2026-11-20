"""Reviewed policy adapters and 60 synthetic cases with separate manual labels.

Labels are specified here without running the reviewer. Variant 2 is frozen for
boundary regression, not a claim of unseen merchant-data generalization.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


def build_rules():
    corpus = json.loads(Path('knowledge/retailops-policies.json').read_text(encoding='utf-8-sig'))
    approved = json.loads(Path('knowledge/review-rule-sources.json').read_text(encoding='utf-8-sig'))['sources']
    bindings = {d['id'] + '::' + c['id']: {'content_hash': hashlib.sha256(c['text'].encode()).hexdigest(),
                **{k:d[k] for k in ['source_type','source_url','version','platform','business_scope']}}
                for d in corpus['documents'] for c in d['clauses']}
    rules = []
    def rule(rid, check, platform, scope, reasons, sources, **extra):
        if any(bindings[cid] != approved.get(cid) for cid in sources):
            raise ValueError('policy source changed; review policy conditions and update the approved manifest explicitly')
        rules.append(dict(id=rid, check=check, platform=platform, business_scope=scope,
                          reasons=reasons, source_bindings={cid: approved[cid] for cid in sources}, **extra))
    rule('pdd-quality-freight', 'freight', 'pinduoduo', 'platform', ['quality'],
         ['pdd-protection::pdd-protection-freight'], party='merchant', coverage='both',
         requirements=['evidence:quality_proof', 'evidence:platform_audit'])
    rule('pdd-quality-materials', 'materials', 'pinduoduo', 'platform', ['quality'],
         ['pdd-protection::pdd-protection-freight'], requirements=['evidence:quality_proof', 'evidence:platform_audit'])
    rule('pdd-personal-freight', 'freight', 'pinduoduo', 'platform', ['personal'],
         ['pdd-help::pdd-help-personal', 'pdd-protection::pdd-protection-seven'], party='buyer', coverage='return',
         seven_day=True, requirements=['order:supports_seven_day', 'order:goods_condition'])
    for reason in ['quality', 'description_mismatch']:
        rule('tmall-' + reason + '-freight', 'freight', 'tmall', 'platform', [reason],
             ['tmall-seven::tmall-seven-quality'], party='merchant', coverage='return', requirements=['evidence:reason_proof'],
             requirements_basis='review_evidence_requirement; policy specifies liability, not a mandatory document list')
    rule('tmall-personal-freight', 'freight', 'tmall', 'platform', ['personal'],
         ['tmall-seven::tmall-seven-freight', 'tmall-seven::tmall-seven-condition', 'tmall-seven::tmall-seven-clock'],
         party='buyer', coverage='by_shipping', seven_day=True,
         requirements=['order:supports_seven_day', 'order:goods_condition', 'evidence:alipay_guaranteed_transaction'])
    rule('jd-marketplace-personal', 'freight', 'jd', 'marketplace', ['personal'],
         ['jd-marketplace-shipping::jd_marketplace_no_reason_shipping'],
         party='buyer', coverage='by_shipping', seven_day=True,
         requirements=['order:supports_seven_day', 'order:goods_condition'])
    rule('jd-marketplace-quality', 'freight', 'jd', 'marketplace', ['quality', 'description_mismatch', 'wrong_item'],
         ['jd-marketplace-shipping::jd_marketplace_nonbuyer_shipping'],
         party='merchant', coverage='by_shipping', seven_day=True, requirements=['evidence:reason_proof'])
    rule('jd-self-personal', 'freight', 'jd', 'self_operated', ['personal'],
         ['jd-self-operated-shipping::jd_self_operated_shipping_membership'],
         party='membership', coverage='return', requirements=['order:fulfilled_by'])
    rule('jd-self-quality', 'freight', 'jd', 'self_operated', ['quality'],
         ['jd-self-operated-shipping::jd_self_operated_shipping_membership'],
         party='jd', coverage='return', requirements=['order:fulfilled_by', 'evidence:reason_proof'])
    rule('suning-quality', 'freight', 'suning', 'platform', ['quality'],
         ['suning-freight-disputes::suning_quality_shipping'], party='merchant', coverage='both',
         requirements=['evidence:quality_proof', 'evidence:platform_intervention', 'evidence:no_special_agreement'])
    rule('jd-office-return', 'materials', 'jd', 'marketplace', ['personal', 'quality', 'description_mismatch'],
         ['jd-marketplace-office::jd_office_return_materials'], category='office_software', request_types=['return'],
         requirements=['evidence:detail_policy_review', 'if_evidence:has_gifts:gift_return', 'if_evidence:has_paper_invoice:invoice_return'])
    rule('jd-office-quality-exchange', 'materials', 'jd', 'marketplace', ['quality'],
         ['jd-marketplace-office::jd_office_return_materials'], category='office_software', request_types=['exchange'],
         requirements=['evidence:detail_policy_review'])
    write('knowledge/review-rules.json', {'schema_version': 1, 'rule_kind': 'review_guidance', 'rules': rules})


def make_case(number, variant):
    cid = f'CASE-{number:02}-{variant}'
    amount = 100 + variant * 37
    case = dict(case_id=cid, platform='tmall', shop_id='demo-shop-A', order_id=f'O-{number}-{variant}',
                business_scope='platform', checks=['records', 'amount'], reason='quality',
                request_type='return', application_date='2026-11-20')
    order = dict(evidence_id=cid+'-order', platform=case['platform'], shop_id=case['shop_id'], order_id=case['order_id'],
                 paid_amount=str(amount), received_date='2026-11-17', goods_condition='intact',
                 supports_seven_day=True, shipping_included=True, category='general')
    refunds, evidence, extra_orders = [], [], []
    status, codes, gap_kinds, party = 'clear', [], [], None
    def refund(rid, value, state='succeeded', **extra):
        row = dict(evidence_id=cid+f'-refund-row-{len(refunds)+1}', platform=case['platform'], shop_id=case['shop_id'],
                   order_id=case['order_id'], refund_id=cid+'-'+rid, refund_amount=str(value), refund_date='2026-11-19', status=state)
        row.update(extra);refunds.append(row)
    def proof(kind):
        evidence.append(dict(evidence_id=cid+'-'+kind, case_id=cid, kind=kind, status='confirmed', text='人工构造的核查凭证'))
    if number == 1:
        refund('R1', amount // 3);refund('R2', amount // 4)
    elif number == 2:
        refund('R1', amount // 3);refund('R1', amount // 3)
        status, codes = 'needs_review', ['duplicate_refund_record']
    elif number == 3:
        refund('R1', 30);refund('R1', 40)
        status, codes, gap_kinds = 'needs_review', ['refund_record_conflict'], ['data']
    elif number == 4:
        refund('R1', 20);order=None
        status, codes, gap_kinds='needs_review',['orphan_refund'],['data']
    elif number == 5:
        refund('R1', amount // 4);refund('R2', amount * 2, 'requested');refund('R3', amount * 2, 'cancelled')
    elif number == 6:
        refund('R1', amount);refund('R2', 1 + variant)
        status,codes='needs_review',['refund_total_exceeds_paid']
    elif number == 7:
        refund('R1', 20);order.pop('paid_amount')
        status,gap_kinds='needs_information',['data']
    elif number == 8:
        refund('R1', 20, refund_transaction_id=cid+'-TX1');refund('R2', 20, refund_transaction_id=cid+'-TX1')
        status,codes,gap_kinds='needs_review',['refund_transaction_reused'],['data']
    elif number == 9:
        refund('R1', 20)
        foreign = dict(order, shop_id='demo-shop-B', evidence_id=cid+'-foreign-order')
        extra_orders.append(foreign)
        refund('R1', amount * 3, shop_id='demo-shop-B')
    elif number == 10:
        case.update(platform='suning',checks=['freight'],proposed_freight_party='merchant')
        for kind in ['quality_proof','platform_intervention','no_special_agreement']:proof(kind)
        party='merchant'
    elif number == 11:
        case.update(platform='pinduoduo', checks=['freight'], proposed_freight_party='buyer')
        proof('quality_proof');proof('platform_audit')
        status,codes,party='needs_review',['freight_responsibility_mismatch'],'merchant'
    elif number == 12:
        case.update(platform='pinduoduo',checks=['freight','materials'])
        status,codes,gap_kinds='needs_review',['required_materials_missing'],['data']
    elif number == 13:
        case.update(reason='personal',checks=['freight'],proposed_freight_party='buyer')
        proof('alipay_guaranteed_transaction');party='buyer'
    elif number == 14:
        case.update(checks=['freight'],proposed_freight_party='buyer',reason='description_mismatch')
        proof('reason_proof');status,codes,party='needs_review',['freight_responsibility_mismatch'],'merchant'
    elif number == 15:
        case.update(platform='jd',business_scope='self_operated',reason='personal',checks=['freight'],proposed_freight_party='buyer')
        order.update(fulfilled_by='jd',membership='regular');party='buyer'
    elif number == 16:
        case.update(platform='jd',business_scope='self_operated',reason='personal',checks=['freight'])
        status,gap_kinds='needs_information',['data']
    elif number == 17:
        case.update(platform='jd',business_scope='marketplace',reason='personal',checks=['materials'])
        order.update(category='office_software',has_gifts=True,has_paper_invoice=True);proof('detail_policy_review')
        status,codes,gap_kinds='needs_review',['required_materials_missing'],['data']
    elif number == 18:
        case.update(platform='jd',business_scope='marketplace',checks=['materials'],request_type='exchange')
        order.update(category='office_software');proof('detail_policy_review')
    elif number == 19:
        case.update(platform='taobao',reason='personal',checks=['freight'])
        status,gap_kinds='needs_policy',['knowledge']
    elif number == 20:
        case.update(checks=['refund_velocity'])
        status,gap_kinds='needs_tool',['tool']
    if order:
        order['platform'] = case['platform']
    expected = dict(case_id=cid, family=number, split='test' if variant == 2 else 'dev',
                    status=status, finding_codes=codes, gap_kinds=gap_kinds, freight_party=party)
    return case, ([order] if order else []) + extra_orders, refunds, evidence, expected


def main():
    build_rules()
    book = dict(schema_version=1,dataset_kind='synthetic',cases=[],orders=[],refunds=[],evidence=[])
    oracle = []
    for n in range(1,21):
        for variant in range(3):
            case, orders, refunds, evidence, expected = make_case(n,variant)
            for name,rows in [('cases',[case]),('orders',orders),('refunds',refunds),('evidence',evidence)]:
                book[name].extend(rows)
            oracle.append(expected)
    write('data/case-review/benchmark.json',book)
    write('benchmarks/case-review/oracle.json',{'schema_version':1,'dataset_kind':'synthetic',
        'split_policy':'40 dev, 20 frozen parameter-boundary variants across the same 20 scenario families',
        'labels':oracle})
    demo_ids={'CASE-02-0','CASE-06-0','CASE-10-0','CASE-11-0','CASE-13-0','CASE-17-0','CASE-19-0','CASE-20-0'}
    demo_cases=[c for c in book['cases'] if c['case_id'] in demo_ids]
    keys={(c['platform'],c['shop_id'],c['order_id']) for c in demo_cases}
    demo=dict(schema_version=1,dataset_kind='synthetic',cases=demo_cases,
              orders=[r for r in book['orders'] if (r['platform'],r['shop_id'],r['order_id']) in keys],
              refunds=[r for r in book['refunds'] if (r['platform'],r['shop_id'],r['order_id']) in keys],
              evidence=[e for e in book['evidence'] if e['case_id'] in demo_ids])
    write('data/case-review/demo.json',demo)
    print(json.dumps({'cases':len(oracle),'dev':40,'test':20,'demo':len(demo_cases)}))


if __name__=='__main__':main()
