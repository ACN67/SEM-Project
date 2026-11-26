from contextlib import closing
from pathlib import Path
from docx import Document
import test_knowledge_mysql
from backend.app.knowledge.text_repository import TextRepository

class TextMySQLTests(test_knowledge_mysql.KnowledgeMySQLTests):
    def test_atomic_text_evidence(self):
        with closing(self.connect(self.database)) as connection, connection.cursor() as cursor:
            for statement in Path("backend/migrations/011_knowledge_metrics.sql").read_text().split(";"):
                if statement.strip():
                    cursor.execute(statement)
            cursor.execute("CREATE TRIGGER reject_evidence BEFORE INSERT ON evidences FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test rollback'")
        document = Document()
        document.add_heading("Heading",1)
        document.add_paragraph("First evidence")
        path = self.root / "source.docx"
        document.save(path)
        accepted = self.ingest.ingest_document(path,self.metadata)
        repo = TextRepository(lambda: self.connect(self.database),self.root / "runtime")
        with self.assertRaises(Exception):
            repo.ingest_version(accepted.document_version_id)
        with closing(self.connect(self.database)) as connection, connection.cursor() as cursor:
            for table in ("chunks","evidences"):
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                self.assertEqual(cursor.fetchone()[0],0)
            cursor.execute("DROP TRIGGER reject_evidence")
        first = repo.ingest_version(accepted.document_version_id,industry="auto")
        self.assertEqual(len(first.evidences),2)
        self.assertEqual(first,repo.ingest_version(accepted.document_version_id,industry="auto"))
        with self.assertRaises(ValueError):
            repo.ingest_version(accepted.document_version_id,industry="other")
        with closing(self.connect(self.database)) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM evidences")
            self.assertEqual(cursor.fetchone()[0],2)
            cursor.execute("SELECT COUNT(*) FROM metrics")
            self.assertEqual(cursor.fetchone()[0],0)
