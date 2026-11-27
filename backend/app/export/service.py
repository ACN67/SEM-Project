"""Export the caller's frozen report without accessing mutable report state."""
from pathlib import Path
from tempfile import TemporaryDirectory
from ..contracts import models as m
from ..contracts.services import ServiceError
from .artifacts import publish, version_component
from .pdf import export_error
from .word import render_docx

class ExportService:
    def __init__(self, runtime_root: Path, *, converter=None, asset_resolver=None, template_root=None):
        self.runtime = Path(runtime_root).resolve()
        self.converter = converter
        self.asset_resolver = asset_resolver
        self.template_root = template_root

    def render_report(self, snapshot: m.ReportDocument, format: m.Format):
        if format not in ("docx", "pdf"):
            raise export_error("invalid_format")
        version = version_component(snapshot.version_id)
        try:
            word = render_docx(snapshot, asset_resolver=self.asset_resolver, layout_root=self.template_root)
            staging = self.runtime / ".export-staging"
            staging.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=staging) as directory:
                work = Path(directory)
                document = work / ("report-" + version + ".docx")
                document.write_bytes(word)
                output = document
                if format == "pdf":
                    if self.converter is None:
                        raise export_error("converter_missing")
                    output = self.converter.convert(document, work)
                return publish(output, self.runtime, version)
        except ServiceError:
            raise
        except Exception as exc:
            raise export_error("export_io_failed", retryable=True) from exc
