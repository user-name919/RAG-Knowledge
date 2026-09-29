# RAG-Knowledge · 团队 RAG 知识库后端

一个可以独立运行的团队知识库：QA 或文件入库 → 后台解析与向量化 → PostgreSQL 混合召回。Python/FastAPI + PostgreSQL + pgvector + 硅基流动 Embedding，配套 Vue 3 上传与检索 Demo。后续智能体可直接调用 `recall`，本服务返回原文，不生成聊天答案。

目前支持 Markdown、UTF-8 TXT、文本型 PDF 上传、替换、下载和删除，原生 QA、任务重试、知识库权限以及默认向量 0.8 + 关键词 0.2 的检索。

## 使用上传页面

启动后打开 <http://localhost:8000>，填写本地 `.env` 的 `ADMIN_API_KEY`（不是 Embedding Key）。创建或选择知识库，上传文件，等待状态变成“可检索”，然后进入“检索测试”输入问题。页面支持来源范围、top_k 和阈值设置，并显示命中原文、章节/PDF 页码及分数。

- 文件最大 10 MB；PDF 最多 300 页；正文最多 100 万字符、1000 个片段。
- Markdown 按标题组织；PDF 按页解析；长段落按模型实际 token 数切分，保留原文。
- 扫描件需要先做 OCR；加密 PDF、Word/Excel 暂不支持。部分 PDF 页面无文字时会显示警告。
- 替换文件后旧版本立即退出召回，等待新版完成索引；完全相同的替换不会重复向量化。
- 原文件保存到 `upload_data` 卷，元数据、任务、片段与向量统一保存到 PostgreSQL。Embedding 会将文档片段发送到你配置的模型服务。
- 浏览器 Key 只保留在内存，刷新后需要重新连接；只读 Key 可以检索和下载授权资料，不能上传。
- 前端源码位于 `frontend/`。开发时执行 `npm ci && npm run dev`，Vite 将 API 请求代理到本机 8000 端口；Docker 构建时自动打包前端，由 FastAPI 同源提供。

## 快速启动

在本目录执行。Mac M3 使用 ARM64 镜像，无需在 macOS 单独安装 PostgreSQL 或 Java。

```sh
python3 scripts/init_env.py
# 在本地 .env 填写 EMBEDDING_API_KEY；已有 .env 不会被覆盖。
docker compose up -d --build
docker compose ps
```

Docker 不在 PATH 时，可使用 `/Applications/Docker.app/Contents/Resources/bin/docker` 替代 `docker`。

- 接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- PostgreSQL：`127.0.0.1:5433`，数据库 `rag`，用户 `rag`，密码在 `.env`。
- 管理员凭据：`.env` 中的 `ADMIN_API_KEY`；Swagger 点 **Authorize** 后填写。不要把凭据贴进聊天或提交 Git。

`health.status=ok` 表示 PostgreSQL 与 pgvector 可访问；`embedding_configured=true` 只表示配置了 Key，**不表示模型已通过实测**。

PostgreSQL 与 pgvector 和上传原文件使用持久化卷。`docker compose stop` 停止服务，`docker compose up -d` 恢复。`docker compose down` 保留数据卷；**不要加 `-v`，否则会删除数据卷**。

当前配置仅用于本机开发：使用本机开发账号，端口绑定 `127.0.0.1`。上服务器前需要配置 TLS、网络隔离、备份、凭据管理及资源限制。不要直接改为公网监听。

## 运行虚构 QA 演示

示例均为虚构，不代表实际产品规则。运行会向配置的 Embedding 服务发送示例文本和查询，产生少量模型调用。

宿主机需要 Python 3.9+；容器使用 Python 3.12。

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/demo.py
.venv/bin/python scripts/demo.py --query '发布失败显示 TASK_403'
.venv/bin/python scripts/evaluate.py
```

脚本从 `.env` 读取管理员 Key，不打印凭据；首次建立演示知识库，之后复用 `data/demo.json` 中的 ID。填入或更改 Embedding 配置后运行：

```sh
docker compose up -d --force-recreate api worker
```

## 主要接口

所有 `/v1` 接口使用 `api-key` 请求头。`/health` 和接口文档不需要认证。

| 方法 | 路径 | 行为 |
|---|---|---|
| POST / GET | `/v1/knowledge_bases` | 创建 / 列出知识库；创建需要管理员 |
| POST / GET | `/v1/knowledge_bases/{kb}/documents` | 创建 QA 节点 / 列出所有文档 |
| POST | `/v1/knowledge_bases/{kb}/documents/upload` | multipart 上传 file、title、tags，返回 202 |
| PUT / GET | `/v1/knowledge_bases/{kb}/documents/{doc}/file` | 替换 / 下载原文件 |
| POST | `/v1/knowledge_bases/{kb}/documents/{doc}/qa_pairs/batch_create` | 批量保存最多 50 条 QA，返回 202 |
| GET | `/v1/knowledge_bases/{kb}/documents/{doc}/qa_pairs` | 查看 QA 原文及版本 |
| PUT / DELETE | `/v1/knowledge_bases/{kb}/documents/{doc}/qa_pairs/{qa}` | 整体更新 / 删除 QA |
| DELETE | `/v1/knowledge_bases/{kb}/documents/{doc}` | 删除文档节点及所含 QA |
| GET | `/v1/knowledge_bases/{kb}/documents/{doc}/index_status` | 查看当前版本索引状态 |
| POST | `/v1/knowledge_bases/{kb}/documents/{doc}/retry_failed` | 重置当前版本失败任务 |
| POST | `/v1/knowledge_bases/recall` | 混合召回原文片段 |
| POST / DELETE | `/v1/api_keys` / `/v1/api_keys/{id}` | 管理员创建 / 撤销限定知识库的 Key |

列表接口提供 `offset`、`limit` 分页。`index_status` 默认最多 100 条，可分页查看。

新增 QA 示例：

```json
{
  "qa_pairs": [{
    "external_id": "source-record-123",
    "question": "学生看不到作业怎么办？",
    "answer": "检查作业是否发布以及目标班级是否正确。",
    "stand_query": "老师布置的题学生无法查看如何排查？",
    "tags": ["老师", "作业"]
  }]
}
```

推荐外部采集系统提供稳定的 `external_id`。同一文档内，重复发送相同 ID 和内容复用原记录；同 ID 不同内容返回 409，请调用 PUT 更新。未传 ID 时按内容生成摘要，只保证完全相同内容的重复请求幂等，不做语义去重。一批请求在同一个 PostgreSQL 事务里保存，发生冲突整批回滚。

`202` 表示原文和任务已保存，不表示立即可检索。轮询 `index_status`，当前版本为 `ready` 才算完成。状态可能为 `pending/running/failed/ready/deleted`。

召回示例：

```json
{
  "knowledge_base_ids": ["知识库ID"],
  "document_ids": [],
  "query": "题布置下去了，孩子那边怎么没显示？",
  "retrieval_options": {"top_k": 8, "score_threshold": 0, "tags": []},
  "weights": {
    "vector_setting": {"vector_weight": 0.8},
    "keyword_setting": {"vector_weight": 0.2}
  }
}
```

`document_ids=[]` 表示搜索指定知识库下的全部有效文档。`tags` 为全部匹配（AND）。权限根据 API Key 的知识库授权在服务端校验，不把标签当权限。默认应用 Key 只读；管理员创建时可授予写入权限。多个教师共用一个 Key 时权限范围也相同，未来 Agent 应按实际用户权限选择/限定可查询范围。

响应包含 `content`、QA/文档/知识库 ID、来源、版本、更新时间、最终分数和两路分数。`total` 是本次返回数量，不是全库命中总数。没有匹配返回空列表；模型或 PostgreSQL 故障返回 503，不伪装成“没查到”。

## 混合召回的准确含义

- 文档和问题使用 `BAAI/bge-large-zh-v1.5`，1024 维；问题添加该模型适用的中文检索前缀。
- QA 的标准问法（未填时用原问题）与答案片段一起向量化；返回原问题和答案片段。
- 中文采用重叠双字词元；英文、错误码和路径保留完整词项，经编码后写入 PostgreSQL `tsvector`，使用 GIN 索引。不是语义分词，领域术语仍需评测。
- 两路先分别召回候选：`min(500, max(40, top_k*5))` 条；每路都应用知识库、文档、标签过滤。
- pgvector 使用余弦距离与 HNSW 索引，向量分数为 `(1 + cosine) / 2`。关键词使用 `ts_rank_cd(...,32)`，即 `rank/(rank+1)`，不是 BM25；问题/标题/正文的字段权重比例为 3:2:1。
- 最终分数为 `向量权重 × 向量分数 + 关键词权重 × 映射后的关键词分数`。两权重必须相加为 1，缺失分支贡献 0。
- 默认 `0.8/0.2`，阈值在**融合后**应用。这是本项目明确定义的实现，并非已复刻对方平台的评分公式。迁移后关键词评分发生变化，阈值需重新评测。
- 分数不是正确率或置信度；初始阈值 0 不会主动拒绝语义不相关结果。需要用真实正例和无答案问题校准，不能直接把其他平台的 0.3 搬过来。
- 本阶段未启用 rerank。不会接受一个参数却悄悄忽略它；额外字段会返回 422。

该 BGE 模型限制 512 tokens。项目内置其 tokenizer，QA 与文档片段包含标题/问法后控制在 480 tokens 内；查询加检索前缀后超 512 tokens 返回 422。tokenizer 版本与许可见 `app/assets/README.md`。切换模型时还必须同步更换 tokenizer 与长度规则。

`RETRIEVAL_COLLECTION` 绑定模型与维度。相同维度换模型需要新集合并重新索引；更换维度需要显式修改数据库向量列/索引。启动会阻止模型或维度不一致，不静默混用向量。当前内置 BGE tokenizer，换模型还需要替换 tokenizer 与长度规则。

## 更新、删除和任务可靠性

- PostgreSQL 保存业务表及 `knowledge_chunks` 检索片段；`retrieval_metadata` 记录模型、维度与集合版本。
- QA 写入和索引任务创建在一个事务里完成。Worker 默认每 2 秒扫描，可用任务行锁 `SKIP LOCKED` 支持多个进程。
- 每个任务对应一个版本；片段 ID 由 `QA ID:版本:位置` 构成，部分写入后的重试不会重复创建同版本片段。
- 任务租约 15 分钟，超时可重新领取；失败退避重试，最多 5 次，之后可手动重试。只记录脱敏错误类别，不保存供应商响应原文。
- 修改后旧版本立即不再通过检索结果校验，新版索引完成前该 QA 暂时不可召回。删除也是先在 PostgreSQL 失效，再异步清理片段。
- 召回结果在返回前校验 PostgreSQL 当前版本、有效状态和访问范围，避免索引滞后泄露已失效内容。
- 后台索引完成前再次检查版本；更新过程中旧任务结束，不会把旧版本重新发布。
- 两路查询在截断候选前检查已发布版本与权限范围；API 返回前再次校验。HNSW 使用迭代扫描，但近似检索仍可能漏召回，不能保证一定补足 top_k。
- 旧片段清理与版本发布在同一 PostgreSQL 事务中执行；Embedding 调用和新片段暂存不占用最终发布事务。

当前数据库使用初始化建表，尚未引入 Alembic 迁移。后续修改表结构需提供迁移；不要靠 `create_all` 更新已有表。

## 测试

```sh
.venv/bin/python -m pytest -q
# 使用真实开发 PostgreSQL/pgvector，创建并清理专属测试知识库和索引；向量是测试夹具，不调用云模型。
.venv/bin/python scripts/integration_check.py
```

集成测试使用隔离索引，真实服务的 Worker 同时运行时可能竞争测试任务。建议执行前 `docker compose stop worker`，测试后 `docker compose up -d worker`。真实语义效果通过 `scripts/demo.py` 单独验证，不能用夹具测试代替。

源码主要在 `app/main.py`（接口）、`app/worker.py`（任务）、`app/search.py`（pgvector、全文索引与融合）、`app/embedding.py`（模型调用）。

本次迁移和回退说明见 `MIGRATION_POSTGRES.md`。旧版文件上传与 Vue Demo 的验证结果见 `VERIFICATION_DOCUMENTS.md`。首次交付的验证结果见 `VERIFICATION.md` 和 `VERIFICATION_RESULTS.json`。云模型真实调用与测试夹具的结果分开记录。

## 官方参考

- [硅基流动 Embeddings 接口](https://siliconflow.readme.io/reference/createembedding)
- [BGE 模型说明](https://huggingface.co/BAAI/bge-large-zh-v1.5)
- [pgvector 官方说明](https://github.com/pgvector/pgvector)
- [PostgreSQL 全文检索评分](https://www.postgresql.org/docs/17/textsearch-controls.html)
