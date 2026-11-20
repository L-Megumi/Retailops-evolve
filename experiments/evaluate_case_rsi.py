"""Cold capability gap -> generation -> admitted execution -> persisted reuse."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path

from evaluate_case_review import score
from retailops_agent.case_capabilities import CaseCapabilityRuntime
from retailops_agent.case_data import CaseBook
from retailops_agent.case_review import CaseReviewAgent
from retailops_agent.knowledge import KnowledgeStore, FastEmbedEncoder
from retailops_agent.vector_index import QdrantVectorIndex
from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.persistence import atomic_json


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--qdrant-url',default='http://127.0.0.1:6333')
    args=p.parse_args()
    registry=args.state/'registry.json'
    if registry.exists():
        raise ValueError('cold-start experiment requires a new state directory; existing state is preserved')
    book=CaseBook.from_path('data/case-review/rsi-v2.json')
    labels=json.loads(Path('benchmarks/case-review/rsi-v2-oracle.json').read_text(encoding='utf-8'))['labels']
    gold={l['case_id']:l for l in labels}
    store=KnowledgeStore.from_path('knowledge/retailops-policies.json',encoder=FastEmbedEncoder(),
                                   vector_index=QdrantVectorIndex(args.qdrant_url))
    model=OpenAICompatibleModel.deepseek_from_env()
    runtime=CaseCapabilityRuntime.from_path(registry,model=model,allow_evolution=True,artifact_root=args.state/'evolution')
    report={'created_at':datetime.now(timezone.utc).isoformat(),'input_hash':book.content_hash,
            'oracle_sha256':hashlib.sha256(Path('benchmarks/case-review/rsi-v2-oracle.json').read_bytes()).hexdigest(),
            'dataset_kind':'synthetic','phases':{}}
    initial=CaseCapabilityRuntime.from_path(registry)
    before=CaseReviewAgent(store,capability_runtime=initial).run('核查案例',book)
    report['phases']['before']={'scores':score(before.cases,gold),'trace':asdict(before)}
    atomic_json(args.output,report)
    after=CaseReviewAgent(store,capability_runtime=runtime).run('核查案例',book)
    report['phases']['evolve']={'scores':score(after.cases,gold),'trace':asdict(after)}
    atomic_json(args.output,report)
    loaded=CaseCapabilityRuntime.from_path(registry)
    reused=CaseReviewAgent(store,capability_runtime=loaded).run('核查案例',book)
    report['phases']['reopen_reuse']={'scores':score(reused.cases,gold),'trace':asdict(reused)}
    regression=[]
    for name,path,oracle in [
        ('seed','data/case-review/benchmark.json','benchmarks/case-review/oracle.json'),
        ('combination','data/case-review/combination-v2.json','benchmarks/case-review/combination-v2-oracle.json'),
        ('public','data/case-review/public-v2.json','benchmarks/case-review/public-v2-oracle.json')]:
        inp=CaseBook.from_path(path);g={r['case_id']:r for r in json.loads(Path(oracle).read_text(encoding='utf-8'))['labels']}
        a=CaseReviewAgent(store).run('核查案例',inp)
        b=CaseReviewAgent(store,capability_runtime=loaded).run('核查案例',inp)
        changes=[]
        for old,new in zip(a.cases,b.cases):
            if old['status']!=new['status'] or old['findings']!=new['findings'] or old['gaps']!=new['gaps']:
                changes.append({'case_id':old['case_id'],'before_status':old['status'],'after_status':new['status'],
                                'findings_unchanged':old['findings']==new['findings'],
                                'reason':'registered check now requires explicit review parameters' if name=='seed' and old['case_id'].startswith('CASE-20-') else 'inspect'})
        regression.append({'set':name,'before':score(a.cases,g),'after':score(b.cases,g),
            'case_changes':changes,
            'note':'seed contains a refund_velocity placeholder without parameters; it becomes a data gap rather than an installed-tool gap'})
    report['regression']=regression
    report['model_usage']={'model':model.model,'http_requests':model.request_count,**model.total_usage()}
    report['plugins']=[{'output_name':r.spec.output_name,'source_sha256':r.registration.source_sha256,
                        'status':r.status,'admission':r.admission} for r in loaded.manager.records]
    report['solvable_capability_cases']=sum(not l['gap_kinds'] for l in labels)
    report['blocked_input_cases']=sum(bool(l['gap_kinds']) for l in labels)
    report['claim']='Fixed-model, verifier-gated tool synthesis and replanning; no change to weights, trusted definitions or safety harness.'
    atomic_json(args.output,report)
    for phase,row in report['phases'].items():
        print(json.dumps({'phase':phase,**row['scores'],'generation_calls':row['trace']['model_usage'].get('generation_calls')},ensure_ascii=False),flush=True)
    if not after.ok or not reused.ok or score(after.cases,gold)['exact_case_matches']!=len(labels):
        raise SystemExit('case capability evolution did not satisfy the benchmark; inspect report')


if __name__=='__main__':main()
