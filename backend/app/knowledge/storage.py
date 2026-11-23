"""Validate and persist uploaded originals before registering a database version."""
import csv
from hashlib import sha256
import os
from pathlib import Path, PureWindowsPath
from tempfile import NamedTemporaryFile
from unicodedata import normalize
from zipfile import BadZipFile, ZipFile

from ..contracts import models as m
from ..contracts.services import ServiceError

MAX_SIZE = 50 * 1024 * 1024
BLOCK_SIZE = 1024 * 1024
OFFICE_PARTS = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml"}


def ingest_error(code: str, message: str, *, retryable: bool = False) -> ServiceError:
    return ServiceError(m.ErrorInfo(code=code, message=message, retryable=retryable, details={}))


def filename_key(name: str) -> bytes:
    normalized = normalize("NFKC", name)
    key = normalized.casefold().encode("utf-8")
    if (
        not key or len(key) > 1020 or len(name) > 255
        or normalized.endswith((".", " "))
        or PureWindowsPath(normalized).is_reserved()
        or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in normalized)
    ):
        raise ingest_error("INVALID_FILENAME", "资料文件名无效。")
    return key


def _check_type(path: Path, suffix: str) -> None:
    try:
        if suffix == ".pdf":
            with path.open("rb") as stream:
                if not stream.read(8).startswith(b"%PDF-"):
                    raise ValueError
                stream.seek(-min(path.stat().st_size, 1024), os.SEEK_END)
                if b"%%EOF" not in stream.read():
                    raise ValueError
        elif suffix in OFFICE_PARTS:
            with ZipFile(path) as package:
                package.getinfo("[Content_Types].xml")
                part = package.getinfo(OFFICE_PARTS[suffix])
                if not part.file_size or part.flag_bits & 1:
                    raise ValueError
        else:
            with path.open("rb") as stream:
                if stream.read(8).startswith((b"%PDF-", b"PK\x03\x04")):
                    raise ValueError
            has_data = False
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.reader(stream, strict=True):
                    if any(
                        ord(char) < 32 and char not in "\t\r\n"
                        for cell in row for char in cell
                    ):
                        raise ValueError
                    has_data |= any(cell.strip() for cell in row)
            if not has_data:
                raise ValueError
    except (ValueError, KeyError, BadZipFile, csv.Error) as exc:
        raise ingest_error("UNSUPPORTED_FILE_TYPE", "实际文件内容与声明的格式不符。") from exc


class OriginalStorage:
    def __init__(self, runtime_root: Path):
        self._root = runtime_root.resolve() / "knowledge" / "originals"

    def persist(self, source: Path) -> tuple[str, str]:
        suffix = source.suffix.lower()
        if suffix not in {".csv", ".pdf", *OFFICE_PARTS}:
            raise ingest_error("UNSUPPORTED_FILE_TYPE", "仅支持 PDF、DOCX、XLSX 和 CSV。")
        if not source.is_file():
            raise ingest_error("INVALID_DOCUMENT", "资料文件不存在或不可读取。")

        temporary: Path | None = None
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            digest = sha256()
            size = 0
            with source.open("rb") as incoming, NamedTemporaryFile(
                dir=self._root, prefix=".ingest-", delete=False,
            ) as output:
                temporary = Path(output.name)
                while block := incoming.read(BLOCK_SIZE):
                    size += len(block)
                    if size > MAX_SIZE:
                        raise ingest_error("FILE_TOO_LARGE", "资料大小不得超过 50 MiB。")
                    output.write(block)
                    digest.update(block)
                output.flush()
                os.fsync(output.fileno())
            if size == 0:
                raise ingest_error("INVALID_DOCUMENT", "资料文件为空。")
            _check_type(temporary, suffix)
            checksum = digest.hexdigest()
            destination = self._root / checksum
            try:
                os.link(temporary, destination)
            except FileExistsError:
                pass
            with destination.open("rb"):
                pass
            return checksum, f"knowledge/originals/{checksum}"
        except OSError as exc:
            raise ingest_error("DOCUMENT_IO_ERROR", "读取或保存资料失败。", retryable=True) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
