from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from uuid import uuid4

import pymysql

from backend.app.contracts.models import Metadata
from backend.app.contracts.services import ServiceError
from backend.app.knowledge.ingest import DocumentIngest
from backend.app.knowledge.repository import DocumentRepository
from backend.app.knowledge.storage import OriginalStorage

MIGRATION = Path(__file__).resolve().parents[2] / "migrations/010_knowledge_ingest.sql"


@unittest.skipUnless(os.getenv("W1_MYSQL_PORT"), "set W1_MYSQL_PORT to use an isolated test database")
class KnowledgeMySQLTests(unittest.TestCase):
    def connect(self, database=None):
        return pymysql.connect(
            host="127.0.0.1", port=int(os.environ["W1_MYSQL_PORT"]),
            user=os.environ.get("W1_MYSQL_USER", "root"), password=os.environ.get("W1_MYSQL_PASSWORD", ""),
            database=database, charset="utf8mb4", autocommit=True,
        )

    def setUp(self):
        self.database = f"w1_test_{uuid4().hex}"
        with closing(self.connect()) as connection, connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{self.database}` CHARACTER SET utf8mb4")
        self.addCleanup(self.drop_database)
        for _ in range(2):
            with closing(self.connect(self.database)) as connection, connection.cursor() as cursor:
                for statement in MIGRATION.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        cursor.execute(statement)
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = DocumentRepository(lambda: self.connect(self.database))
        self.ingest = DocumentIngest(OriginalStorage(self.root / "runtime"), self.repo)
        self.metadata = Metadata(title="初版", publisher="测试机构", published_at=date(2024, 1, 1))

    def drop_database(self):
        with closing(self.connect()) as connection, connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE `{self.database}`")

    def upload(self, folder, name, content):
        target = self.root / folder / name
        target.parent.mkdir(exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def test_duplicate_alias_and_revision_remain_pollable(self):
        original = self.upload("a", "资料.csv", "销量\n100\n")
        alias = self.upload("b", "别名.csv", "销量\n100\n")
        revised = self.upload("c", "资料.csv", "销量\n200\n")
        changed = self.metadata.model_copy(update={"title": "修订"})
        first = self.ingest.ingest_document(original, self.metadata)
        repeat = self.ingest.ingest_document(original, self.metadata)
        self.assertEqual(first, repeat)
        self.assertEqual(first.ingest_status, "QUEUED")
        with closing(self.connect(self.database)) as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE document_versions SET ingest_status = 'READY' WHERE document_version_id = %s",
                           (first.document_version_id,))
        duplicate = self.ingest.ingest_document(alias, changed)
        new = self.ingest.ingest_document(revised, changed)
        self.assertEqual(duplicate.document_version_id, first.document_version_id)
        self.assertEqual(duplicate.ingest_status, "READY")
        self.assertEqual(new.document_id, first.document_id)
        self.assertNotEqual(new.document_version_id, first.document_version_id)
        self.assertEqual(first.status_url, f"/api/v1/documents/{first.document_id}/versions/{first.document_version_id}")
        reopened = DocumentRepository(lambda: self.connect(self.database))
        self.assertEqual(reopened.get_version(first.document_id, first.document_version_id).title, "初版")
        self.assertEqual(reopened.get_latest(first.document_id).document_version_id, new.document_version_id)
        self.assertIsNone(reopened.get_version("wrong-id", first.document_version_id))
        with closing(self.connect(self.database)) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM document_versions")
            self.assertEqual(cursor.fetchone()[0], 2)
        print(f"duplicate/revision: {first.document_id} / {first.document_version_id} / {duplicate.document_version_id} / {new.document_version_id}")

    def concurrent_upload(self, contents, filenames):
        inputs = [self.upload(str(i), name, text) for i, (name, text) in enumerate(zip(filenames, contents))]
        barrier = Barrier(len(inputs), timeout=10)

        def ingest(path):
            barrier.wait()
            return self.ingest.ingest_document(path, self.metadata)

        with ThreadPoolExecutor(max_workers=len(inputs)) as executor:
            return list(executor.map(ingest, inputs))

    def test_concurrent_same_content_has_one_version(self):
        records = self.concurrent_upload(["year\n2024"] * 4, ["a.csv", "b.csv", "c.csv", "a.csv"])
        self.assertEqual(len({item.document_version_id for item in records}), 1)
        with closing(self.connect(self.database)) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM documents")
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute("SELECT COUNT(*) FROM document_versions")
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_concurrent_same_name_with_different_content_has_one_document(self):
        records = self.concurrent_upload([f"year\n{year}" for year in range(2020, 2024)], ["a.csv"] * 4)
        self.assertEqual(len({item.document_id for item in records}), 1)
        self.assertEqual(len({item.document_version_id for item in records}), 4)

    def test_failed_insert_rolls_back_database_and_retry_uses_blob(self):
        original = self.upload("a", "资料.csv", "销量\n100\n")
        with closing(self.connect(self.database)) as connection, connection.cursor() as cursor:
            cursor.execute("CREATE TRIGGER fail_ingest BEFORE INSERT ON document_versions FOR EACH ROW "
                           "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'failure test'")
            with self.assertRaises(ServiceError) as error:
                self.ingest.ingest_document(original, self.metadata)
            self.assertEqual(error.exception.error.code, "DOCUMENT_STORAGE_ERROR")
            for table in ("documents", "document_filenames", "document_versions"):
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("DROP TRIGGER fail_ingest")
        blobs = list((self.root / "runtime/knowledge/originals").iterdir())
        self.assertEqual(len(blobs), 1)
        self.assertEqual(self.ingest.ingest_document(original, self.metadata).ingest_status, "QUEUED")
        self.assertEqual(list((self.root / "runtime/knowledge/originals").iterdir()), blobs)
