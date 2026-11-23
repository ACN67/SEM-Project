import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jsonschema import Draft202012Validator

from backend.app.contracts import models as m
from backend.app.contracts.services import ServiceError
from backend.app.generation.outline import plan_outline
from backend.app.templates import TemplateCatalog
from backend.app.templates.catalog import REPO_ROOT


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.catalog = TemplateCatalog()

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
                spec, resolved_template = self.catalog.validate_request(data)
                self.assertEqual(resolved_template, template)
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

    def assert_invalid(self, payload, *paths):
        with self.assertRaises(ServiceError) as error:
            self.catalog.validate_request(payload)
        self.assertEqual(error.exception.error.code, "VALIDATION_ERROR")
        self.assertEqual({item["path"] for item in error.exception.error.details["errors"]}, set(paths))

    def test_requires_raw_date_on_industry_request(self):
        data = self.request("industry_analysis")
        spec, _ = self.catalog.validate_request(data)
        self.assertEqual(spec.as_of.isoformat(), data["as_of"])
        for value in (None, "invalid", "2024-02-30", "2025-12-31T00:00:00"):
            with self.subTest(value=value):
                self.assert_invalid({**data, "as_of": value}, "/as_of")
        del data["as_of"]
        self.assert_invalid(data, "/as_of")
        del data["industry"]
        del data["period"]["from"]
        self.assert_invalid(data, "/as_of", "/industry", "/period/from")

    def test_comparison_supports_no_date_and_checks_business_rules(self):
        data = self.request("enterprise_comparison")
        spec, template = self.catalog.validate_request(data)
        self.assertIsInstance(spec, m.Comparison)
        self.assertIsNone(spec.as_of)
        self.assertNotIn("as_of", template.input_schema["required"])
        for companies in (["比亚迪", "比亚迪"], ["比亚迪", " 比亚迪 "]):
            with self.subTest(companies=companies):
                self.assert_invalid({**data, "companies": companies}, "/companies")
        self.assert_invalid({**data, "period": {"from": "2025", "to": "2024"}}, "/period")
        self.assert_invalid({**data, "report_type": "industry_analysis"}, "/report_type")
        self.assert_invalid({**data, "template_id": ""}, "/template_id")
        with self.assertRaises(ServiceError) as error:
            self.catalog.validate_request(data, version=99)
        self.assertEqual(error.exception.error.code, "TEMPLATE_NOT_FOUND")
        self.assertFalse(error.exception.error.retryable)

    def test_deployed_versions_are_ordered_and_highest_version_is_frozen(self):
        kind = "industry_analysis"
        data = self.request(kind)
        source = REPO_ROOT / "templates/content" / data["template_id"] / "1.json"
        config = json.loads(source.read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / source.parent.name
            folder.mkdir()
            for version in (10, 2, 1):
                (folder / f"{version}.json").write_text(
                    json.dumps({**config, "version": version, "requires_as_of": version != 1}),
                    encoding="utf-8",
                )
            catalog = TemplateCatalog(root)
            self.assertEqual([item.version for item in catalog.list_templates()], [1, 2, 10])
            _, current = catalog.validate_request(data)
            self.assertEqual(current.version, 10)
            del data["as_of"]
            _, frozen = catalog.validate_request(data, version=1)
            self.assertEqual(frozen.version, 1)
            current.sections.clear()
            catalog.list_templates()[0].sections.clear()
            self.assertTrue(catalog.get_current_template(data["template_id"]).sections)
            self.assertTrue(catalog.get_template(data["template_id"], 1).sections)

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
