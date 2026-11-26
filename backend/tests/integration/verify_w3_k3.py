"""Opt-in W3 -> K3 acceptance using the registered original SAIC PDF.

Run from the repository root with the knowledge and export dependencies installed:
python -m backend.tests.integration.verify_w3_k3 <original.pdf> <output-dir>
"""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
from decimal import Decimal

from docx import Document

from backend.app.contracts import models as m
from backend.app.contracts.services import ServiceError
from backend.app.contracts.validation import check_snapshot
from backend.app.knowledge.text import parse_text
from backend.app.export.word import render_docx
from backend.app.quality.render import render_chart_png

ROOT = Path(__file__).resolve().parents[3]
VALUES = ("17,649,893,105.21", "4,163,196,940.09", "21,813,090,045.30")


def verify(original: Path, output: Path):
    manifest = json.loads((ROOT / "data/samples/w5/evidence.json").read_text(encoding="utf8"))
    source = next(r for r in manifest["records"] if r["evidence_key"] == "EV-R02-SAIC-2024-P13")
    digest = sha256(original.read_bytes()).hexdigest()
    assert digest == source["source_hash"], "Original PDF hash differs from registered source"
    record = m.DocumentRecord(**{key:source[key] for key in (
        "document_id","document_version_id","title","publisher","published_at","source_url")},
        ingest_status="QUEUED",error=None)
    parsed = parse_text(original,source["file_version"],record)
    evidence = next(e for e in parsed.evidences if e.locator.page == source["locator"]["page"]
                    and all(value in e.excerpt for value in VALUES))
    repeated = parse_text(original,source["file_version"],record)
    assert evidence in repeated.evidences, "Repeated parsing changed evidence identity"
    data = m.ReportDocument.model_validate_json(
        (ROOT / "shared/contracts/fixtures/report_document.json").read_text(encoding="utf8"))
    data.source_version_ids = [record.document_version_id]
    data.evidences = [evidence]
    data.spec.period.from_ = "2024"
    data.version_id = "w3-k3-saic-acceptance"
    data.report_id = "w3-k3-saic-acceptance"
    data.model_id = "manual-acceptance-fixture"

    # Values are manually bound from the verified excerpt, not automatically
    # promoted by the text parser. Use exact arithmetic for conversion.
    numbers = [Decimal(value.replace(",","")) for value in VALUES]
    assert numbers[0] + numbers[1] == numbers[2]
    names = ["费用化研发投入", "资本化研发投入", "研发投入合计"]
    metrics = []
    for index,(name,number) in enumerate(zip(names,numbers,strict=True)):
        metrics.append(m.Metric(metric_id=f"rd-{index+1}",indicator="rd_investment",name=name,
            entity_id="SAIC",period="2024",granularity="year",region="中国",scope="上汽集团合并口径",
            raw_value=format(number,"f") if index<2 else None,raw_unit="元" if index<2 else None,
            multiplier="0.00000001" if index<2 else "1",value=format(number/Decimal(100000000),"f"),
            unit="亿元",currency="CNY",source_evidence_ids=[evidence.evidence_id] if index<2 else [],
            formula_id=None if index<2 else "sum",input_metric_ids=[] if index<2 else ["rd-1","rd-2"],
            missing_reason=None))
    data.metrics = metrics
    chapter = data.chapters[0]
    chapter.title = "上汽集团研发投入"
    chapter.summary = "上汽集团2024年合并口径研发投入"
    chapter.blocks[0].text = "2024 年，上汽集团合并口径研发投入合计 21,813,090,045.30 元（约 218.13 亿元）。"
    chapter.blocks[0].evidence_ids = [evidence.evidence_id]
    chapter.blocks[0].metric_ids = ["rd-3"]
    task = data.outline.chapters[0]
    task.title = chapter.title
    task.required_metrics = ["rd_investment"]
    task.research_questions = ["2024年上汽集团合并口径研发投入是多少？"]
    task.evidence_requirements = ["上汽集团2024年年报物理第13页"]
    chart = data.charts[0]
    chart.title = "上汽集团2024年研发投入构成及合计"
    chart.source_metric_ids = [metric.metric_id for metric in metrics]
    chart.unit = "亿元"
    chart.data = [m.Point(label=metric.period,series=metric.name,value=metric.value,metric_id=metric.metric_id)
                  for metric in metrics]
    chapter.chart_requests[0] = m.ChartRequest.model_validate(chart.model_dump(include=set(m.ChartRequest.model_fields)))
    check_snapshot(data)
    png = render_chart_png(chart,width=900,height=540)
    assets = {(data.version_id,chart.asset_id):png}
    word = render_docx(data,asset_resolver=lambda version,asset:assets[(version,asset)])
    rendered = Document(BytesIO(word))
    texts = [p.text for p in rendered.paragraphs]
    assert len(rendered.inline_shapes) == 1
    assert any("21,813,090,045.30" in t and "[1]" in t for t in texts)
    assert "来源：[1]" in texts
    entries = [t for t in texts if t.startswith("[1] ")]
    assert len(entries) == 1
    assert all(value in entries[0] for value in (source["publisher"],source["title"],
        source["published_at"],"PDF 第 13 页",source["source_url"]))
    assert not any(t.startswith("[2] ") for t in texts)

    broken = data.model_copy(deep=True)
    broken.metrics[1].source_evidence_ids = []
    def unexpected_asset(*args):
        raise AssertionError("Asset was read before evidence validation")
    try:
        render_docx(broken,asset_resolver=unexpected_asset)
    except ServiceError as error:
        assert error.error.code == "EXPORT_FAILED"
        assert error.error.details["chart_id"] == chart.chart_id
        assert error.error.details["metric_id"] == "rd-2"
        failure = error.error.model_dump(mode="json")
    else:
        raise AssertionError("Incomplete provenance was exported")

    output.mkdir(parents=True,exist_ok=True)
    (output/"report.docx").write_bytes(word)
    (output/"chart.png").write_bytes(png)
    result = {"source_hash":digest,"evidence":evidence.model_dump(mode="json"),
              "manual_metric_inputs":[metric.model_dump(mode="json") for metric in metrics],
              "reference":entries[0],"positive":"PASS","negative":failure}
    (output/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
    print(json.dumps({"source_hash":digest,"evidence_id":evidence.evidence_id,
                      "locator":evidence.locator.model_dump(),"positive":"PASS","negative":failure},
                     ensure_ascii=True))


if __name__ == "__main__":
    verify(Path(sys.argv[1]),Path(sys.argv[2]))
