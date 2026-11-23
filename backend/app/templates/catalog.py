"""Read-only catalog used by the Templates protocol."""
from pathlib import Path

from ..contracts import models as m
from ..contracts.services import ServiceError
from .loader import load_template
from .request import parse_request, request_template_id

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

    def list_templates(self) -> list[m.Template]:
        """Return all deployed versions in template ID and version order."""
        return [self._templates[key].model_copy(deep=True) for key in sorted(self._templates)]

    def get_current_template(self, template_id: str) -> m.Template:
        """Select the highest numeric version included in this deployment."""
        version = max(
            (version for id, version in self._templates if id == template_id),
            default=1,
        )
        return self.get_template(template_id, version)

    def get_template(self, id: str, version: int) -> m.Template:
        template = self._templates.get((id, version))
        if template is None:
            raise ServiceError(m.ErrorInfo(
                code="TEMPLATE_NOT_FOUND", message="未找到指定模板版本。",
                retryable=False, details={"template_id": id, "version": version},
            ))
        return template.model_copy(deep=True)

    def validate_request(
        self, payload: object, version: int | None = None,
    ) -> tuple[m.Spec, m.Template]:
        """Validate the raw request and return the spec with its exact deployed template."""
        template_id = request_template_id(payload)
        template = (
            self.get_current_template(template_id)
            if version is None
            else self.get_template(template_id, version)
        )
        return parse_request(payload, template), template
