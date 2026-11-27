"""Run: python -m backend.tests.integration.verify_pdf <soffice> <output-root>."""
from pathlib import Path
from hashlib import sha256
import json
import subprocess
import sys
import time
from pypdf import PdfReader
from backend.app.export.service import ExportService
from backend.app.export.pdf import PdfConverter
from backend.tests.unit.test_export_evidence import snapshot
from backend.app.quality.render import render_chart_png

def verify(executable, root):
    data=snapshot()
    service=ExportService(root,converter=PdfConverter(executable),
        asset_resolver=lambda version,asset:render_chart_png(data.charts[0]))
    started=time.monotonic()
    artifacts=[]
    for version in ("acceptance-v1","acceptance-v2"):
        data.version_id=version
        data.chapters[0].blocks[0].text=version+" 模拟销量原始观测为 120 辆。"
        for format in ("docx","pdf"):
            artifact=service.render_report(data,format)
            file=root/artifact.storage_key
            assert artifact.sha256==sha256(file.read_bytes()).hexdigest()
            assert artifact.size_bytes==file.stat().st_size
            if format=="pdf":
                pages=PdfReader(file).pages
                text="\n".join(page.extract_text() for page in pages)
                assert version in text and "120" in text and "市场规模" in text
                assert "参考来源" in text
                artifacts.append({"version":version,"pages":len(pages),**artifact.model_dump()})
    result={"converter":subprocess.check_output([str(executable),"--version"],text=True).strip(),
        "seconds":time.monotonic()-started,"pdf_artifacts":artifacts}
    (root/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
    print(root)

if __name__=="__main__":
    root=Path(sys.argv[2]);root.mkdir(parents=True,exist_ok=True)
    verify(Path(sys.argv[1]),root)
