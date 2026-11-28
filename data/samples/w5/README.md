# W5 验收数据包

数据包版本：1.1（2026-11-28）；JSON 结构版本仍为 1.0。

年度基线统计期末为 2024-12-31（JSON 的 `data_cutoff`）。各案例用 `request.as_of` 约束可用证据的发布日期。年度复盘可使用统计期后发布、在研究截止日前已公开的资料。

指标复核：陈易

来源、真实服务复验与逐项采用说明见 doc/testing/W5-acceptance/README.md；本轮编制后交 Astra 独立复核。

本目录固定 R01-R05 的指标、证据定位、预期计算、预期结论和缺口案例。大体积 PDF/XLSX 不进入仓库，只登记稳定 URL、文件哈希和页/单元格定位。

## 文件

- `cases.json`：五类正向案例及缺口案例。
- `evidence.json`：原文摘录、证据角色、适用对象、生效日期和定位。
- `cases.schema.json`、`evidence.schema.json`：Draft 2020-12 JSON Schema。
- `../acceptance_metrics.json`：原始指标和 `contract_indicators` 映射。

## 使用规则

1. 指标先按 `contract_indicators` 映射成共享 `Metric.indicator`，例如 `nev_sales` 映射为 `sales`，`r_and_d_investment_total` 映射为 `rd_investment`。
2. R03 的同比由 `QualityService` 从原始季度值计算；已有 `nev_sales_yoy` 记录只作人工预期，不重复参与计算。
3. 报告只能使用 `evidence.json` 中 `review_status=PASS` 的证据。`PENDING_EVIDENCE` 或缺口案例不得被改写成已核验事实。
4. PDF 页码同时区分物理 PDF 页和文内页码；XLSX 使用工作表与单元格；网页使用页面锚点或章节名称。
5. 指标的 `evidence_keys` 对应具体定位，供装配 `Metric.source_evidence_ids`。原件标识仍保存在 `source_id` 或 `source_ids`。R04 规划目标和无发布日期的动态网页保留为待核实资料。
6. 来源文件的 SHA-256 用于校验文件版本。网页没有静态文件哈希时允许为 `null`，但必须保留发布日期和访问日期。

当前共享 `Evidence.locator` 只支持 PDF、DOCX、XLSX 和 CSV。PDF/XLSX 证据可测试转换为共享对象；纯网页记录先保留完整来源元数据，待知识库解析并保存网页快照后再转成共享 `Evidence`。测试适配器为了贯通生成与质量链路，会把网页锚点包装成带真实 `source_url` 的“网页快照”DOCX locator；该表示只存在于测试目录，不属于正式知识库。

## 状态

- `READY`：正向数据齐全，可进入模板与生成联调。
- `READY_WITH_LIMITATION`：核心结论可形成，但存在明确限制或未覆盖资料。
- `PENDING_EVIDENCE`：缺必需一侧或必要政策属性，只能形成待核实项。

任何来源替换、哈希变化、页码变化或指标口径变化都必须形成数据包新版本，不能静默覆盖。

## 1.1 数据修订

- R01 的 2024 年销量改用 2025-01-13 发布的工信部年度原文，同样注明中汽协统计口径；原生态环境部年报记录保留作补充。未改变 2024 年销量或研究截止日。
- R02 必需来源采用比亚迪 2024 年报第 34 页的 2024/2023 比较表；保留 2023 年报作补充。比亚迪基线改为原表精度 399.17743、541.60964 亿元，展示同比仍为 35.68%。
- R05 新增 2024/2522 普通关税表证据。CN 8703 80 10 的常规税率为 10%，表自 2025-01-01 适用；仅支持指定产品、日期和税项，不代表全部进口税负。
- capture_routes.json 登记同一官方法律原文的可获取入口及节选范围。网页文字转存、原始 PDF、手工整理表各有独立哈希与来源关联，不互相冒充原件。
- prepare_w5_tables.py 从已获取资料抽取原值，季度使用累计端点差分。整理表经 KnowledgeService 导入，并与原件清单关联。完整机器复验入口为 scripts/verify_w5_cases.py。
