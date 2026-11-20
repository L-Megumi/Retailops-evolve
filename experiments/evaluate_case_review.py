"""Case-level correctness, finding precision/recall and capability ablations."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path

from retailops_agent.case_data import CHECKS, CaseBook
from retailops_agent.case_review import CaseReviewAgent, render_case_review
from retailops_agent.knowledge import FastEmbedEncoder, KnowledgeStore
from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.vector_index import QdrantVectorIndex


def score(rows, labels):
    tp=fp=fn=matches=0
    for case in rows:
        gold=labels[case['case_id']]
        actual={f['code'] for f in case['findings']};expected=set(gold['finding_codes'])
        tp+=len(actual & expected);fp+=len(actual-expected);fn+=len(expected-actual)
        matches += case['status']==gold['status'] and actual==expected and {g['kind'] for g in case['gaps']}==set(gold['gap_kinds']) and (
            gold['freight_party'] is None or any(g.get('party')==gold['freight_party'] for g in case['guidance']))
    return dict(case_count=len(rows),exact_case_matches=matches,finding_true_positive=tp,finding_false_positive=fp,
                finding_false_negative=fn,precision=tp/(tp+fp) if tp+fp else None,
                recall=tp/(tp+fn) if tp+fn else None,
                normal_case_false_positives=sum(bool(c['findings']) for c in rows if labels[c['case_id']]['status']=='clear'))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--casebook',type=Path,default=Path('data/case-review/benchmark.json'))
    p.add_argument('--oracle',type=Path,default=Path('benchmarks/case-review/oracle.json'))
    p.add_argument('--knowledge',type=Path,default=Path('knowledge/retailops-policies.json'))
    p.add_argument('--qdrant-url')
    p.add_argument('--with-model',action='store_true')
    p.add_argument('--max-model-calls',type=int,default=8)
    p.add_argument('--ids',help='comma-separated case IDs')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    book=CaseBook.from_path(args.casebook)
    # Real-model acceptance uses only repository-authored synthetic inputs.
    if args.with_model and book.dataset_kind!='synthetic':
        raise ValueError('this experiment accepts synthetic casebooks only')
    oracle=json.loads(args.oracle.read_text(encoding='utf-8-sig'))
    labels={r['case_id']:r for r in oracle['labels']}
    encoder=FastEmbedEncoder() if args.qdrant_url else None
    store=KnowledgeStore.from_path(args.knowledge,encoder=encoder,
         vector_index=QdrantVectorIndex(args.qdrant_url) if args.qdrant_url else None)
    model=OpenAICompatibleModel.deepseek_from_env() if args.with_model else None
    ids=tuple(args.ids.split(',')) if args.ids else ()
    configs=['full'] if args.with_model else ['data_tools_only','policy_tools_only','full']
    report={'created_at':datetime.now(timezone.utc).isoformat(),'input_hash':book.content_hash,
            'oracle_sha256':hashlib.sha256(args.oracle.read_bytes()).hexdigest(),
            'rules_sha256':hashlib.sha256(Path('knowledge/review-rules.json').read_bytes()).hexdigest(),
            'approved_sources_sha256':hashlib.sha256(Path('knowledge/review-rule-sources.json').read_bytes()).hexdigest(),
            'corpus_hash':store.corpus_hash,'dataset_kind':book.dataset_kind,'split_policy':oracle['split_policy'],
            'vector_backend':'qdrant' if encoder else 'memory','configs':{}}
    for config in configs:
        available={'records','amount'} if config=='data_tools_only' else {'freight','materials'} if config=='policy_tools_only' else CHECKS
        agent=CaseReviewAgent(store if config!='data_tools_only' else None,model=model,
                              max_model_calls=args.max_model_calls,available_checks=available)
        trace=agent.run('核查售后案例并整理证据及待补信息',book,case_ids=ids)
        if not trace.ok:raise RuntimeError(trace.issues)
        # Check reference provenance independently from rule findings.
        record_ids={r['evidence_id'] for r in book.orders+book.refunds+book.evidence}
        invalid_references=[]
        for case in trace.cases:
            cited={c['chunk_id'] for c in case['citations']}
            for finding in case['findings']:
                if set(finding['record_evidence_ids'])-record_ids or set(finding['policy_citation_ids'])-cited:
                    invalid_references.append(finding['finding_id'])
            if any(c['platform']!=case['platform'] or c['business_scope'] not in {case['business_scope'],'platform'} for c in case['citations']):
                invalid_references.append(case['case_id']+':platform')
        report['configs'][config]={'scores':score(trace.cases,labels),
            'splits':{split:score([c for c in trace.cases if labels[c['case_id']]['split']==split],labels) for split in ['dev','test']},
            'invalid_references':invalid_references,
            'model_draft_count':sum(c['draft_explanation'] is not None for c in trace.cases),
            'model_fallback_count':sum(e['stage']=='explanation_fallback' for e in trace.events),
            'trace':asdict(trace)}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
        if config=='full':args.output.with_suffix('.md').write_text(render_case_review(trace),encoding='utf-8')
        print(json.dumps({'config':config,**score(trace.cases,labels),'invalid_references':len(invalid_references)},ensure_ascii=False),flush=True)
    if model:report['model_usage']={'model':model.model,'http_requests':model.request_count,**model.total_usage()}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')


if __name__=='__main__':main()
