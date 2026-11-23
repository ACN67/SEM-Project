"""Validate incoming JSON against the chosen template before model coercion."""
from collections.abc import Iterable

from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from ..contracts import models as m
from ..contracts.services import ServiceError

SPEC_ADAPTER = TypeAdapter(m.Spec)
SCHEMA_MESSAGES = {
    "additionalProperties": "含未定义字段。",
    "const": "字段值与模板要求不一致。",
    "enum": "请选择模板允许的值。",
    "format": "日期须为有效的 YYYY-MM-DD。",
    "minItems": "选项数量不足。",
    "minLength": "字段不能为空。",
    "pattern": "字段格式不符合模板要求。",
    "type": "字段类型不符合模板要求。",
    "uniqueItems": "选项不得重复。",
}


def _pointer(parts: Iterable[int | str]) -> str:
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _invalid(errors: list[dict[str, str]]) -> ServiceError:
    return ServiceError(m.ErrorInfo(
        code="VALIDATION_ERROR", message="报告参数校验失败。",
        retryable=False, details={"errors": errors},
    ))


def request_template_id(payload: object) -> str:
    if not isinstance(payload, dict):
        raise _invalid([{"path": "", "message": "请求正文须为 JSON 对象。"}])
    template_id = payload.get("template_id")
    if not isinstance(template_id, str) or not template_id.strip():
        raise _invalid([{"path": "/template_id", "message": "请选择有效的模板标识。"}])
    return template_id


def parse_request(payload: object, template: m.Template) -> m.Spec:
    validator = Draft202012Validator(
        template.input_schema, format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    errors = []
    missing_paths = set()
    for error in validator.iter_errors(payload):
        path = tuple(error.absolute_path)
        if error.validator == "required":
            for key in error.validator_value:
                if key not in error.instance:
                    pointer = _pointer((*path, key))
                    if pointer not in missing_paths:
                        errors.append({"path": pointer, "message": "缺少必填字段。"})
                        missing_paths.add(pointer)
        else:
            errors.append({
                "path": _pointer(path),
                "message": SCHEMA_MESSAGES.get(error.validator, "字段不符合模板约束。"),
            })
    if errors:
        raise _invalid(errors)
    try:
        return SPEC_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        issues = []
        for error in exc.errors():
            path = error["loc"]
            if path and path[0] == template.report_type:
                path = path[1:]
            if not path and template.report_type == "enterprise_comparison":
                # Comparison currently has one model-level rule: distinct company IDs after trimming.
                issues.append({"path": "/companies", "message": "企业标识不得重复。"})
            else:
                issues.append({"path": _pointer(path), "message": "字段不符合报告业务约束。"})
        raise _invalid(issues) from exc
