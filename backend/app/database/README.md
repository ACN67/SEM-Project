# 持久化与存储（叶泽晨）

设计文档 §2.5「持久化模型」的实现层。领域模块不直接访问这里的表；编排服务和 HTTP 层通过 `Store` 协议读写。

## 文件

- `store.py`：`Store` 协议、`ReportRecord`/`JobRecord`/`ChapterRecord`/`ExportRecord` 记录类型、`MemoryStore`（线程内存实现，供单元测试与零配置演示）。
- `mysql.py`：`MySqlStore`，与 `MemoryStore` 同一接口，基于 PyMySQL 连接工厂；`connect_from_url(DATABASE_URL)` 返回该工厂。
- `../migrations/020_report_orchestration.sql`：业务表结构，在知识库 010–012 之后按序应用。

## 表与职责

| 表 | 内容 |
|---|---|
| `reports` | 报告项目：标题、模板身份、当前版本指针、最近任务状态镜像（列表页免联表）。 |
| `jobs` | 一次生成流程：状态机、stage、`spec_json`（校验后的需求）、`frozen_json`（冻结运行输入）、`context_json`（运行证据包，供重启后重试恢复）、心跳、错误、费用估算。 |
| `chapter_runs` | 章节级状态与已通过质检的 `content_json`，重试时复用成功章。 |
| `report_versions` | 不可变版本快照（完整 `ReportDocument` JSON）+ 导出资格与过期章列表。 |
| `exports` | 导出任务：状态、storage_key、SHA-256、大小、错误。 |
| `idempotency_keys` | （操作、目标、幂等键）唯一记录，含请求摘要与受理响应；保留至所属报告删除。 |
| `model_usage` | 模型调用记录（token、费用）；当前预留额度制先行，真实用量待模型联调接入。 |
| `admin_sessions` | 服务端管理会话，8 小时有效期。 |

## 语义要点

- 资源创建与幂等记录写入同一事务（`begin_report`/`begin_retry`/`begin_export`），崩溃后同键请求得到 409 而不是重复资源。
- 任务认领（`claim_next_queued`）与导出认领使用 `SELECT ... FOR UPDATE` 条件更新，只允许一个 Worker 领取。
- 时间戳统一按 UTC 写入（naive），读出时补 UTC 时区，满足契约 `AwareDatetime`。
- 报告、文档列表按创建时间倒序、同时间按 ID 倒序，与接口规范一致。

## 迁移

MySQL 8+：在业务库按 010 → 011 → 012 → 020 顺序执行（集成测试用与 `test_knowledge_mysql.py` 相同的按 `;` 拆分方式即可）。
