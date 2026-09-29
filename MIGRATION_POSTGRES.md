# PostgreSQL + pgvector 迁移记录

## 当前架构

- PostgreSQL 17 + pgvector 0.8.2，Docker 服务 `postgres`，本机端口 `5433`。
- 业务表、任务表保留原结构；新增 `knowledge_chunks` 保存片段、向量、标签和全文索引，`retrieval_metadata` 保存集合模型配置。
- 向量使用 HNSW 余弦索引；关键词使用中文双字词元、完整英文/错误码词项、GIN 全文索引。
- 两路默认权重仍为 0.8/0.2。向量分为 `(1+cosine)/2`，关键词改为 `ts_rank_cd(...,32)`；问题/标题/正文权重比例 3:2:1。该关键词分不是 ES BM25，旧阈值需要重新评测。
- Worker 完成模型调用和片段暂存后，锁定业务版本；旧片段清理与版本发布在同一 PostgreSQL 事务中提交。查询在 LIMIT 之前筛选已发布版本和范围，返回前再次校验。
- 原文件继续使用 `team-rag_upload_data`；新数据库使用 `team-rag_postgres_data`。

## 本机数据迁移

迁移前停止 API/Worker 写入，导出旧库并备份文件卷。导入保留了 1 个知识库、1 个文档节点、3 条 QA、3 个任务和 3 个向量片段；无上传文件记录或普通 API Key。全部业务字段及片段内容逐项比较一致，向量误差小于 1e-6，没有重新调用 Embedding。

本机私有文件均在被 Git 忽略的目录：

- `.env`：新 PostgreSQL 连接配置，原管理员和 Embedding Key 保留。
- `data/migration-backup/.env`：旧配置。
- `data/migration-backup/snapshot.json`：旧业务数据与完整向量。
- `data/migration-backup/uploads.tar.gz`：原文件备份。
- `data/migration-backup/compose.mysql-es.yaml`：回退配置，使用保留镜像 `team-rag-local:mysql-es-backup`。

这些文件不可提交到 Git。旧 MySQL/ES 容器停止后保留，旧数据卷不删除。

## 可复用迁移工具

旧系统停写后执行 `scripts/migrate_mysql_es.py export <快照路径> --source-env <旧env路径>`；旧库驱动另见 `scripts/requirements-migration.txt`。

目标 `.env` 指向新 PostgreSQL，启动数据库后执行 `scripts/migrate_mysql_es.py import <快照路径>`。仅允许向空目标导入；数据插入在一个事务中完成，失败全部回滚。模型和维度不匹配时拒绝导入。工具不包含原文件搬迁，跨机器需要另外恢复文件卷。

## 本机回退

先停止新版 API/Worker，再执行：

```sh
docker compose stop api worker
docker compose --project-directory data/migration-backup -f data/migration-backup/compose.mysql-es.yaml up -d --no-build mysql elasticsearch api worker
```

回退恢复的是迁移时的旧数据库。迁移后新写入的 PostgreSQL 数据不会自动同步回 MySQL，存在新写入时应先另行备份和规划回迁。不要执行 `down -v` 或删除任意数据卷。

## 验证范围

- 30 项自动化测试通过。
- 真实 PostgreSQL 集成：并发任务领取、幂等索引、中文关键词、错误码精确匹配、权限过滤、版本更新/删除及事务回滚通过。
- 真实 Embedding 文件链路：Markdown、TXT、PDF 上传/召回/下载，PDF 页码、版本替换、删除、扫描件失败及只读权限通过。
- 真实语义小样本 7/7 命中预期 QA。初次迁移默认字段权重下为 6/7，统一为 3:2:1 后恢复；没有修改向量或评测问题。默认阈值 0 仍会对无答案问题返回候选，不能当作自动拒答。
- 真实语义小样本结果保存在本机 `data/pg-evaluation.json`，文件链路结果在 `data/pg-document-results.json`。仅使用虚构资料，不能作为业务准确率承诺。
- 前端 API 路径与认证方式保持不变；本次未新增浏览器视觉验收。
