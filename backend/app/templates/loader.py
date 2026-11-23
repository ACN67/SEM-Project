"""Load versioned report templates and their shared form schemas."""
import json
from graphlib import TopologicalSorter
from pathlib import Path

from ..contracts import models as m
from ..contracts.validation import indexed, refs

FORMS = {
    "industry_analysis": "Industry.json",
    "enterprise_comparison": "Comparison.json",
    "industry_prosperity": "Prosperity.json",
    "investment_attraction": "Investment.json",
    "overseas_assessment": "Overseas.json",
}


def load_template(path: Path, forms_root: Path) -> m.Template:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("expected a JSON object")
        requires_as_of = config.pop("requires_as_of", False)
        if not isinstance(requires_as_of, bool):
            raise ValueError("requires_as_of must be boolean")
        form_name = FORMS.get(config.get("report_type"))
        if form_name is None:
            raise ValueError("invalid report_type")
        form_path = forms_root / form_name
        schema = json.loads(form_path.read_text(encoding="utf-8"))
        if requires_as_of:
            schema["properties"]["as_of"] = {"type": "string", "format": "date"}
            if "as_of" not in schema["required"]:
                schema["required"].append("as_of")
        if "input_schema" in config:
            raise ValueError("input_schema is assembled from the shared form")
        template = m.Template.model_validate({**config, "input_schema": schema}, strict=True)
        if not template.sections:
            raise ValueError("sections must not be empty")
        sections = indexed(template.sections, "section_key")
        indexed(template.sections, "chapter_id")
        indexed(template.sections, "order")
        for section in template.sections:
            refs(section.depends_on, sections, f"depends_on for {section.section_key}")
        sorter = TopologicalSorter({key: task.depends_on for key, task in sections.items()})
        tuple(sorter.static_order())
        if path.parent.name != template.template_id or path.name != f"{template.version}.json":
            raise ValueError("template_id/version must match the directory and filename")
        return template
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
