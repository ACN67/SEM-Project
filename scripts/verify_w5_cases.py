"""Exercise real Knowledge and Quality services for the five source cases.

Use an isolated, migrated database. This checks acquisition, retrieval boundaries,
transcribed metrics and deterministic negatives. It never signs off model prose.
"""
import argparse
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.contracts import models as m
from backend.app.knowledge.index import EvidenceIndex
from backend.app.knowledge.service import KnowledgeService
from backend.app.quality.service import QualityService


def save(path, value):
    temp=path.with_suffix('.json.part')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    temp.replace(path)


def main():
    import pymysql
    parser=argparse.ArgumentParser()
    parser.add_argument('sources',type=Path)
    parser.add_argument('runtime',type=Path)
    parser.add_argument('--database',required=True)
    args=parser.parse_args()
    args.runtime.mkdir(parents=True,exist_ok=True)
    # Invalidate previous results before any acquisition or dependency can fail.
    for name in ('five-case-sources.json','five-case-tables.json','five-case-results.json'):
        save(args.runtime/name,[])
    connect=lambda:pymysql.connect(host=os.getenv('MYSQL_HOST','127.0.0.1'),port=int(os.getenv('W1_MYSQL_PORT','3306')),
        user=os.getenv('MYSQL_USER','root'),password=os.getenv('MYSQL_PASSWORD',''),database=args.database,charset='utf8mb4')
    records={r['evidence_key']:r for r in json.loads(Path('data/samples/w5/evidence.json').read_text(encoding='utf8'))['records']}
    cases=json.loads(Path('data/samples/w5/cases.json').read_text(encoding='utf8'))['cases']
    renditions={r['evidence_key']:r for r in json.loads((args.sources/'renditions.json').read_text(encoding='utf8'))}
    originals={r['evidence_key']:r for r in json.loads((args.sources/'acquisition.json').read_text(encoding='utf8'))}
    transcriptions=json.loads((args.sources/'transcriptions.json').read_text(encoding='utf8'))
    index=EvidenceIndex(args.runtime/'index')
    service=KnowledgeService(connect,args.runtime,index)
    results=[];versions={};table_versions={};evidence_hits={}
    try:
        required=list(dict.fromkeys(k for c in cases for k in c['evidence_keys']))
        for key in required:
            record=records[key]
            item=originals.get(key,{})
            if item.get('status') not in ('LOCATED_REQUIRES_REVIEW','ORIGINAL_REQUIRES_CELL_REVIEW'):
                item=renditions.get(key,{})
            result=dict(evidence_key=key,case_id=record['case_id'],fact_review='PENDING_ASTRA_REVIEW')
            try:
                if item.get('status') not in ('RENDITION_REQUIRES_REVIEW','LOCATED_REQUIRES_REVIEW','ORIGINAL_REQUIRES_CELL_REVIEW'):
                    raise ValueError('source not ready')
                path=args.sources/item['file']
                if sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('source hash mismatch')
                version=service.ingest_document(path,m.Metadata(title=record['title'],publisher=record['publisher'],
                    published_at=record['published_at'],source_url=record['source_url'])).document_version_id
                print(key,version,flush=True)
                service.pipeline.process(version,labels={'industries':['新能源汽车']})
                hits=service.retrieve(record['excerpt'],m.Filters(),50,[version])
                metrics=service.get_metrics(m.Filters(),[version]) if path.suffix.lower()=='.xlsx' else []
                if path.suffix.lower()=='.xlsx':
                    expected='524219' if '2023' in key else '380609'
                    if len(metrics)!=1 or metrics[0].value!=expected or metrics[0].unit!='辆':
                        raise ValueError('original workbook metric mismatch')
                elif not hits:
                    raise ValueError('real retrieval returned no candidate')
                versions[key]=version
                evidence_hits[key]=hits
                result.update(status='RETRIEVED',document_version_id=version,representation=item.get('representation','acquired-original'),
                    sha256=item['sha256'],source_url=record['source_url'],retrieved=[x.model_dump(mode='json') for x in hits],
                    metrics=[x.model_dump(mode='json') for x in metrics])
            except Exception as exc:result.update(status='FAILED',error=str(exc))
            results.append(result);save(args.runtime/'five-case-sources.json',results)
        table_results=[]
        for table in transcriptions['tables']:
            path=args.sources/table['file']
            if sha256(path.read_bytes()).hexdigest()!=table['sha256']:raise ValueError('transcription changed')
            keys={key for row in table['rows'] for key in row['evidence_keys']}
            if not keys<=versions.keys():raise ValueError('transcription original unavailable')
            version=service.ingest_document(path,m.Metadata(title=table['case_id']+' 原件数值整理表（非原始发布表）',publisher='SEM 项目资料整理',
                published_at=max(date.fromisoformat(records[k]['published_at']) for k in keys),source_url=None)).document_version_id
            service.pipeline.process(version,labels={'industries':['新能源汽车']})
            metrics=service.get_metrics(m.Filters(),[version])
            if len(metrics)!=len(table['rows']):raise ValueError('transcription row count mismatch')
            table_versions[table['case_id']]=version
            table_results.append(dict(case_id=table['case_id'],document_version_id=version,metrics=[x.model_dump(mode='json') for x in metrics],provenance=table))
        save(args.runtime/'five-case-tables.json',table_results)
        outcomes=[]
        for case in cases:
            cid=case['case_id'];missing=[k for k in case['evidence_keys'] if k not in versions]
            checks=[];negatives=[]
            indicator=case['required_contract_indicators']
            selected=[table_versions[cid]] if cid in table_versions else [versions[k] for k in case['evidence_keys'] if k in versions and k.endswith(('AL461','AL488'))]
            raw=service.get_metrics(m.Filters(),selected) if selected else []
            if indicator:
                task=m.Task(chapter_id='acceptance',section_key='acceptance',title=cid,order=0,required=True,depends_on=[],
                    research_questions=[],evidence_requirements=[],required_metrics=indicator,desired_chart=None)
                calc=QualityService().calculate_metrics(raw,task)
                expected={'R01':{(None,'2024'):'35.50'},'R02':{('byd','2024'):'35.68',('saic','2024'):'-0.91'},
                    'R03':{(None,'2024-Q1'):'31.78',(None,'2024-Q2'):'32.07',(None,'2024-Q3'):'33.39',(None,'2024-Q4'):'41.31'},
                    'R05':{(None,'2024'):'-27.40'}}[cid]
                rates={(x.entity_id,x.period):x for x in calc.metrics if x.formula_id=='yoy_pct' and x.value is not None}
                for key,value in expected.items():
                    actual=rates.get(key)
                    checks.append(dict(name='yoy',entity_id=key[0],period=key[1],expected=value,
                        actual=actual.value if actual else None,status='PASS' if actual and f'{Decimal(actual.value):.2f}'==value else 'FAIL'))
                base=next((x for x in raw if x.period in ('2023','2023-Q1')),None)
                if base:
                    removed=[x for x in raw if x.metric_id!=base.metric_id]
                    negative=QualityService().calculate_metrics(removed,task)
                    target='2024-Q1' if cid=='R03' else '2024'
                    passed=not any(x.formula_id=='yoy_pct' and x.period==target and x.entity_id==base.entity_id and x.value is not None for x in negative.metrics)
                    negatives.append(dict(name='remove_baseline',status='PASS' if passed else 'FAIL',gaps=[x.model_dump() for x in negative.gaps]))
                if cid=='R02':
                    bad=[x.model_copy(update={'unit':'辆'}) if x.entity_id=='byd' and x.period=='2024' else x for x in raw]
                    result=QualityService().calculate_metrics(bad,task)
                    negatives.append(dict(name='rd_unit_conflict',status='PASS' if any(g.code=='UNIT_MISMATCH' for g in result.gaps) else 'FAIL'))
                    bad=[x.model_copy(update={'scope':'研发费用（不含资本化支出）'}) if x.entity_id=='byd' and x.period=='2024' else x for x in raw]
                    result=QualityService().calculate_metrics(bad,task)
                    negatives.append(dict(name='rd_expense_vs_total',status='PASS' if any(g.code=='SCOPE_MISMATCH' for g in result.gaps) else 'FAIL'))
                if cid=='R03':
                    from backend.app.generation.analysis_context import quarterly_analysis
                    analysis=quarterly_analysis(m.Prosperity.model_validate(case['request']),calc.metrics)
                    changes=analysis['adjacent_yoy_changes']
                    checks.append(dict(name='adjacent_yoy_direction',status='PASS' if len(changes)==3 and all(x['direction']=='加快' for x in changes) else 'FAIL',analysis=analysis))
                    bad=[x.model_copy(update={'value':'0'}) if x.period=='2023-Q1' else x for x in raw]
                    result=QualityService().calculate_metrics(bad,task)
                    negatives.append(dict(name='zero_base',status='PASS' if any(g.code=='ZERO_BASE' for g in result.gaps) else 'FAIL'))
                    bad=[x for x in raw if x.period!='2024-Q3']
                    result=QualityService().calculate_metrics(bad,task)
                    negatives.append(dict(name='missing_quarter',status='PASS' if not any(x.period=='2024-Q3' for x in result.metrics) else 'FAIL'))
                if cid=='R05':
                    bad=[x.model_copy(update={'scope':'全部新能源汽车，含插电混动'}) if x.period=='2024' else x for x in raw]
                    result=QualityService().calculate_metrics(bad,task)
                    negatives.append(dict(name='bev_scope_conflict',status='PASS' if any(g.code=='SCOPE_MISMATCH' for g in result.gaps) else 'FAIL'))
            if cid=='R04':
                roles={
                    'EV-R04-HEFEI-IMPL-P3':('动力电池','配套能力'),
                    'EV-R04-GOTION-LAB-2023':('合肥','电芯测试'),
                }
                role_matches={key:all(token in ''.join(''.join(e.excerpt.split()) for e in evidence_hits.get(key,[]))
                                      for token in tokens) for key,tokens in roles.items()}
                checks.append(dict(name='demand_and_capability_fragments',status='PASS' if all(role_matches.values()) else 'FAIL',
                    source_role_matches=role_matches,meaning='分别定位政策配套方向与企业测试能力，匹配适用性仍交独立复核。'))
                for removed in case['evidence_keys']:
                    allowed=[versions[k] for k in case['evidence_keys'] if k!=removed and k in versions]
                    remaining=next(k for k in case['evidence_keys'] if k!=removed)
                    hits=service.retrieve(records[remaining]['excerpt'],m.Filters(),50,allowed)
                    passed=bool(hits) and removed in versions and all(x.document_version_id!=versions[removed] for x in hits)
                    negatives.append(dict(name='remove_'+removed,status='PASS' if passed else 'FAIL',remaining_versions=sorted({x.document_version_id for x in hits}),
                        consequence='仅剩单侧资料，匹配判断须待核实；此项验证知识库边界，不验证模型语义。'))
            if cid in ('R01','R05'):
                key='EV-R01-MIIT-2024-ANNUAL' if cid=='R01' else 'EV-R05-EU-ANTISUBSIDY'
                cutoff=date(2024,12,31) if cid=='R01' else date(2024,10,28)
                hits=service.retrieve(records[key]['excerpt'],m.Filters(as_of=cutoff),50,[versions[key]]) if key in versions else None
                negatives.append(dict(name='before_publication',status='PASS' if hits==[] else 'FAIL',as_of=cutoff.isoformat()))
            passed=not missing and bool(negatives) and all(x['status']=='PASS' for x in checks+negatives)
            outcomes.append(dict(case_id=cid,missing_sources=missing,checks=checks,negatives=negatives,
                machine_verification='PASS' if passed else 'INCOMPLETE',independent_review='PENDING_ASTRA_REVIEW',
                semantic_conclusions='REQUIRES_REVIEW',acceptance='READY_FOR_REVIEW' if passed else 'INCOMPLETE'))
            save(args.runtime/'five-case-results.json',outcomes)
    finally:
        index.client._system.stop()
    if any(c['machine_verification']!='PASS' for c in outcomes):
        raise SystemExit('Five-case machine verification incomplete; inspect five-case-results.json')


if __name__=='__main__':main()
