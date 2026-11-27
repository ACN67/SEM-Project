from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import unittest
from scripts.verify_w5_sources import acquire
from scripts.w5_facts import check_fact


class SourceToolTests(unittest.TestCase):
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

    def test_same_page_without_expected_value_fails(self):
        key = "EV-R02-SAIC-2024-P13"
        self.assertFalse(check_fact(key, [SimpleNamespace(excerpt="Unrelated text on page 13")]))
        self.assertTrue(check_fact(key, [SimpleNamespace(excerpt="研发投入合计 21,813,090,045.30 元")]))
