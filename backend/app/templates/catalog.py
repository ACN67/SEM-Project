"""Read-only catalog used by the Templates protocol."""
from pathlib import Path

from ..contracts import models as m
from ..contracts.services import ServiceError
from .loader import load_template

REPO_ROOT = Path(__file__).resolve().parents[3]


class TemplateCatalog:
    def __init__(
        self,
        root: Path = REPO_ROOT / "templates/content",
        forms_root: Path = REPO_ROOT / "shared/contracts/schema/forms",
    ):
        root = root.resolve(strict=True)
        self._templates: dict[tuple[str, int], m.Template] = {}
        for path in sorted(root.glob("*/*.json")):
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"{path}: template file is outside the template directory")
            template = load_template(path, forms_root)
            key = (template.template_id, template.version)
            if key in self._templates:
                raise ValueError(f"{path}: duplicate template_id/version {key}")
            self._templates[key] = template
        if not self._templates:
            raise ValueError(f"{root}: no templates found")

    def get_template(self, id: str, version: int) -> m.Template:
        template = self._templates.get((id, version))
        if template is None:
            raise ServiceError(m.ErrorInfo(
                code="TEMPLATE_NOT_FOUND", message="未找到指定模板版本。",
                retryable=False, details={"template_id": id, "version": version},
            ))
        return template.model_copy(deep=True)

    def validate_request(self, spec: m.Spec, version: int) -> None:
        """Validate template-specific constraints before scheduling a new run."""
        template = self.get_template(spec.template_id, version)
        if spec.report_type != template.report_type or (
            "as_of" in template.input_schema["required"] and spec.as_of is None
        ):
            raise ServiceError(m.ErrorInfo(
                code="INVALID_TEMPLATE_INPUT", message="报告类型或政策研判截止日不符合模板要求。",
                retryable=False, details={"template_id": spec.template_id, "version": version},
            ))
