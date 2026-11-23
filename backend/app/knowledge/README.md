# W1 资料受理与历史版本

## 对接

先在项目 MySQL 8 数据库执行 `backend/migrations/010_knowledge_ingest.sql`，安装本目录 `requirements.txt` 及项目契约依赖。与后端共用数据库配置。给仓储传入返回独立 PyMySQL 连接的工厂，使用默认元组游标、utf8mb4 字符集。仓储负责关闭连接和管理登记事务。

```python
repository = DocumentRepository(connect)
ingest = DocumentIngest(OriginalStorage(runtime_root), repository)
accepted = ingest.ingest_document(upload_path, metadata)
record = repository.get_version(accepted.document_id, accepted.document_version_id)
```

上传适配器在唯一临时目录保留经过校验的原始 basename，鉴权、请求体限额和 HTTP 路由由后端提供。将 `UNSUPPORTED_FILE_TYPE` 映射为 415、`FILE_TOO_LARGE` 映射为 413。用 `get_version` 按两级 ID 读取轮询状态，用 `get_latest` 读取文献详情的最新登记版本。重复上传沿用原版本和首次元数据，返回当前状态。

按内容寻址的原件放在运行目录 `knowledge/originals`，部署时使用支持硬链接的本地卷（例如 NTFS）。格式检查是上传阶段的签名、容器或 CSV 基础检查。解析完整性由后续 Worker 验证，受理成功时版本为 QUEUED。事务失败时会清掉本次暂存文件，已发布的内容对象予以保留供并发请求或重试复用。

## 复验

从仓库根按 README 准备 Python 环境，安装本目录的 PyMySQL 依赖。使用单独的本机 MySQL 测试实例，测试账户需能创建并删除临时测试库，以及在其中创建表和触发器。如果实例配置了密码，通过 `W1_MYSQL_PASSWORD` 环境变量提供。

```powershell
$env:W1_MYSQL_PORT = "<本地测试实例端口>"
$env:W1_MYSQL_USER = "<测试账户>"
python -m unittest discover -s backend/tests/integration -v
python -m unittest discover -s backend/tests/unit -v
python -m unittest discover -s backend/tests/contract -v
python scripts/export_contracts.py --check
```

测试用并发连接复验全库哈希唯一及同名修订、检查新建仓储实例能否读取原版本，并通过 MySQL 触发器制造插入失败，验证文献、文件名和版本登记均回滚。控制台的 duplicate/revision 行依次显示文献 ID、初版 ID、异名重复 ID 和修订 ID。
