import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jsonschema import Draft202012Validator, ValidationError as SchemaError
from pydantic import TypeAdapter, ValidationError

from backend.app.contracts import models as m
from backend.app.contracts.services import ServiceError
from backend.app.generation.outline import plan_outline
from backend.app.templates import TemplateCatalog
from backend.app.templates.catalog import REPO_ROOT


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.catalog = TemplateCatalog()
        self.adapter = TypeAdapter(m.Spec)

    def request(self, kind):
        path = REPO_ROOT / "templates/examples" / f"{kind}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_example_requests_and_generation_outline(self):
        for kind, metric, chart in (
            ("industry_analysis", "sales", "line"),
            ("enterprise_comparison", "rd_investment", "table"),
        ):
            with self.subTest(kind=kind):
                data = self.request(kind)
                template = self.catalog.get_template(data["template_id"], 1)
                Draft202012Validator.check_schema(template.input_schema)
                Draft202012Validator(
                    template.input_schema, format_checker=Draft202012Validator.FORMAT_CHECKER,
                ).validate(data)
                spec = self.adapter.validate_python(data)
                self.catalog.validate_request(spec, 1)
                outline = plan_outline(spec, template)
                self.assertEqual(outline.chapters[0].section_key, "summary")
                self.assertEqual(
                    set(outline.chapters[0].depends_on),
                    {chapter.chapter_id for chapter in outline.chapters[1:]},
                )
                self.assertTrue(all(task.required for task in outline.chapters))
                self.assertIn((metric, chart), {
                    (indicator, task.desired_chart)
                    for task in outline.chapters for indicator in task.required_metrics
                })
                self.assertEqual(template, self.catalog.get_template(spec.template_id, 1))
                template.input_schema["required"].clear()
                template.sections.clear()
                self.assertTrue(self.catalog.get_template(spec.template_id, 1).sections)
                self.assertTrue(self.catalog.get_template(spec.template_id, 1).input_schema["required"])

    def test_rejects_missing_policy_date_and_missing_form_fields(self):
        data = self.request("industry_analysis")
        template = self.catalog.get_template(data["template_id"], 1)
        validator = Draft202012Validator(
            template.input_schema, format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        for date in (None, "invalid", "2024-02-30"):
            with self.subTest(date=date), self.assertRaises(SchemaError):
                validator.validate({**data, "as_of": date})
        del data["as_of"]
        with self.assertRaises(SchemaError):
            validator.validate(data)
        with self.assertRaises(ServiceError) as error:
            self.catalog.validate_request(self.adapter.validate_python(data), 1)
        self.assertEqual(error.exception.error.code, "INVALID_TEMPLATE_INPUT")
        del data["industry"]
        with self.assertRaises(ValidationError):
            self.adapter.validate_python(data)

    def test_rejects_duplicate_companies_and_unknown_version(self):
        data = self.request("enterprise_comparison")
        template = self.catalog.get_template(data["template_id"], 1)
        self.assertNotIn("as_of", template.input_schema["required"])
        data["companies"] = ["比亚迪", "比亚迪"]
        with self.assertRaises(SchemaError):
            Draft202012Validator(template.input_schema).validate(data)
        with self.assertRaises(ValidationError):
            self.adapter.validate_python(data)
        with self.assertRaises(ServiceError) as error:
            self.catalog.get_template(template.template_id, 99)
        self.assertEqual(error.exception.error.code, "TEMPLATE_NOT_FOUND")
        self.assertFalse(error.exception.error.retryable)

    def test_invalid_configuration_identifies_file_and_field(self):
        source = REPO_ROOT / "templates/content/industry_analysis_v1/1.json"
        for first, second, field in (
            ({"section_key": "overview"}, {}, "section_key"),
            ({"order": 1}, {}, "order"),
            ({"depends_on": ["unknown"]}, {}, "depends_on"),
            ({"depends_on": ["summary"]}, {}, "cycle"),
            ({"depends_on": ["overview"]}, {"depends_on": ["summary"]}, "cycle"),
        ):
            with self.subTest(first=first, second=second), TemporaryDirectory() as directory:
                data = json.loads(source.read_text(encoding="utf-8"))
                data["sections"][0].update(first)
                data["sections"][1].update(second)
                root = Path(directory)
                path = root / source.parent.name / source.name
                path.parent.mkdir()
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError) as error:
                    TemplateCatalog(root)
                self.assertIn(str(path), str(error.exception))
                self.assertIn(field, str(error.exception))
