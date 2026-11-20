"""Source-linked public adaptations and new combinations; labels stay separate."""
import copy
from decimal import Decimal
import json
from pathlib import Path

from build_case_review_benchmark import make_case, write


PUBLIC = [
    ('spc-promise', '直播商品承诺与描述不符', 'https://www.court.gov.cn/zixun/xiangqing/467931.html',
     '2025-06-16', '案例1', '直播手串材质与承诺不符，购买价款1000元，消费者主张承诺赔偿。',
     '1000', 'description_mismatch', ['platform_identity', 'merchant_promise', 'product_identification']),
    ('spc-handbag', '手提包无理由退货遭拒', 'https://www.court.gov.cn/zixun/xiangqing/467931.html',
     '2025-06-16', '案例2', '手提包签收七日内申请退货，商家援引页面不支持退货条款拒绝。',
     None, 'personal', ['platform_identity', 'product_eligibility', 'return_condition']),
    ('spc-promotion', '促销优惠争议', 'https://www.court.gov.cn/zixun/xiangqing/467931.html',
     '2025-06-16', '案例3', '家具促销中提前支付定金，客服未说明时点不符；合计支付2499元。',
     '2499', 'other', ['platform_identity', 'promotion_terms', 'customer_service_commitment']),
    ('spc-ticket', '同单两张票退票争议', 'https://www.court.gov.cn/zixun/xiangqing/467931.html',
     '2025-06-16', '案例4', '同次购买两张演出票，一张退票、另一张仅退八成，规则含义有争议。',
     None, 'other', ['platform_identity', 'ticket_terms', 'refund_ledger']),
    ('samr-phone', '手机合理拆封后退货遭拒', 'https://www.samr.gov.cn/zt/pgt/art/2026/art_dc27a442bba24b25a824a502209ecc7a.html',
     '2026-03-13', '上海迪信电子通信技术有限公司案', '手机仅拆原厂塑料膜且未激活，消费者退货时被商家拒绝。',
     None, 'personal', ['platform_identity', 'product_eligibility', 'application_dates']),
    ('suzhou-pearl', '珍珠耳钉次日退货争议', 'https://www.suzhou.gov.cn/szsrmzf/szyw/202603/f6ec3a07d4c34b5c9a8e6ccc1c1cd37a.shtml',
     '2026-03-25', '孙某某诉某商贸公司案', '小红书直播购买珍珠耳钉15800元，签收次日申请退货被拒。',
     '15800', 'personal', ['unsupported_platform_policy', 'product_eligibility', 'purchase_confirmation']),
]


def main():
    public = dict(schema_version=1, dataset_kind='public_case_adaptation', cases=[], orders=[], refunds=[], evidence=[])
    oracle=[];sources=[]
    for cid,title,url,published,locator,summary,paid,reason,unknown in PUBLIC:
        case=dict(case_id=cid, platform='xiaohongshu' if cid=='suzhou-pearl' else 'undisclosed',
                  shop_id='PUBLIC-SHOP-'+cid, order_id='PUBLIC-ORDER-'+cid, checks=['freight'],
                  business_scope='platform', reason=reason, request_type='return',
                  public_origin={'source_url':url,'locator':locator,'retrieved_at':'2026-11-20',
                                 'summary':summary,'unavailable_facts':unknown},
                  adaptation_notes=['case_id, shop_id and order_id are synthetic linkage handles',
                                    'freight is a review question added for coverage assessment, not a reported dispute fact',
                                    'judgment and outcome are excluded from runtime input'])
        public['cases'].append(case)
        order=dict(evidence_id=cid+'-source-facts',platform=case['platform'],shop_id=case['shop_id'],order_id=case['order_id'])
        if paid is not None:order['paid_amount']=paid
        public['orders'].append(order)
        public['evidence'].append(dict(evidence_id=cid+'-public-summary',case_id=cid,kind='public_source_summary',
                                      status='present',text=summary))
        oracle.append(dict(case_id=cid,family=cid,split='public-coverage',status='needs_policy',finding_codes=[],
                           gap_kinds=['knowledge'],freight_party=None))
        sources.append(dict(case_id=cid,title=title,source_url=url,published_at=published,locator=locator,
                            retrieved_at='2026-11-20',input_facts=summary,not_disclosed=unknown))
    write('data/case-review/public-v2.json',public)
    write('benchmarks/case-review/public-v2-oracle.json',dict(schema_version=1,labels=oracle,
          split_policy='six public-source adaptations; coverage/refusal evaluation, not complete reconstructed ledgers'))
    write('benchmarks/case-review/public-v2-sources.json',dict(schema_version=1,sources=sources))
    combined=dict(schema_version=1,dataset_kind='synthetic',cases=[],orders=[],refunds=[],evidence=[])
    gold=[]
    for family in range(8):
        for v in range(3):
            base=[11,13,17,19,10,12,18,15][family]
            case,orders,refunds,evidence,_=make_case(base,v)
            old=case['case_id'];cid=f'COMBO-{family+1:02}-{v}'
            def rename(obj):
                return json.loads(json.dumps(obj,ensure_ascii=False).replace(old,cid))
            case,orders,refunds,evidence=map(rename,(case,orders,refunds,evidence))
            case['case_id']=cid;case['order_id']=cid+'-ORDER';case['checks']=['records','amount']+case['checks']
            for row in orders:row['order_id']=case['order_id']
            paid=Decimal(orders[0]['paid_amount'])
            def refund(n,amount,status='succeeded',**extra):
                return dict(evidence_id=cid+f'-RROW{n}',platform=case['platform'],shop_id=case['shop_id'],
                            order_id=case['order_id'],refund_id=cid+f'-R{n}',refund_amount=str(amount),
                            refund_date='2026-11-19',status=status,**extra)
            codes=[];kinds=[];party=None
            if family==0:
                refunds=[refund(1,paid+Decimal('.01'))]
                codes=['refund_total_exceeds_paid','freight_responsibility_mismatch'];party='merchant'
            elif family==1:
                case['proposed_freight_party']='merchant'
                refunds=[refund(1,paid/2),refund(2,paid/2)]
                codes=['freight_responsibility_mismatch'];party='buyer'
            elif family==2:
                refunds=[refund(1,20),refund(2,paid,'failed')];dup=copy.deepcopy(refunds[0]);dup['evidence_id']=cid+'-RDUP';refunds.append(dup)
                codes=['duplicate_refund_record','required_materials_missing'];kinds=['data']
            elif family==3:
                refunds=[refund(1,paid+1)];codes=['refund_total_exceeds_paid'];kinds=['knowledge']
            elif family==4:
                refunds=[refund(1,paid/2),refund(2,paid/2),refund(3,paid*2,'cancelled')];party='merchant'
            elif family==5:
                refunds=[refund(1,30),refund(2,40)];refunds[1]['refund_id']=refunds[0]['refund_id']
                codes=['refund_record_conflict','required_materials_missing'];kinds=['data']
            elif family==6:
                refunds=[refund(1,paid)];orders[0].pop('paid_amount');kinds=['data']
            else:
                case['proposed_freight_party']='merchant';refunds=[refund(1,20)]
                codes=['freight_responsibility_mismatch'];party='buyer'
            status='needs_review' if codes else 'needs_information' if kinds else 'clear'
            gold.append(dict(case_id=cid,family=family,split='combination-audit',status=status,
                             finding_codes=codes,gap_kinds=kinds,freight_party=party))
            combined['cases'].append(case);combined['orders'].extend(orders);combined['refunds'].extend(refunds);combined['evidence'].extend(evidence)
    write('data/case-review/combination-v2.json',combined)
    write('benchmarks/case-review/combination-v2-oracle.json',dict(schema_version=1,labels=gold,
          split_policy='24 new mixed data-and-policy combinations, frozen before RSI integration; three amount boundaries per family'))
    print(json.dumps({'public_adaptations':6,'new_combinations':24}))


if __name__=='__main__':main()
