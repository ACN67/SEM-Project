"""Validate and exercise the W5 acceptance data pack."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import TypeAdapter

from backend.app.contracts import models as m
from backend.app.quality import QualityService, format_percent

ROOT = Path(__file__).resolve().parents[3]
W5 = ROOT / "data/samples/w5"
ACCEPTANCE = ROOT / "data/samples/acceptance_metrics.json"
TEMPLATES = ROOT / "templates/content"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def metric_from_record(record: dict, contract_indicators: dict[str, str]) -> m.Metric:
    return m.Metric(
        metric_id=record["record_id"],
        indicator=contract_indicators[record["indicator"]],
        name=record["name"],
        entity_id=record.get("entity_id"),
        period=record["period"],
        granularity=record["granularity"],
        region=record["region"],
        scope=record["scope"],
        raw_value=record.get("value"),
        raw_unit=record.get("unit"),
        multiplier="1",
        value=record.get("value"),
        unit=record["unit"],
        currency=record.get("currency"),
        source_evidence_ids=record["evidence_keys"],
        formula_id=None,
        input_metric_ids=[],
        missing_reason=None,
    )


def shared_evidence(record: dict) -> m.Evidence:
    locator = record["locator"]
    if locator["kind"] == "pdf":
        parsed_locator = m.Pdf(kind="pdf", page=locator["page"])
    elif locator["kind"] == "xlsx":
        parsed_locator = m.Sheet(
            kind="xlsx",
            sheet=locator["sheet"],
            cells=locator["cell"],
        )
    else:
        raise ValueError("UNSUPPORTED_WEB_LOCATOR")
    if record["published_at"] is None:
        raise ValueError("MISSING_PUBLISHED_AT")
    return m.Evidence(
        title=record["title"],
        publisher=record["publisher"],
        published_at=record["published_at"],
        source_url=record["source_url"],
        evidence_id=record["evidence_key"],
        document_id=record["document_id"],
        document_version_id=record["document_version_id"],
        chunk_id=f"chunk_{record['evidence_key']}",
        locator=parsed_locator,
        excerpt=record["excerpt"],
        score=None,
    )


class AcceptancePackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.acceptance = read_json(ACCEPTANCE)
        cls.evidence_doc = read_json(W5 / "evidence.json")
        cls.cases_doc = read_json(W5 / "cases.json")
        cls.contract_indicators = cls.acceptance["technical_review"][
            "contract_indicators"
        ]
        cls.metric_records = {
            record["record_id"]: record for record in cls.acceptance["metrics"]
        }
        cls.evidence_records = {
            record["evidence_key"]: record
            for record in cls.evidence_doc["records"]
        }
        cls.cases = {
            case["case_id"]: case for case in cls.cases_doc["cases"]
        }
        cls.service = QualityService()

    def metrics_for(self, case_id: str, records: list[dict] | None = None) -> list[m.Metric]:
        selected = (
            records
            if records is not None
            else [self.metric_records[item] for item in self.cases[case_id]["metric_record_ids"]]
        )
        return [
            record
            if isinstance(record, m.Metric)
            else metric_from_record(record, self.contract_indicators)
            for record in selected
        ]

    def task_for(self, case_id: str) -> m.Task:
        case = self.cases[case_id]
        return m.Task(
            chapter_id=case_id,
            section_key=case_id.lower(),
            title=case["title"],
            order=0,
            required=True,
            depends_on=[],
            research_questions=[],
            evidence_requirements=[],
            required_metrics=case["required_contract_indicators"],
            desired_chart=None,
        )

    def calculate(self, case_id: str, records: list[dict] | None = None):
        return self.service.calculate_metrics(
            self.metrics_for(case_id, records),
            self.task_for(case_id),
        )

    def test_documents_match_schemas(self):
        for document_name, schema_name in (
            ("evidence.json", "evidence.schema.json"),
            ("cases.json", "cases.schema.json"),
        ):
            with self.subTest(document=document_name):
                document = read_json(W5 / document_name)
                schema = read_json(W5 / schema_name)
                Draft202012Validator(
                    schema,
                    format_checker=FormatChecker(),
                ).validate(document)

    def test_ids_references_and_required_metadata_are_complete(self):
        self.assertEqual(
            {case["case_id"] for case in self.cases_doc["cases"]},
            {"R01", "R02", "R03", "R04", "R05"},
        )
        self.assertEqual(
            len(self.metric_records),
            len(self.acceptance["metrics"]),
        )
        self.assertEqual(
            len(self.evidence_records),
            len(self.evidence_doc["records"]),
        )
        source_ids = {
            source["source_id"] for source in self.acceptance["source_refs"]
        }
        for record in self.acceptance["metrics"]:
            ids = record.get("source_ids", [])
            if not ids and "source_id" in record:
                ids = [record["source_id"]]
            self.assertTrue(set(ids) <= source_ids)

        metric_mapping_values = set(self.contract_indicators.values())
        for case in self.cases.values():
            self.assertTrue(
                set(case["required_contract_indicators"]) <= metric_mapping_values
            )
            self.assertTrue(
                set(case["metric_record_ids"]) <= set(self.metric_records)
            )
            self.assertTrue(set(case["evidence_keys"]) <= set(self.evidence_records))
            for conclusion in case["expected_conclusions"]:
                self.assertTrue(
                    set(conclusion["evidence_keys"]) <= set(self.evidence_records)
                )
            gap_ids = [gap["gap_case_id"] for gap in case["gap_cases"]]
            self.assertEqual(len(gap_ids), len(set(gap_ids)))

        for record in self.evidence_doc["records"]:
            self.assertEqual(record["case_id"], self.cases[record["case_id"]]["case_id"])
            if record["locator"]["kind"] in {"pdf", "xlsx"}:
                self.assertRegex(record["source_hash"], r"^[a-f0-9]{64}$")

    def test_selected_metrics_resolve_to_reviewed_evidence_in_the_same_case(self):
        for case in self.cases.values():
            for metric in self.metrics_for(case["case_id"]):
                with self.subTest(metric=metric.metric_id):
                    self.assertTrue(metric.source_evidence_ids)
                    for evidence_id in metric.source_evidence_ids:
                        self.assertIn(evidence_id, case["evidence_keys"])
                        evidence = self.evidence_records[evidence_id]
                        self.assertEqual(evidence["case_id"], case["case_id"])
                        self.assertEqual(evidence["review_status"], "PASS")
                        if evidence["locator"]["kind"] in {"pdf", "xlsx"}:
                            self.assertEqual(shared_evidence(evidence).evidence_id, evidence_id)

    def test_requests_are_shared_specs_and_contract_indicators_match_templates(self):
        adapter = TypeAdapter(m.Spec)
        parsed = {
            case_id: adapter.validate_python(case["request"])
            for case_id, case in self.cases.items()
        }
        self.assertEqual(set(parsed), {"R01", "R02", "R03", "R04", "R05"})

        for case_id, template_id in (
            ("R01", "industry_analysis_v1"),
            ("R02", "enterprise_comparison_v1"),
        ):
            template = read_json(TEMPLATES / template_id / "1.json")
            template_metrics = {
                indicator
                for section in template["sections"]
                for indicator in section["required_metrics"]
            }
            self.assertTrue(
                set(self.cases[case_id]["required_contract_indicators"])
                <= template_metrics
            )

    def test_records_convert_to_shared_metric_and_file_evidence(self):
        byd = metric_from_record(
            self.metric_records["r02-byd-r-and-d-2024"],
            self.contract_indicators,
        )
        self.assertEqual(byd.indicator, "rd_investment")
        self.assertEqual(byd.entity_id, "byd")

        sales = metric_from_record(
            self.metric_records["r01-cn-nev-sales-2024"],
            self.contract_indicators,
        )
        self.assertEqual(sales.indicator, "sales")

        pdf = shared_evidence(self.evidence_records[byd.source_evidence_ids[0]])
        self.assertIsInstance(pdf.locator, m.Pdf)
        self.assertEqual(pdf.locator.page, 34)

        bev = next(metric for metric in self.metrics_for("R05") if metric.period == "2024")
        sheet = shared_evidence(self.evidence_records[bev.source_evidence_ids[-1]])
        self.assertEqual(sheet.locator.sheet, "FZ 10.1")
        self.assertIsInstance(sheet.locator, m.Sheet)
        self.assertEqual(sheet.locator.cells, "AL488")

        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_WEB_LOCATOR"):
            shared_evidence(self.evidence_records["EV-R03-MIIT-2024-ANNUAL"])

    def assert_expected_metrics(self, case_id: str, records: list[dict] | None = None):
        result = self.calculate(case_id, records)
        derived = {
            metric.metric_id: format_percent(Decimal(metric.value))
            for metric in result.metrics
            if metric.formula_id == "yoy_pct"
        }
        for expected in self.cases[case_id]["expected_metrics"]:
            derived_id = f"yoy:{expected['source_metric_id']}"
            self.assertEqual(derived.get(derived_id), expected["display_value"])
        return result

    def test_positive_metric_expectations(self):
        for case_id in ("R01", "R02", "R03", "R05"):
            with self.subTest(case_id=case_id):
                result = self.assert_expected_metrics(case_id)
                self.assertEqual(result.gaps, [])

    def test_r01_missing_year_is_not_interpolated(self):
        case = self.cases["R01"]
        gap_case = case["gap_cases"][0]
        remove = set(gap_case["details"]["remove_metric_ids"])
        records = [
            self.metric_records[item]
            for item in case["metric_record_ids"]
            if item not in remove
        ]
        result = self.calculate("R01", records)
        codes = {gap.code for gap in result.gaps}
        self.assertTrue(set(gap_case["expected_codes"]) <= codes)
        self.assertNotIn(
            "yoy:r01-cn-nev-sales-2024",
            {metric.metric_id for metric in result.metrics},
        )

    def test_r02_unit_conflict_is_blocked(self):
        case = self.cases["R02"]
        gap_case = case["gap_cases"][0]
        target = gap_case["details"]["metric_record_id"]
        patched = []
        for record_id in case["metric_record_ids"]:
            record = deepcopy(self.metric_records[record_id])
            if record_id == target:
                record.update(gap_case["details"]["patch"])
            patched.append(record)
        result = self.calculate("R02", patched)
        codes = {gap.code for gap in result.gaps}
        self.assertTrue(set(gap_case["expected_codes"]) <= codes)

    def test_r03_zero_base_and_missing_quarter(self):
        zero_case = self.cases["R03"]["gap_cases"][0]
        target = zero_case["details"]["metric_record_id"]
        patched = []
        for record_id in self.cases["R03"]["metric_record_ids"]:
            record = deepcopy(self.metric_records[record_id])
            if record_id == target:
                record.update(zero_case["details"]["patch"])
                record["raw_value"] = zero_case["details"]["patch"]["value"]
            patched.append(record)
        result = self.calculate("R03", patched)
        self.assertIn("ZERO_BASE", {gap.code for gap in result.gaps})

        missing_case = self.cases["R03"]["gap_cases"][1]
        removed = set(missing_case["details"]["remove_metric_ids"])
        records = [
            self.metric_records[item]
            for item in self.cases["R03"]["metric_record_ids"]
            if item not in removed
        ]
        result = self.calculate("R03", records)
        self.assertNotIn(
            "yoy:r03-cn-nev-sales-2024-q3",
            {metric.metric_id for metric in result.metrics},
        )

    def test_r04_requires_both_demand_and_supply_evidence(self):
        case = self.cases["R04"]
        active = [self.evidence_records[key] for key in case["evidence_keys"]]
        self.assertTrue(any(item["role"] == "industry_demand" for item in active))
        self.assertTrue(any(item["role"] == "enterprise_capability" for item in active))

        for gap_case in case["gap_cases"]:
            removed = set(gap_case["details"]["remove_evidence_keys"])
            remaining = [
                self.evidence_records[key]
                for key in case["evidence_keys"]
                if key not in removed
            ]
            has_demand = any(
                item["role"] == "industry_demand"
                and item["review_status"] == "PASS"
                for item in remaining
            )
            has_supply = any(
                item["role"] == "enterprise_capability"
                and item["review_status"] == "PASS"
                for item in remaining
            )
            with self.subTest(gap_case=gap_case["gap_case_id"]):
                if "PENDING_DEMAND_EVIDENCE" in gap_case["expected_codes"]:
                    self.assertFalse(has_demand)
                    self.assertTrue(has_supply)
                if "PENDING_SUPPLY_EVIDENCE" in gap_case["expected_codes"]:
                    self.assertTrue(any(
                        item["role"] == "industry_demand" for item in remaining
                    ))
                    self.assertFalse(has_supply)

    def test_r05_policy_metadata_and_scope_confusion(self):
        policy_case = self.cases["R05"]["gap_cases"][0]
        evidence = deepcopy(
            self.evidence_records[policy_case["details"]["evidence_key"]]
        )
        evidence.update(policy_case["details"]["patch"])
        self.assertTrue(
            evidence["applies_to"] is None or evidence["effective_from"] is None
        )
        self.assertIn("POLICY_METADATA_MISSING", policy_case["expected_codes"])

        scope_case = self.cases["R05"]["gap_cases"][1]
        target = scope_case["details"]["metric_record_id"]
        patched = []
        for record_id in self.cases["R05"]["metric_record_ids"]:
            record = deepcopy(self.metric_records[record_id])
            if record_id == target:
                record.update(scope_case["details"]["patch"])
            patched.append(record)
        result = self.calculate("R05", patched)
        self.assertIn("SCOPE_MISMATCH", {gap.code for gap in result.gaps})

    def test_data_pack_preserves_known_source_hashes(self):
        expected = {
            "EV-R01-MEE-2024-P8": "abf5d83596647e7e4edbcb20d28736c086c769883249c59bbb62e2b63b701558",
            "EV-R02-BYD-CNINFO-P34": "e9c2d7fdd088e151ccb6c8ad3d95587b2b014b10f2c9731508d23ce07fde4de3",
            "EV-R02-SAIC-2023-P13": "4a2339816a97dc00b9196ed9d82b91373f1c3fc1f200313996b0605d74219e45",
            "EV-R02-SAIC-2024-P13": "c980b2d827f372b5b90742e299d609345c83342584901c96ea95891c43f4ab3f",
            "EV-R05-KBA-XLSX-AL488": "856b9afe515d51aa52bcb34d645dce2c5cdeaf47ef398b4e0a754c1bd5813dbf",
        }
        for key, digest in expected.items():
            with self.subTest(key=key):
                self.assertEqual(self.evidence_records[key]["source_hash"], digest)


if __name__ == "__main__":
    unittest.main()
