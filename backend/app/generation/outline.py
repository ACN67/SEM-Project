"""Freeze display order and resolve template dependency keys to chapter IDs."""
from graphlib import CycleError, TopologicalSorter

from ..contracts import models as m
from ..contracts.validation import indexed
from .client import generation_error


def plan_outline(spec: m.Spec, template: m.Template) -> m.Outline:
    if spec.template_id != template.template_id or spec.report_type != template.report_type:
        raise generation_error("INVALID_OUTLINE", "报告需求与模板不匹配。")
    if "as_of" in template.input_schema.get("required", ()) and spec.as_of is None:
        raise generation_error("INVALID_OUTLINE", "该模板要求填写政策研判截止日。")

    try:
        sections = indexed(template.sections, "section_key")
        indexed(template.sections, "chapter_id")
        tasks = []
        for order, section in enumerate(template.sections):
            task = section.model_copy(deep=True, update={
                "order": order,
                "depends_on": list(dict.fromkeys(
                    sections[key].chapter_id for key in section.depends_on
                )),
            })
            tasks.append(task)
        tuple(TopologicalSorter({task.chapter_id: task.depends_on for task in tasks}).static_order())
        return m.Outline(chapters=tasks)
    except KeyError:
        raise generation_error("INVALID_OUTLINE", "模板引用了不存在的章节键。") from None
    except CycleError:
        raise generation_error("INVALID_OUTLINE", "模板章节依赖存在环。") from None
    except ValueError:
        raise generation_error("INVALID_OUTLINE", "模板章节为空或标识重复。") from None
