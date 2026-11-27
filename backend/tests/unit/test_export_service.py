from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock
from backend.app.export.service import ExportService
from backend.app.export.pdf import PdfConverter
from backend.app.contracts.services import ServiceError
from backend.tests.unit.test_export_evidence import snapshot
from backend.app.quality.render import render_chart_png

class ExportServiceTests(unittest.TestCase):
    def test_isolated_publication_and_failure(self):
        snap = snapshot()
        png = render_chart_png(snap.charts[0])
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = ExportService(root, asset_resolver=lambda v,a:png)
            with ThreadPoolExecutor(2) as pool:
                artifacts = list(pool.map(lambda _:service.render_report(snap,"docx"), range(2)))
            self.assertNotEqual(artifacts[0].storage_key, artifacts[1].storage_key)
            for artifact in artifacts:
                payload = (root/artifact.storage_key).read_bytes()
                self.assertEqual(artifact.sha256, sha256(payload).hexdigest())
                self.assertEqual(artifact.size_bytes, len(payload))
            with self.assertRaises(ServiceError) as err:
                service.render_report(snap,"pdf")
            self.assertEqual(err.exception.error.details["reason"], "converter_missing")
            self.assertTrue((root/artifacts[0].storage_key).is_file())
            self.assertEqual(list((root/".export-staging").iterdir()), [])

    def test_converter_failures(self):
        import subprocess
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root/"converter";exe.write_bytes(b"stub")
            document = root/"report.docx";document.write_bytes(b"stub")
            converter = PdfConverter(exe)
            for effect, code, reason in [
                (subprocess.TimeoutExpired("converter",90),0,"timeout"),
                (None,0,"invalid_output"),
                (None,1,"converter_failed"),
            ]:
                process = MagicMock(returncode=code, pid=123)
                process.communicate.side_effect = [effect, None] if effect else None
                with self.subTest(reason=reason), patch("backend.app.export.pdf.subprocess.Popen",return_value=process), patch("backend.app.export.pdf.subprocess.run"):
                    with self.assertRaises(ServiceError) as err:converter.convert(document,root)
                    self.assertEqual(err.exception.error.details["reason"],reason)
            with self.assertRaises(ServiceError):PdfConverter(root/"absent").convert(document,root)

    def test_versions_and_invalid_path(self):
        snap=snapshot()
        with TemporaryDirectory() as tmp:
            service=ExportService(Path(tmp),asset_resolver=lambda v,a:render_chart_png(snap.charts[0]))
            first=service.render_report(snap,"docx")
            snap.version_id="version-2";snap.chapters[0].blocks[0].text="Changed version"
            second=service.render_report(snap,"docx")
            self.assertNotEqual(first.storage_key,second.storage_key)
            self.assertNotEqual(first.sha256,second.sha256)
            snap.version_id="../outside"
            with self.assertRaises(ServiceError):service.render_report(snap,"docx")

    def test_bad_layout_is_not_retryable(self):
        snap = snapshot()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = ExportService(root/"output", template_root=root,
                asset_resolver=lambda v,a:render_chart_png(snap.charts[0]))
            for broken in (False, True):
                if broken:
                    (root/"1").mkdir()
                    (root/"1/base.docx").write_bytes(b"broken")
                with self.assertRaises(ServiceError) as err:
                    service.render_report(snap,"docx")
                self.assertFalse(err.exception.error.retryable)
                self.assertEqual(err.exception.error.details["reason"],"layout_invalid")

    def test_toc_cache_is_inside_field_and_ordered(self):
        from docx import Document
        from io import BytesIO
        from backend.app.export.word import render_docx
        snap=snapshot()
        snap.chapters.reverse()
        doc=Document(BytesIO(render_docx(snap,asset_resolver=lambda v,a:render_chart_png(snap.charts[0]))))
        fields=doc.element.xpath(".//w:fldSimple")
        self.assertEqual(len(fields),1)
        self.assertEqual(fields[0].xpath(".//*[local-name()='t']/text()"),["市场规模","结论"])
        self.assertEqual(len(fields[0].xpath(".//*[local-name()='br']")),1)

    def test_readable_docx_missing_required_style_is_configuration_error(self):
        from docx import Document
        from backend.app.export.layout import ROOT
        snap = snapshot()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/"1").mkdir()
            for name in ("Report Cover Title", "Report Table Text", "Heading 1"):
                with self.subTest(style=name):
                    document = Document(ROOT/"1/base.docx")
                    document.styles[name].delete()
                    document.save(root/"1/base.docx")
                    service = ExportService(root/"output", template_root=root,
                        asset_resolver=lambda v,a:render_chart_png(snap.charts[0]))
                    with self.assertRaises(ServiceError) as err:
                        service.render_report(snap,"docx")
                    self.assertEqual(err.exception.error.details["reason"],"layout_invalid")
                    self.assertFalse(err.exception.error.retryable)
