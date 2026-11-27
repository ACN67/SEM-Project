"""Import verified PDF originals through KnowledgeService using an existing isolated DB."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.contracts import models as m
from backend.app.knowledge.index import EvidenceIndex
from backend.app.knowledge.service import KnowledgeService

def main():
    import pymysql
    parser=argparse.ArgumentParser()
    parser.add_argument("sources",type=Path)
    parser.add_argument("runtime",type=Path)
    parser.add_argument("--database",required=True)
    args=parser.parse_args()
    connect=lambda:pymysql.connect(host=os.getenv("MYSQL_HOST","127.0.0.1"),
        port=int(os.getenv("W1_MYSQL_PORT","3306")),user=os.getenv("MYSQL_USER","root"),
        password=os.getenv("MYSQL_PASSWORD",""),database=args.database,charset="utf8mb4")
    index=EvidenceIndex(args.runtime/"index")
    service=KnowledgeService(connect,args.runtime,index)
    records=json.loads(Path("data/samples/w5/evidence.json").read_text(encoding="utf8"))["records"]
    acquired={r["evidence_key"]:r for r in json.loads((args.sources/"acquisition.json").read_text(encoding="utf8"))}
    results=[]
    args.runtime.mkdir(parents=True,exist_ok=True)
    try:
        for record in records:
            try:
                item=acquired[record["evidence_key"]]
                result={"evidence_key":record["evidence_key"],"case_id":record["case_id"]}
                if item["status"] not in ("LOCATED_REQUIRES_REVIEW","ORIGINAL_REQUIRES_CELL_REVIEW"):
                    results.append({**result,"status":"SOURCE_NOT_READY"})
                    continue
                path=args.sources/item["file"]
                if sha256(path.read_bytes()).hexdigest()!=item["sha256"]:
                    raise ValueError("changed original "+str(path))
                version=service.ingest_document(path,m.Metadata(title=record["title"],publisher=record["publisher"],
                    published_at=record["published_at"],source_url=record["source_url"])).document_version_id
                service.pipeline.process(version,labels={"industries":["新能源汽车"]})
                if path.suffix.lower()==".xlsx":
                    metrics=service.get_metrics(m.Filters(),[version])
                    expected={"2023":"524219","2024":"380609"}
                    passed=bool(metrics) and all(x.value==expected.get(x.period) and x.unit=="辆" for x in metrics)
                    results.append({**result,"status":"METRIC_PASS" if passed else "METRIC_FAIL",
                        "document_version_id":version,"metrics":[x.model_dump(mode="json") for x in metrics]})
                    continue
                hits=service.retrieve(record["excerpt"],m.Filters(),50,[version])
                located=[e for e in hits if e.locator.kind=="pdf" and e.locator.page==item["locator"]["page"]]
                from scripts.w5_facts import check_fact
                fact=check_fact(record["evidence_key"],located)
                results.append({**result,"status":"FACT_PASS" if fact else "FACT_NOT_VERIFIED",
                    "page_located":bool(located),"fact_verified":fact,
                    "document_version_id":version,"retrieved":[e.model_dump(mode="json") for e in located]})
            except Exception as exc:
                results.append({"evidence_key":record["evidence_key"],"case_id":record["case_id"],
                    "status":"FAILED","error":str(exc)})
            finally:
                temporary=args.runtime/"source-results.json.part"
                temporary.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf8")
                temporary.replace(args.runtime/"source-results.json")
    finally:
        index.client._system.stop()
    args.runtime.mkdir(parents=True,exist_ok=True)
    (args.runtime/"source-results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf8")

if __name__=="__main__":
    main()
