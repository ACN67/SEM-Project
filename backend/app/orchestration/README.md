# 任务编排模块（叶泽晨）

实现设计文档 §2.5 的后端业务与任务编排：HTTP 之外的全部任务状态、调度、持久化、恢复和版本事务都集中在这里。

## 模块结构

| 文件 | 职责 |
|---|---|
| `service.py` | `OrchestrationService`：`schedule_report`、`assess_coverage`、`prepare_chapter_context`（编排协议），以及 `run_cycle`、重试、取消、导出执行等 Worker 侧操作。 |
| `worker.py` | `Worker` 后台线程，循环调用 `run_cycle()`；单 Worker 顺序执行，不引入 Celery/Redis。 |
| `fallback.py` | `FixedWriter` 固定应答 Writer（联调用，结果通过 `model_id` 标记）与 `EmptyKnowledge`。 |
| `fixture_knowledge.py` | W5 验收数据包的只读知识适配器；单元测试与零配置演示共用，不能替代真实知识库做验收。 |
| `../database/` | 存储协议 + `MemoryStore`（测试/演示） + `MySqlStore`（迁移 020）。 |

## 任务生命周期

```
QUEUED → RUNNING(PLANNING) → RUNNING(GENERATING) → RUNNING(VALIDATING)
       → COMPLETED / COMPLETED_WITH_WARNINGS / FAILED / CANCELLED
```

- **规划**：`plan_outline` 冻结大纲；`FrozenRun`（需求、模板版本、READY 资料版本清单、共享事实）一次性写入 `jobs.frozen_json`。
- **DAG**：只启动依赖章全部 SUCCEEDED 的章节；失败章的下游被 `SKIP_DEPENDENTS` 标记 SKIPPED 并保留原因；必需章失败 → 任务 FAILED，其余成功章保留。
- **章节执行**：`prepare_chapter_context` 组包（多查询并集检索 + 指标计算 + 按 ID 解析指标来源证据）→ 生成 → `validate_chapter`/`build_charts` → SUCCEEDED。重试策略：暂时故障最多额外 2 次、格式错误最多修复 1 次、总上限 3 次（指数退避）。
- **版本快照**：全章完成后重建图表、`validate_report`、写图表 PNG 到 `runtime/assets/{version_id}/`、`check_snapshot` 兜底校验，保存不可变版本并更新当前版本指针。
- **恢复**：每次 cycle 先把心跳超过 90 秒的 RUNNING 任务标记 `FAILED/WORKER_LOST`；`POST /jobs/{id}/retry` 创建新 job，复用冻结输入、运行证据包（`context_json`）和已成功章节。
- **预算**：每次模型调用前按 `PER_CALL_RESERVE_CNY` 预留额度，超过 `BUDGET_CNY` 抛 `BUDGET_LIMIT`（当前为保守估算，真实用量统计接入 model_usage 后细化）。
- **幂等**：创建/重试/导出以（操作、目标、Idempotency-Key）唯一记录，验证后请求的排序 JSON SHA-256 判定同键异体 → 409 `IDEMPOTENCY_CONFLICT`；响应与资源创建同事务写入。

## 导出链路

`POST /versions/{id}/exports` 校验版本 `exportable`（必需章齐全、无过期章、无 BLOCK、必需规则已执行）→ 202 → Worker 调 `ExportService.render_report`（通过 `asset_resolver` 按 version_id + asset_id 读取图表 PNG）→ READY 后提供下载；未就绪下载返回 409 `EXPORT_NOT_READY`。

## 已知边界（按接口规范逐条补齐的后续项）

- `PATCH /reports/{id}/chapters/{cid}` 与章节 regenerate（R12 编辑/局部重写、STALE 传播）尚未实现，OpenAPI 契约已定义。
- 文档上传的异步处理通过 `ingest_runner` 调 `knowledge.pipeline.process(version, labels={})`；元数据级检索标签（entity/industry）需与资料登记流程一并设计。
- 真实知识库需补一个按 ID 读取证据的小方法 `get_evidences(evidence_ids, source_version_ids)`（fixture 适配器已有同签名实现），否则指标来源证据无法并入章节包。
- PDF 导出依赖部署机的 LibreOffice（`soffice.com`），未配置时导出报 `converter_missing`。

## 本地运行（零配置演示）

```powershell
.venv\Scripts\python.exe -m uvicorn backend.app.main:create_app --factory --port 8000
```

不设置 `DATABASE_URL` / `MODEL_*` 时自动进入演示模式：MemoryStore + W5 资料包 + 固定应答。执行一次生成到导出：

```powershell
$body = Get-Content templates/examples/industry_analysis.json -Raw
$created = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/reports -Body $body -ContentType "application/json" -Headers @{ "Idempotency-Key" = "demo-1" }
# 后台 Worker 每 2 秒一个 cycle；轮询任务状态
Invoke-RestMethod http://127.0.0.1:8000/api/v1/jobs/$created.job_id
```

正式模式：复制 `.env.example` 为 `.env`，配置 `DATABASE_URL`（先按 010→011→012→020 顺序应用迁移）与 `MODEL_*` 后重启。

## Review fixes (2026-11-28)
Runs freeze READY source IDs when accepted, including retries of the same run. Internal KnowledgeService.get_evidences resolves exact metric provenance within READY frozen versions. The project_budget row persists shared conservative reservations across jobs and restarts. Reservations are never refunded automatically; real provider usage settlement remains a separate integration step. PER_CALL_RESERVE_CNY must be configured conservatively for the provider's maximum call cost; no real provider charge was tested in this rework.
