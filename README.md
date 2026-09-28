# RAG-Knowledge · 团队 RAG 知识库后端

第一阶段是一个独立的 QA 知识库后端：录入 → 后台向量化 → ES 索引 → 混合召回。Python/FastAPI + MySQL + Elasticsearch + 硅基流动 Embedding。不生成聊天答案，后续智能体调用 `recall` 即可。

当前支持知识库、文档节点、批量 QA、更新删除、持久化索引任务、按知识库授权的 API Key、混合检索。**文档节点目前是 QA 容器，不是文件上传接口**；Markdown/PDF/Word 导入、正式管理页面和企业文档同步属于下一阶段。

## 快速启动

在本目录执行。Mac M3 使用 ARM64 镜像，无需在 macOS 单独安装 MySQL、ES 或 Java。

```sh
python3 scripts/init_env.py
# 在本地 .env 填写 EMBEDDING_API_KEY；已有 .env 不会被覆盖。
docker compose up -d --build
docker compose ps
```

Docker 不在 PATH 时，可使用 `/Applications/Docker.app/Contents/Resources/bin/docker` 替代 `docker`。

- 接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- MySQL：`127.0.0.1:3307`，数据库 `rag`，用户 `rag`，密码在 `.env`。
- ES：`127.0.0.1:9200`。
- 管理员凭据：`.env` 中的 `ADMIN_API_KEY`；Swagger 点 **Authorize** 后填写。不要把凭据贴进聊天或提交 Git。

`health.status=ok` 表示 MySQL、ES 可访问；`embedding_configured=true` 只表示配置了 Key，**不表示模型已通过实测**。

MySQL、ES 使用持久化卷。`docker compose stop` 停止服务，`docker compose up -d` 恢复。`docker compose down` 保留数据卷；**不要加 `-v`，否则会删除数据卷**。

当前配置仅用于本机开发：ES 未启用认证，端口绑定 `127.0.0.1`。上服务器前需要配置 TLS、网络隔离、备份、凭据管理及资源限制。不要直接改为公网监听。

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
| POST / GET | `/v1/knowledge_bases/{kb}/documents` | 创建 / 列出 QA 文档节点 |
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

推荐外部采集系统提供稳定的 `external_id`。同一文档内，重复发送相同 ID 和内容复用原记录；同 ID 不同内容返回 409，请调用 PUT 更新。未传 ID 时按内容生成摘要，只保证完全相同内容的重复请求幂等，不做语义去重。一批请求在同一个 MySQL 事务里保存，发生冲突整批回滚。

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

响应包含 `content`、QA/文档/知识库 ID、来源、版本、更新时间、最终分数和两路分数。`total` 是本次返回数量，不是全库命中总数。没有匹配返回空列表；模型或 ES 故障返回 503，不伪装成“没查到”。

## 混合召回的准确含义

- 文档和问题使用 `BAAI/bge-large-zh-v1.5`，1024 维；问题添加该模型适用的中文检索前缀。
- QA 的标准问法（未填时用原问题）与答案片段一起向量化；返回原问题和答案片段。
- ES 使用内置 `cjk` 分析器，中文二元词元与拉丁词项检索。它是基线方案，领域术语效果后续用业务数据评估。
- 两路先分别召回候选：`min(500, max(40, top_k*5))` 条；每路都应用知识库、文档、标签过滤。
- ES cosine 分数为 `(1 + cosine) / 2`，在 `[0,1]`；关键词 BM25 通过 `s/(s+8)` 映射到 `[0,1)`。
- 最终分数为 `向量权重 × 向量分数 + 关键词权重 × 映射后的关键词分数`。两权重必须相加为 1，缺失分支贡献 0。
- 默认 `0.8/0.2`，阈值在**融合后**应用。这是本项目明确定义的实现，并非已复刻对方平台的评分公式；参数 8 也是待业务评测的初始校准值。
- 分数不是正确率或置信度；初始阈值 0 不会主动拒绝语义不相关结果。需要用真实正例和无答案问题校准，不能直接把其他平台的 0.3 搬过来。
- 本阶段未启用 rerank。不会接受一个参数却悄悄忽略它；额外字段会返回 422。

该 BGE 模型上下文较短。第一版按 UTF-8 字节数保守限制单次输入不超过 480 字节，避免在线索引依赖下载 tokenizer。问法最多 80 字符，答案最多 12000 字符，长答案拆分；查询加前缀后超限返回 422。这个实现牺牲了一些片段长度，后续文档导入阶段应改为 tokenizer 感知的结构化切分。

修改 Embedding 模型或维度，需要换一个 `ES_INDEX` 并重新索引数据；启动时检查模型和索引元数据，不会静默混用不同模型。当前尚无批量迁移命令。

## 更新、删除和任务可靠性

- MySQL 是权威来源；ES 是可重建的检索副本。
- QA 写入和索引任务创建在一个事务里完成。Worker 默认每 2 秒扫描，可用任务行锁 `SKIP LOCKED` 支持多个进程。
- 每个任务对应一个版本；ES 片段 ID 由 `QA ID:版本:位置` 构成，部分写入后的重试不会重复创建同版本片段。
- 任务租约 15 分钟，超时可重新领取；失败退避重试，最多 5 次，之后可手动重试。只记录脱敏错误类别，不保存供应商响应原文。
- 修改后旧版本立即不再通过检索结果校验，新版索引完成前该 QA 暂时不可召回。删除也是先在 MySQL 失效，再异步清理 ES。
- 召回结果在返回前校验 MySQL 当前版本、有效状态和访问范围，避免索引滞后泄露已失效内容。
- 后台索引完成前再次检查版本；更新过程中旧任务结束，不会把旧版本重新发布。
- 过滤失效候选可能导致返回少于 top_k；需要保持索引任务健康。第一版未做持续扩展候选的复杂回补。

当前数据库使用初始化建表，尚未引入 Alembic 迁移。后续修改表结构需提供迁移；不要靠 `create_all` 更新已有表。

## 测试

```sh
.venv/bin/python -m pytest -q
# 使用真实开发 MySQL/ES，创建并清理专属测试知识库和索引；向量是测试夹具，不调用云模型。
.venv/bin/python scripts/integration_check.py
```

集成测试使用隔离索引，真实服务的 Worker 同时运行时可能竞争测试任务。建议执行前 `docker compose stop worker`，测试后 `docker compose up -d worker`。真实语义效果通过 `scripts/demo.py` 单独验证，不能用夹具测试代替。

源码主要在 `app/main.py`（接口）、`app/worker.py`（任务）、`app/search.py`（ES 与融合）、`app/embedding.py`（模型调用）。

首次交付的验证结果见 `VERIFICATION.md` 和 `VERIFICATION_RESULTS.json`。云模型真实调用与测试夹具的结果分开记录。

## 官方参考

- [硅基流动 Embeddings 接口](https://siliconflow.readme.io/reference/createembedding)
- [BGE 模型说明](https://huggingface.co/BAAI/bge-large-zh-v1.5)
- [ES 外部向量接入](https://www.elastic.co/docs/solutions/search/vector/bring-own-vectors)
- [ES 8.19 Docker 部署](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/docker.html)
