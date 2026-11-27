import json
from pathlib import Path
import unittest
from backend.app.templates.catalog import TemplateCatalog
from backend.app.contracts.services import ServiceError

class SpecializedTemplatesTests(unittest.TestCase):
    def test_three_forms_and_missing_fields(self):
        catalog=TemplateCatalog()
        for kind,field in [("industry_prosperity","indicator"),("investment_attraction","target_segment"),("overseas_assessment","product")]:
            payload=json.loads(Path("templates/examples",kind+".json").read_text(encoding="utf8"))
            spec,template=catalog.validate_request(payload)
            self.assertEqual(spec.report_type,template.report_type)
            self.assertTrue(all(s.required for s in template.sections))
            for missing in (field,"as_of"):
                bad=dict(payload);bad.pop(missing)
                with self.assertRaises(ServiceError):
                    catalog.validate_request(bad)
        payload=json.loads(Path("templates/examples/overseas_assessment.json").read_text(encoding="utf8"))
        payload["region"]="OTHER"
        with self.assertRaises(ServiceError):catalog.validate_request(payload)
