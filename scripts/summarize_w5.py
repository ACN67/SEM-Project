"""Produce five-case coverage and verify that removing evidence preserves gaps."""
import json
from pathlib import Path
import sys
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.contracts import models as m
from backend.app.quality.service import QualityService

def summarize(rows):
    cases=json.loads(Path("data/samples/w5/cases.json").read_text(encoding="utf8"))["cases"]
    available={r["evidence_key"] for r in rows if r["status"] in ("CANDIDATE_MATCH","METRIC_PASS")}
    coverage=[]
    for case in cases:
        required=set(case["evidence_keys"])
        missing=sorted(required-available)
        coverage.append(dict(case_id=case["case_id"],
            source_coverage="COVERED" if not missing else "INCOMPLETE",
            fact_review="PENDING", independent_signoff="PENDING", acceptance="INCOMPLETE",
            missing_evidence_keys=missing,
            business_negative={"status":"NOT_RUN","reason":"Case business prerequisites and fact signoff incomplete"}))
    return coverage

def main():
    root=Path(sys.argv[1])
    rows=json.loads((root/"source-results.json").read_text(encoding="utf8"))
    metrics=[m.Metric.model_validate(x) for r in rows if r["status"]=="METRIC_PASS" for x in r["metrics"]]
    output={"cases":summarize(rows)}
    if len(metrics)==2:
        task=m.Task(chapter_id="market",section_key="market",title="注册量",order=0,required=True,
            depends_on=[],research_questions=[],evidence_requirements=[],required_metrics=["bev_registrations"],desired_chart=None)
        calculation=QualityService().calculate_metrics(metrics,task)
        output["calculation"]=calculation.model_dump(mode="json")
        expected=(Decimal("380609")/Decimal("524219")-1)*100
        output["manual_yoy_pct"]=str(expected)
        derived=[metric for metric in calculation.metrics if metric.formula_id=="yoy_pct"]
        assert len(derived)==1 and abs(Decimal(derived[0].value)-expected)<Decimal("0.00000001")
        missing_baseline=QualityService().calculate_metrics([metric for metric in metrics if metric.period=="2024"],task)
        assert not any(metric.formula_id=="yoy_pct" and metric.value is not None for metric in missing_baseline.metrics)
        output["missing_baseline_negative"]="PASS"
    (root/"case-results.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf8")

if __name__=="__main__":
    main()
