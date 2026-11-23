# 智能报告生成

`GenerationService` 实现共享 `Generation` 接口，对应 R01–R05、R08 和 R12 的写作环节。

- `plan_outline(spec, template)` 按模板数组顺序保留章节、研究问题、证据及指标要求，将依赖的 `section_key` 解析成 `chapter_id`，校验悬空引用和依赖环。用户的主题、关注点和专用字段随冻结的 `spec` 传入写作。
- `generate_chapter(context)` 返回一章结构化正文、摘要、事实候选、图表意图及缺口。`generate_synthesis(context)` 使用同一章契约，综合给定的前置章摘要。依赖缺失或证据包越界会在请求模型前报错。
- 局部重写由 Worker 以冻结的目标章和重新准备的 `ChapterInput` 调用相同入口。章节通过质检后，可用 `summarize_chapter(chapter)` 生成携带正文引用的前置摘要，再由编排层装配下游证据包。
- 证据为空时直接返回缺证草稿。必需章缺证可由 `QualityService` 标记为 `BLOCK`。图表意图的 ID 由章和块决定，交给质检构建实际图表。

模型调用每次只发一个请求。Worker 管理预算、重试与版本事务。暂时错误可按接口规范重试，`INVALID_MODEL_OUTPUT` 和 `INVALID_EVIDENCE_REF` 属于输出修复类错误。模型输出使用共享 `Chapter` 的 JSON Schema 和 Pydantic 校验。引用白名单检查能确认 ID 是否存在，结论与证据的对应关系仍需通过项目验收样例核对。

## 分析写作

提示词要求以章节研究问题组织正文，先给判断，再写清比较对象、证据和适用范围。综合章把有关联的前置结果组织成一个答案。模型需要区分统计变化与因果解释，指出继续核对所需的资料。

景气报告在模型输入中增加 `quarterly_analysis`，提供本期、上年同期及已算同比的指标 ID，并比较相邻季度未舍入的同比。只有口径一致、来源可追溯且没有歧义的指标才进入这份输入。缺季或存在重复观测时跳过相应比较。字段仅在模型请求中使用，生成服务和调度端继续通过共享 `ChapterInput` 和 `Chapter` 交接。

## 离线验证

在仓库根目录使用 Python 3.11+ 执行以下命令。测试使用固定应答和本地 HTTP 模拟服务。

```sh
python -m pip install -r backend/tests/contract/requirements.txt
python -m unittest discover -s backend/tests/unit -p test_generation.py -v
python -m unittest discover -s backend/tests/contract -v
python scripts/export_contracts.py --check
```

## 真实模型单章验证

部署端通过进程环境注入 `MODEL_BASE_URL`、`MODEL_NAME`、`MODEL_API_KEY`。`MODEL_BASE_URL` 使用兼容 Chat Completions 的 API 前缀（包含服务版本路径，例如 `/v1`），也可配置完整的 `/chat/completions` 地址。服务须支持 `response_format: json_object`。

预算确认后，在仓库根目录用以下 Python 代码发起一次对共享测试章的真实调用。日志记录模型标识、耗时和服务返回的 token 用量，可用服务单价核算该次调用费用。

```python
import logging
from pathlib import Path

from backend.app.contracts.models import ChapterInput
from backend.app.generation import GenerationService
from backend.app.quality import QualityService

logging.basicConfig(level=logging.INFO)
fixture = Path("shared/contracts/fixtures/chapter_input.json")
context = ChapterInput.model_validate_json(fixture.read_text(encoding="utf-8"))
chapter = GenerationService.from_env().generate_chapter(context)
quality = QualityService().validate_chapter(chapter, context.evidences, context.metrics)
print(chapter.model_dump_json(indent=2))
print(quality.model_dump_json(indent=2))
```
