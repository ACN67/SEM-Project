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

    def test_selected_indicator_reaches_outline_without_mutating_catalog(self):
        from backend.app.generation.outline import plan_outline
        catalog=TemplateCatalog()
        payload=json.loads(Path("templates/examples/industry_prosperity.json").read_text(encoding="utf8"))
        payload["indicator"]="rd_investment"
        spec,template=catalog.validate_request(payload)
        outline=plan_outline(spec,template)
        self.assertEqual(next(t.required_metrics for t in outline.chapters if t.chapter_id=="quarterly"),["rd_investment"])
        self.assertEqual(next(t.required_metrics for t in template.sections if t.chapter_id=="quarterly"),["sales"])

    def test_changed_business_scope_has_no_demo_constraints(self):
        from backend.app.generation.outline import plan_outline
        cases = [
            ("investment_attraction", {"region": "苏州", "target_segment": "充电设施"}, ("合肥", "动力电池")),
            ("overseas_assessment", {"origin": "日本", "region": "法国", "target_market": "法国", "product": "混合动力乘用车"}, ("中国", "德国", "纯电")),
        ]
        for kind, changes, forbidden in cases:
            payload=json.loads(Path("templates/examples",kind+".json").read_text(encoding="utf8"))
            payload.update(changes)
            spec,template=TemplateCatalog().validate_request(payload)
            outline=plan_outline(spec,template)
            questions=" ".join(q for task in outline.chapters for q in task.research_questions)
            for word in forbidden:
                self.assertNotIn(word,questions)
            for key,value in changes.items():
                self.assertEqual(getattr(spec,key),value)
