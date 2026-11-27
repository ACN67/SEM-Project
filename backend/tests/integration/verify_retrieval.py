"""Opt-in real model calibration: original PDF -> located evidence -> Chroma."""
from pathlib import Path
import json
import sys
from backend.app.knowledge.index import EvidenceIndex
from backend.app.knowledge.text import parse_text
from backend.app.contracts import models as m

REVISION="7999e1d3359715c523056ef9478215996d62a620"
def verify(original,output):
    import torch
    torch.set_num_threads(4)
    manifest=json.loads(Path("data/samples/w5/evidence.json").read_text(encoding="utf8"))
    source=next(r for r in manifest["records"] if r["evidence_key"]=="EV-R02-SAIC-2024-P13")
    from hashlib import sha256
    assert sha256(original.read_bytes()).hexdigest()==source["source_hash"]
    record=m.DocumentRecord(**{k:source[k] for k in ("document_id","document_version_id","title",
        "publisher","published_at","source_url")},ingest_status="QUEUED",error=None)
    evidence=parse_text(original,source["file_version"],record).evidences
    output.mkdir(parents=True,exist_ok=True)
    index=EvidenceIndex(output/"index",revision=REVISION,threshold=-1)
    index.replace(record.document_version_id,evidence)
    ids=[e.evidence_id for e in evidence]
    relevant=["本期费用化研发投入和资本化研发投入分别是多少？","研发投入合计是多少？"]
    unrelated=["如何制作红烧肉？","太阳系火星的公转周期是多少？"]
    scores={}
    for query in relevant+unrelated:
        scores[query]=index.rank(query,ids)[:3]
    positive=min(scores[q][0][1] for q in relevant)
    negative=max(scores[q][0][1] for q in unrelated)
    assert positive>negative,"Calibration lacks separation"
    threshold=(positive+negative)/2
    index.threshold=threshold
    lookup={e.evidence_id:e for e in evidence}
    for query in relevant:
        result=index.rank(query,ids)
        assert result and lookup[result[0][0]].locator.page==13
    for query in unrelated:assert index.rank(query,ids)==[]
    result={"revision":REVISION,"dimension":512,"normalize":True,"threshold":threshold,
        "source_hash":source["source_hash"],"scores":scores,
        "positive_pages":[lookup[scores[q][0][0]].locator.page for q in relevant]}
    (output/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
    print(json.dumps(result,ensure_ascii=True))

if __name__=="__main__":verify(Path(sys.argv[1]),Path(sys.argv[2]))
