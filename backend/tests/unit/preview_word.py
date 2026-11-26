"""Generate representative synthetic Word samples for visual review."""
from pathlib import Path
import json
import sys

from backend.app.contracts.models import ReportDocument
from backend.app.export.word import render_docx

def snapshots():
    fixture = Path(__file__).parent / "fixtures" / "word_snapshot.json"
    for kind in ("industry", "comparison"):
        data = json.loads(fixture.read_text(encoding="utf8"))
        data["spec"]["industry"] = "新能源汽车及动力电池产业"
        if kind == "comparison":
            data["spec"].update(report_type="enterprise_comparison", template_id="enterprise_comparison_v1",
                                companies=["华东新能源汽车制造企业", "华南动力电池制造企业"])
            data["template_ref"]["template_id"] = "enterprise_comparison_v1"
        chapter = next(c for c in data["chapters"] if c["chapter_id"] == "ch_market")
        chapter["blocks"][0]["text"] = "以下为排版验收使用的模拟数据。报告按年度归集销量、研发投入和增长率，便于横向比较。"
        table = chapter["blocks"][1]
        table["columns"] = ["指标及统计对象", "2023 年", "2024 年", "同比"]
        labels = ["新能源汽车销量（万辆）", "研发投入（亿元）", "动力电池装机量（GWh）"]
        if kind == "comparison":
            table["columns"] = ["指标及统计对象", "华东企业", "华南企业", "差异率"]
        table["rows"] = [[{"text":v,"evidence_ids":[],"metric_ids":[]} for v in
                          (labels[i%3], str(i+100), str(i+120), "12.5%")] for i in range(36)]
        chapter["blocks"].insert(1, {"type":"paragraph","block_id":"visual_p","text":"统计期间覆盖两个年度。不同统计口径应结合报告证据解读，缺失值保留原有展示方式。以下表格用于验证列宽、对齐和分页效果。","evidence_ids":[],"metric_ids":[]})
        yield kind, ReportDocument.model_validate(data)

if __name__ == "__main__":
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    for name, snapshot in snapshots():
        (output / f"{name}.docx").write_bytes(render_docx(snapshot))
