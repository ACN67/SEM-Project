# 数据质量模块

`QualityService` 实现 `backend/app/contracts/services.py` 中的 `Quality` 接口，负责确定性指标计算、图表数据、单章质检和全文质检。模块不访问知识库、数据库或模型，也不修改生成正文。

## 输入与输出

- `calculate_metrics`：接收结构化 `Metric`，返回原指标、同比派生指标和缺口。同比只比较主体、地区、口径、粒度、单位和币种一致的相邻年份同期值。
- `build_charts`：把 `ChartRequest` 转为折线、柱状或表格数据。缺失值保留为空点，不插值；单位不一致或没有数值时不生成图表。
- `render_chart_png`：把图表数据渲染为 PNG 字节流。字体可通过 `SEM_CHART_FONT` 指定；PNG 存储和 HTTP 资源服务仍由编排或导出模块负责。
- `validate_chapter`：检查结构、证据白名单、指标引用、正文数值和图表位置。
- `validate_report`：另行检查必需章节、依赖环、资料版本和过期章节。

`BLOCK` 表示结构损坏、无法解析的引用、缺必需章、依赖错误或明确数值冲突，会阻止正式导出。`WARN` 表示资料或核验能力有限，完整报告仍可导出。

## 同比约定

- 公式 ID 为 `yoy_pct`。
- 派生指标 ID 为 `yoy:{本期_metric_id}`。
- `input_metric_ids` 按“基期、本期”保存，`source_evidence_ids` 合并两侧来源。
- 内部使用十进制定点计算并保留未舍入百分比；正文展示使用四舍五入两位小数。
- 基数为零、缺同期、单位/口径/币种冲突或候选不唯一时返回对应 `Gap`，不生成同比。

## 本地验证

在仓库根目录执行：

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r backend/tests/contract/requirements.txt
.venv/bin/python -m pip install -r backend/app/quality/requirements.txt
.venv/bin/python -m unittest discover -s backend/tests/unit -v
.venv/bin/python -m unittest discover -s backend/tests/contract -v
.venv/bin/python scripts/export_contracts.py --check
```

真实 R03 回归测试读取 `data/samples/acceptance_metrics.json`，核对 2024 年四个季度同比为 `31.78%`、`32.07%`、`33.39%`、`41.31%`。

## W5 测试适配器

`backend/tests/unit/w5_fixture_knowledge.py` 提供只读的 `Knowledge` 测试实现，从 W5 JSON 读取 `Evidence`、`Metric`、案例请求和版本范围，支持 `retrieve()`、`get_metrics()` 及 `ChapterInput` 装配。它用于在正式知识库完成前验证生成与质量链路，不解析文件，也不替代知识库服务。

共享 `Evidence.locator` 尚无网页类型。测试适配器把网页证据表示为带真实 `source_url` 的“网页快照”DOCX locator，只为贯通测试传输；正式知识库必须保留网页快照并生成可定位证据，不能把该测试表示当作生产定位。
