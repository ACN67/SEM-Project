"""Offline contract checks; synthetic fixtures only."""
import json
from pathlib import Path
import unittest
from pydantic import TypeAdapter, ValidationError
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate
from backend.app.contracts import models as m
from backend.app.contracts.validation import check_generation, check_snapshot

BASE = Path(__file__).resolve().parents[3] / "shared/contracts"
def read(path):
    return json.loads((BASE / path).read_text(encoding="utf8"))
def fixture(name):
    return read("fixtures/"+name+".json")

class Baseline(unittest.TestCase):
    def test_api(self):
        doc = read("schema/openapi.json")
        validate(doc)
        for path, methods in doc["paths"].items():
            for method,op in methods.items():
                self.assertIn("422",op["responses"])
                if "202" in op["responses"]:
                    ref=op["responses"]["202"]["content"]["application/json"]["schema"]["$ref"]
                    self.assertIn("status_url",doc["components"]["schemas"][ref.split("/")[-1]]["required"])

    def test_forms(self):
        for model in (m.Industry,m.Comparison,m.Prosperity,m.Investment,m.Overseas):
            schema=read("schema/forms/"+model.__name__+".json")
            doc=fixture(schema["properties"]["report_type"]["const"])
            Draft202012Validator(schema,format_checker=FormatChecker()).validate(doc)
            self.assertIsInstance(TypeAdapter(m.Spec).validate_python(doc),model)

    def test_rejected_forms(self):
        for name,updates in [("enterprise_comparison",{"companies":["A","A"]}),
                             ("overseas_assessment",{"target_market":"其他市场"}),
                             ("industry_analysis",{"period":{"from":"2025","to":"2024"}}),
                             ("investment_attraction",{"target_segment":""})]:
            with self.subTest(name=name),self.assertRaises(ValidationError):
                TypeAdapter(m.Spec).validate_python(fixture(name)|updates)

    def test_chapter_and_snapshot(self):
        ctx=m.ChapterInput.model_validate(fixture("chapter_input"))
        chapter=m.Chapter.model_validate(fixture("chapter"))
        check_generation(ctx,chapter)
        check_snapshot(m.ReportDocument.model_validate(fixture("report_document")))
        chapter.blocks[0].evidence_ids=["not_in_context"]
        with self.assertRaises(ValueError):
            check_generation(ctx,chapter)

    def test_stale_version(self):
        doc=m.ReportDocument.model_validate(fixture("report_document"))
        doc.stale_chapter_ids=[doc.chapters[0].chapter_id]
        with self.assertRaises(ValueError):
            check_snapshot(doc)
        doc.exportable=False
        check_snapshot(doc)

    def test_http_examples(self):
        for name,model in [("edit_response",m.Edited),("job_detail",m.JobDetail),("conflict",m.Error)]:
            model.model_validate(fixture(name))
