from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from backend.app.contracts.services import ServiceError
from backend.app.knowledge.storage import MAX_SIZE, OriginalStorage, filename_key


class OriginalStorageTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.storage = OriginalStorage(self.root / "runtime")

    def test_accepts_each_supported_format_and_byte_preserves_original(self):
        inputs = {
            "资料.csv": "年度,销量\n2024,1286.6\n".encode("utf-8-sig"),
            "资料.pdf": b"%PDF-1.4\n% sample\n%%EOF\n",
        }
        for suffix, part in (("docx", "word/document.xml"), ("xlsx", "xl/workbook.xml")):
            source = self.root / f"资料.{suffix}"
            with ZipFile(source, "w") as package:
                package.writestr("[Content_Types].xml", "<Types/>")
                package.writestr(part, "<sample/>")
            inputs[source.name] = source.read_bytes()
        for filename, content in inputs.items():
            with self.subTest(filename=filename):
                source = self.root / filename
                source.write_bytes(content)
                checksum, storage_key = self.storage.persist(source)
                self.assertTrue(storage_key.endswith(checksum))
                self.assertEqual((self.root / "runtime" / storage_key).read_bytes(), content)

    def test_rejects_bad_uploads_and_cleans_staging(self):
        for filename, content, code in (
            ("empty.pdf", b"", "INVALID_DOCUMENT"),
            ("file.exe", b"MZ", "UNSUPPORTED_FILE_TYPE"),
            ("fake.pdf", b"not a pdf", "UNSUPPORTED_FILE_TYPE"),
            ("fake.xlsx", b"PKbroken", "UNSUPPORTED_FILE_TYPE"),
            ("fake.csv", b"%PDF-1.4\n%%EOF", "UNSUPPORTED_FILE_TYPE"),
            ("binary.csv", b"a,\x00b", "UNSUPPORTED_FILE_TYPE"),
        ):
            with self.subTest(filename=filename):
                source = self.root / filename
                source.write_bytes(content)
                with self.assertRaises(ServiceError) as error:
                    self.storage.persist(source)
                self.assertEqual(error.exception.error.code, code)
        large = self.root / "large.csv"
        with large.open("wb") as stream:
            stream.truncate(MAX_SIZE + 1)
        with self.assertRaises(ServiceError) as error:
            self.storage.persist(large)
        self.assertEqual(error.exception.error.code, "FILE_TOO_LARGE")
        self.assertEqual(list((self.root / "runtime/knowledge/originals").iterdir()), [])

    def test_filename_key(self):
        self.assertEqual(filename_key("Ａ资料.CSV"), filename_key("a资料.csv"))
        for filename in ("CON.csv", "../x.csv", "x.csv ", "x:y.csv"):
            with self.subTest(filename=filename), self.assertRaises(ServiceError):
                filename_key(filename)
