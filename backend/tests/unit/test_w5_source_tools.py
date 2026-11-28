from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import unittest
from scripts.verify_w5_sources import acquire
from scripts.w5_facts import matches_candidate


class SourceToolTests(unittest.TestCase):
    def test_gateway_errors_and_canonical_source_mismatch_are_rejected(self):
        from scripts.capture_w5_web import validate_rendition
        url='https://example.org/?uri=celex%3A32018R0858'
        prefix='URL Source: https://example.org/?uri=celex:32018R0858\n'
        validate_rendition(prefix+'valid public text '*40,url)
        for body in ('403 ERROR', 'JavaScript is disabled', 'Target URL returned error 403'):
            with self.assertRaises(ValueError):
                validate_rendition(prefix+body+' filler '*80,url)
        with self.assertRaises(ValueError):
            validate_rendition(prefix+'valid public text '*40,'https://other.org')

    def test_failed_download_can_recover_from_bad_cache(self):
        record = dict(evidence_key="ev", case_id="R01", source_id="s", title="Expected title",
            publisher="P", published_at="2024-01-01", source_url="https://example.org/page",
            locator={"kind": "web"}, source_hash=None)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "ev.html"
            target.write_text("error page")
            response = MagicMock()
            response.__enter__.return_value.read.return_value = b"<html>Expected title</html>"
            with patch("urllib.request.urlopen", return_value=response):
                self.assertEqual(acquire(record, root)["status"], "WEB_REQUIRES_FROZEN_PDF")
            self.assertIn("Expected title", target.read_text())
            target.write_text("error page")
            response.__enter__.return_value.read.return_value = b"error"
            with patch("urllib.request.urlopen", return_value=response):
                self.assertEqual(acquire(record, root)["status"], "BLOCKED")
            self.assertFalse(target.exists())

    def test_candidate_matching_does_not_verify_meaning(self):
        key = "EV-R02-SAIC-2024-P13"
        self.assertFalse(matches_candidate(key, [SimpleNamespace(excerpt="Unrelated text on page 13")]))
        self.assertTrue(matches_candidate(key, [SimpleNamespace(excerpt="研发投入合计 21,813,090,045.30 元")]))

    def test_empty_results_never_pass_case_or_negative_acceptance(self):
        from scripts.summarize_w5 import summarize
        for case in summarize([]):
            self.assertEqual(case["acceptance"], "INCOMPLETE")
            self.assertEqual(case["business_negative"]["status"], "NOT_RUN")

    def test_negation_is_only_a_candidate_match(self):
        self.assertTrue(matches_candidate("EV-R04-GOTION-LAB-2023",
            [SimpleNamespace(excerpt="2023-02-21 大众未向国轩授予认证。")]))
        from scripts.summarize_w5 import summarize
        cases=summarize([{"evidence_key":"EV-R04-GOTION-LAB-2023","status":"CANDIDATE_MATCH"},
            {"evidence_key":"EV-R04-HEFEI-IMPL-P3","status":"CANDIDATE_MATCH"}])
        case=next(c for c in cases if c["case_id"]=="R04")
        self.assertEqual(case["source_coverage"], "COVERED")
        self.assertEqual(case["fact_review"], "PENDING")
        self.assertEqual(case["acceptance"], "INCOMPLETE")
