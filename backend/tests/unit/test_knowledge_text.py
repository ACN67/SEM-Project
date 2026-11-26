from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
import unittest
from docx import Document
from pypdf import PdfWriter
from backend.app.contracts.models import DocumentRecord
from backend.app.contracts.services import ServiceError
from backend.app.knowledge.text import parse_text

class TextTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/"original"
        self.record=DocumentRecord(document_id="doc",document_version_id="version",title="test",
            publisher="test",published_at=date(2024,1,1),ingest_status="QUEUED",error=None)

    def test_docx_locations_and_chunks(self):
        doc=Document()
        doc.add_heading("第一章",1)
        doc.add_paragraph("")
        doc.add_heading("第二节",2)
        doc.add_paragraph("新能源汽车。" * 170)
        doc.save(self.path)
        result=parse_text(self.path,"source.docx",self.record)
        chunks=[e for e in result.evidences if e.locator.paragraph==4]
        self.assertGreater(len(chunks),1)
        self.assertTrue(all(len(e.excerpt)<=800 and e.locator.section_path==["第一章","第二节"] for e in chunks))
        self.assertEqual(result,parse_text(self.path,"source.docx",self.record))

    def test_blank_and_encrypted_pdf(self):
        writer=PdfWriter()
        writer.add_blank_page(width=100,height=100)
        writer.write(self.path)
        with self.assertRaises(ServiceError):
            parse_text(self.path,"source.pdf",self.record)
        writer.encrypt("password")
        writer.write(self.path)
        with self.assertRaises(ServiceError):
            parse_text(self.path,"source.pdf",self.record)

    def test_pdf_physical_page_with_blank(self):
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer=PdfWriter()
        font=DictionaryObject({NameObject("/Type"):NameObject("/Font"),NameObject("/Subtype"):NameObject("/Type1"),
                               NameObject("/BaseFont"):NameObject("/Helvetica")})
        font_ref=writer._add_object(font)
        for text in ("first page",None,"third page"):
            page=writer.add_blank_page(width=300,height=300)
            if text:
                page[NameObject("/Resources")]=DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):font_ref})})
                stream=DecodedStreamObject()
                stream.set_data(f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode())
                page[NameObject("/Contents")]=writer._add_object(stream)
        writer.write(self.path)
        result=parse_text(self.path,"source.pdf",self.record)
        self.assertEqual([e.locator.page for e in result.evidences],[1,3])
        self.assertIn("third page",result.evidences[-1].excerpt)
        self.assertEqual(result.diagnostics,["Page 2: no readable text"])
