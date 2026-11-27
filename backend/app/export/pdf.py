"""Isolated LibreOffice conversion."""
from pathlib import Path
import subprocess
import os
from ..contracts import models as m
from ..contracts.services import ServiceError

def export_error(reason: str, *, retryable: bool = False) -> ServiceError:
    return ServiceError(m.ErrorInfo(code="EXPORT_FAILED", message="Document export failed",
        retryable=retryable, details={"reason": reason}))

class PdfConverter:
    def __init__(self, executable: Path, timeout: float = 90):
        self.executable = Path(executable)
        self.timeout = timeout

    def convert(self, document: Path, work: Path) -> Path:
        if not self.executable.is_file():
            raise export_error("converter_missing")
        profile = (work / "profile").resolve().as_uri()
        try:
            process = subprocess.Popen([str(self.executable), "-env:UserInstallation=" + profile,
                "--headless", "--convert-to", "pdf", "--outdir", str(work), str(document)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                process.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    process.kill()
                process.communicate()
                raise export_error("timeout", retryable=True) from exc
        except OSError as exc:
            raise export_error("converter_missing") from exc
        output = document.with_suffix(".pdf")
        if process.returncode != 0:
            raise export_error("converter_failed")
        try:
            from pypdf import PdfReader
            if not output.is_file() or output.stat().st_size == 0 or not PdfReader(output).pages:
                raise ValueError("empty PDF")
        except Exception as exc:
            raise export_error("invalid_output") from exc
        return output
