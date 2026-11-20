"""Independent labelled adapter cases, including boundaries and blocked inputs."""
from build_case_review_benchmark import write


def main():
    book=dict(schema_version=1,dataset_kind='synthetic',cases=[],orders=[],refunds=[],evidence=[])
    labels=[]
    for check in ['refund_velocity','refund_average','refund_replay']:
        for v in range(8):
            cid=f'RSI-{check}-{v}'
            case=dict(case_id=cid,platform='tmall',shop_id='rsi-demo',order_id=cid,
                      business_scope='platform',checks=[check],reason='quality',application_date='2026-11-20')
            order=dict(evidence_id=cid+'-order',platform='tmall',shop_id=case['shop_id'],order_id=cid,paid_amount='1000')
            rows=[];codes=[];gaps=[];status='clear'
            def row(n,amount='20',state='succeeded',day='2026-11-20',rid=None):
                return dict(evidence_id=cid+'-E'+str(n),platform='tmall',shop_id=case['shop_id'],order_id=cid,
                            refund_id=cid+'-R'+str(n if rid is None else rid),refund_amount=amount,
                            refund_date=day,status=state)
            if check=='refund_velocity':
                case['review_parameters']={'window_days':7,'event_count_threshold':2}
                if v in {1,2,3}:rows=[row(n) for n in range(v)]
                elif v==4:rows=[row(0),row(1),row(2,rid=1)]
                elif v==5:rows=[row(0),row(1),row(2,state='requested')]
                elif v==6:rows=[row(0,day='2026-11-13'),row(1,day='2026-11-14'),row(2,day='2026-11-21')]
                elif v==7:rows=[row(0)];case['review_parameters'].pop('window_days');gaps=['data'];status='needs_information'
                if v==3:codes=['refund_frequency_threshold_exceeded'];status='needs_review'
            elif check=='refund_average':
                case['review_parameters']={'average_amount_threshold':'50'}
                if v==1:rows=[row(0,'49.99')]
                elif v==2:rows=[row(0,'50')]
                elif v==3:rows=[row(0,'50.01')];codes=['refund_average_threshold_exceeded'];status='needs_review'
                elif v==4:rows=[row(0,'20'),row(1,'100','failed')]
                elif v==5:rows=[row(0,'50'),row(1,'50',rid=0)]
                elif v==6:rows=[row(0,'49.99'),row(1,'50.02')]
                elif v==7:rows=[row(0,'40'),row(1,'60',rid=0)];gaps=['data'];status='needs_information'
            else:
                case['review_parameters']={'replay_rate_threshold_bps':3000}
                if v==1:rows=[row(0)]
                elif v==2:rows=[row(0),row(1)]
                elif v==3:rows=[row(0),row(1,rid=0)];codes=['refund_replay_threshold_exceeded'];status='needs_review'
                elif v==4:rows=[row(0),row(1),row(2,rid=1)];codes=['refund_replay_threshold_exceeded'];status='needs_review'
                elif v==5:rows=[row(0,state='cancelled'),row(1,state='cancelled',rid=0)];codes=['refund_replay_threshold_exceeded'];status='needs_review'
                elif v==6:rows=[row(0),row(1),row(2),row(3,rid=2)]
                elif v==7:rows=[row(0)];case['review_parameters']={};gaps=['data'];status='needs_information'
            book['cases'].append(case);book['orders'].append(order);book['refunds'].extend(rows)
            labels.append(dict(case_id=cid,family=check,split='audit',status=status,finding_codes=codes,gap_kinds=gaps,freight_party=None))
    write('data/case-review/rsi-v2.json',book)
    write('benchmarks/case-review/rsi-v2-oracle.json',dict(schema_version=1,labels=labels,
          split_policy='24 frozen adapter cases; boundary and blocked-input checks across three trusted metric definitions'))
    print('rsi_cases',len(labels))


if __name__=='__main__':main()
