# SEM-Project · 产业智能报告自动生成系统

软件工程管理课程 G05 小组项目。研究员选择报告类型、产业、地区和期间后，系统从预建资料库检索证据，按章组织分析，生成带图表和来源的 Word、PDF 报告。

| 交付节点 | 可检查的成果 |
|---|---|
| 第 5 周 | 从网页生成产业分析和企业对标两类报告，并按版本导出。 |
| 第 7 周 | 景气、招商、出海研判三类接入同一流程，五类报告均可运行。 |
| 第 8–10 周 | 先用五个真实案例统一复验，再完成部署、演示和课程交付。 |

**当前进度**　已建立目录、接口定义、模拟样例和契约测试。首次开发可以从契约测试开始。Vue 页面、FastAPI 业务入口和持续集成工作流属于首轮实现任务。

## 接到任务后从哪里读

| 你要解决的问题 | 文档 | 主要内容 |
|---|---|---|
| 谁负责、何时交付？ | [项目计划书 v1.0](doc/项目文档/项目计划书%20v1.0.md) | 六人分工、10 周里程碑、依赖、预算、风险和交付清单。 |
| 用户要什么、怎样算完成？ | [软件需求规格书 v1.0](doc/项目文档/软件需求规格书%20v1.0.md) | 五类验收案例 A–E、功能需求 R01–R13、非功能需求 N01–N04 及对应测试。 |
| 功能在哪个模块实现？ | [设计文档 v1.2](doc/项目文档/设计文档%20v1.2.md) | 第二部分说明模块职责，第三部分说明数据流与接口，第五部分说明仓库协作和测试。 |
| 调用时具体传什么、返回什么？ | [接口规范 v1.0](doc/contracts/接口规范%20v1.0.md) | 请求、响应、管理会话、幂等、重试、版本冲突及模块交接。字段以链接的共享类型和 OpenAPI 为准。 |

例如，负责景气报告时，从需求 R03 和案例 C 找到验收条件，再读设计第 3.2 节的季度指标规则，用 [景气报告请求样例](shared/contracts/fixtures/industry_prosperity.json) 联调，最后按 TC03 核对结果。

## 六人分工与代码位置

| 负责人 | 工作目录 | 交给其他模块的成果 |
|---|---|---|
| 常珂舒 | `templates/`、`backend/app/templates/`、`backend/app/export/` | 五类内容模板、排版与版本化导出；组织接口评审和最终集成。 |
| 王凯乐 | `backend/app/knowledge/`、`data/samples/` | 资料导入、冻结范围内的检索、可定位的证据和原始指标。 |
| 潘永健 | `backend/app/generation/` | 大纲与章节生成、带来源的综合章节及局部重写。 |
| 陈易 | `backend/app/quality/` | 确定性计算、图表资源、章节和全文的质量检查结果。 |
| 叶泽晨 | `backend/app/api/`、`database/`、`orchestration/`（后两者同在 `backend/app/`）、`backend/migrations/` | HTTP 接口、任务调度、持久化、恢复和版本事务。 |
| 廖晨皓 | `frontend/src/`、`frontend/tests/` | 五类报告表单、任务进度、证据预览、编辑与下载。 |

跨模块交接统一使用 [Python 共享类型与服务签名](backend/app/contracts/)、[HTTP OpenAPI](shared/contracts/schema/openapi.json) 和 [联调样例](shared/contracts/fixtures/)。样例中的数字用于测试。对真实报告的验收使用已登记来源的产业资料。

## 首次本地验证

准备 Git 和 Python 3.11。在已有工作副本中从仓库根目录开始；首次从 GitHub 获取项目可运行以下两行。

```powershell
git clone https://github.com/ACN67/SEM-Project.git
cd SEM-Project
```

在 Windows PowerShell 中创建虚拟环境、安装契约测试依赖，并执行两项检查。此流程无需数据库或模型密钥。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/tests/contract/requirements.txt
.\.venv\Scripts\python.exe scripts/export_contracts.py --check
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests/contract -v
```

第一项检查仓库中的接口文件是否与代码定义一致，成功时退出码为 0。第二项检查五类表单、异常输入、引用、任务响应和过期版本等样例，成功时显示 `OK`。实际接入模型或数据库时，参照 [.env.example](.env.example) 配置本机环境，密钥存放在受忽略的本地配置中。

## 后端最小链路与全部测试

任务编排、持久化与 HTTP 层位于 [backend/app/orchestration/README.md](backend/app/orchestration/README.md) 和 [backend/app/database/README.md](backend/app/database/README.md)。安装全部单元测试依赖后（含 FastAPI 与文档解析工具），可以本地运行整条验证链：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/tests/contract/requirements.txt -r backend/tests/unit/requirements.txt
.\.venv\Scripts\python.exe scripts/export_contracts.py --check
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests/contract -v
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests/unit -v
```

静态检查：`python -m ruff check --select E9,F63,F7,F82 backend scripts`。CI 另通过 MySQL 8.0 服务执行 `python -m unittest backend.tests.integration.test_orchestration_mysql -v`；本地运行时设置 `W1_MYSQL_PORT` 指向无密码 root 的隔离测试实例，测试会创建并删除临时数据库，请勿指向业务数据库。

单元测试覆盖编排模块（MemoryStore + W5 资料包 + 固定应答，无需数据库或模型密钥）与 HTTP 层。零配置启动演示服务并从网页触发一次生成到导出：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:create_app --factory --port 8000
```

使用配置文件时先复制 `.env.example` 为 `.env` 并填写，再用 `python -m uvicorn backend.app.main:create_app --factory --env-file .env --port 8000` 启动。测试依赖中已包含 `python-dotenv`；普通启动命令只读取进程环境变量，不自动加载 `.env`。

正式模式先按 010→011→012→020 应用 MySQL 迁移，再配置 `DATABASE_URL` 与 `MODEL_*`。推送后 GitHub Actions 会自动运行静态检查、契约一致性、契约测试、单元测试及 MySQL 关键集成测试（[.github/workflows/ci.yml](.github/workflows/ci.yml)）。

## 分支与 Pull Request

1. 在任务中写明负责人、需求编号、验收条件和交接对象。从最新 `main` 创建短期分支，例如依次执行 `git switch main`、`git pull --ff-only`、`git switch -c feature/knowledge-ingest`。
2. 在任务分支完成一个可验证的改动，运行契约检查及本模块相关测试。修改共享类型时，同时更新消费方、规范与样例，运行 `python scripts/export_contracts.py` 重新生成 Schema，再用 `--check` 复核。此处的 Python 应使用上一步的虚拟环境。
3. 以本人 GitHub 身份提交任务分支，向 `main` 发起 PR。说明关联需求、改动范围、测试结果及接口影响；页面改动可附截图。当前阶段在 PR 中记录本地检查结果，CI 接入后同时检查自动化结果。
4. 至少一位非作者审核。涉及共享接口、数据库迁移或公共任务状态时，请受影响模块负责人参与审核。修复意见并通过检查后再合并。

## doc/ 文件与归档位置

上方阅读表列出了开发使用的三份现行项目文档和接口规范。其余已有文件均在 [doc/项目文档/](doc/项目文档/) 下。下表中的链接保留各稿与配图原有的相对路径。

| 文件 | 内容与用途 |
|---|---|
| [课题.docx](doc/项目文档/课题.docx) | 课程课题原文，用于核对选题的定义、目标和核心开发内容。 |
| [课程报告与评分细则.md](doc/项目文档/课程报告与评分细则.md) | 提交节点、报告格式与篇幅、各阶段评分办法、个人贡献评价和填写模板。 |
| [方案书.md](doc/项目文档/方案书.md) | 项目初步定位、预期效果、技术路线、初始分工和成果设想。 |
| [计划书.md](doc/项目文档/计划书/计划书.md) · [计划书.pdf](doc/项目文档/计划书/计划书.pdf) | 早期计划书的可编辑稿与 PDF，用于回看范围和排期的演变。 |
| [计划书_实现路线.svg](doc/项目文档/计划书/计划书_实现路线.svg) | 早期计划书中的流程配图，说明报告生成步骤和模块关系。 |
| [开题报告.md](doc/项目文档/开题报告/开题报告.md) · [开题报告.pdf](doc/项目文档/开题报告/开题报告.pdf) | 开题阶段对课题、需求和整体实现路径的说明，分别为源稿和展示用 PDF。 |
| [g05_workflow.svg](doc/项目文档/开题报告/g05_workflow.svg) | 开题报告配套的工作流程图。 |
| 历史设计文档 [v1.0](doc/项目文档/历史设计文档/设计文档%20v1.0.md)、[v1.1](doc/项目文档/历史设计文档/设计文档%20v1.1.md)、[v1.2](doc/项目文档/历史设计文档/设计文档%20v1.2.md) | 依次归档初始模块设计、章节证据与版本规则修订、五类报告范围修订。开发时引用上方阅读表中的现行设计文档。 |

其余目录按用途预留。空目录中的 `.gitkeep` 让 Git 能保留目录结构。

| 目录（均位于 doc/ 下） | 后续归档内容 |
|---|---|
| `过程材料/` | 实际会议纪要、行动项跟进、会议纪要汇编与过程总结。 |
| `阶段提交/` | 按开题、中期、终期保存实际提交版本、展示材料及视频位置。 |
| `交付说明/` | 可运行版本的启动说明、使用说明及成果证据索引。 |
| `个人与评价/` | 个人材料与组内贡献评价的归档位置。仓库公开时，评分和含个人信息的材料通过课程指定渠道提交。 |
| `architecture/`、`decisions/` | 前者保存架构与流程图，后者记录关键选型、理由和影响。 |
| `contracts/` | 已有接口规范，后续归档接口变更说明。 |
| `testing/`、`deployment/` | 前者保存测试计划、执行与复测记录，后者保存部署环境和发布说明。 |
