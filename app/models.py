# PostgreSQL 权威数据模型：知识库 → 文档节点 → QA；索引任务绑定 QA 的具体版本。
# 检索片段 仅保存检索副本。版本、删除状态和授权最终以这些表为准。

from datetime import datetime
from uuid import uuid4
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


# 为新增实体生成 UUID 字符串；作为 Column 默认工厂，在插入时调用。
def uid():
    return str(uuid4())


# 统一以不含时区对象的 UTC 时间写入数据库，API/检索索引 展示时需按 UTC 解释。
def utcnow():
    return datetime.utcnow()


# 知识库是 API Key 授权和召回过滤的基本边界。
class KnowledgeBase(Base):
    __tablename__ = 'knowledge_bases'
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


# 文档节点保存公共标题与来源；上传文件的版本和磁盘路径由 document_files 扩展表管理。
# 软删除后节点及其 QA 不再通过召回校验，索引清理由后台继续完成。
class Document(Base):
    __tablename__ = 'documents'
    id = Column(String(36), primary_key=True, default=uid)
    knowledge_base_id = Column(String(36), ForeignKey('knowledge_bases.id'), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    source_uri = Column(String(1000), default='', nullable=False)
    deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


# 保存问答原文及其当前版本。相同 document_id 下 external_id 唯一，用于重复提交保护。
# version 是内容版本，indexed_version 是已完成索引版本；只有两者相等才允许返回该 QA。
class QAPair(Base):
    __tablename__ = 'qa_pairs'
    # 数据库唯一约束是重复写入的最后一道防线，接口查询本身不能替代并发约束。
    __table_args__ = (UniqueConstraint('document_id', 'external_id'),)
    id = Column(String(36), primary_key=True, default=uid)
    document_id = Column(String(36), ForeignKey('documents.id'), nullable=False, index=True)
    # 上游业务记录 ID；缺省时接口根据内容生成摘要，不能用它判断语义近似。
    external_id = Column(String(128), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    # 标准问法用于向量输入；原 question 保留给用户核对，二者可以不同。
    stand_query = Column(Text, nullable=False)
    tags = Column(JSON, nullable=False, default=list)
    version = Column(Integer, default=1, nullable=False)
    # 初始 0 表示未发布任何索引版本，仅由成功完成任务的 Worker 推进。
    indexed_version = Column(Integer, default=0, nullable=False)
    deleted = Column(Boolean, default=False, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# 持久化索引任务，一个 QA 版本最多一条。状态为 pending/running/done/failed/superseded。
# 删除也通过版本任务执行；Worker 根据 QA 当前的 deleted 标志选择清理索引。
class IndexTask(Base):
    __tablename__ = 'index_tasks'
    # 同版本任务不重复创建；队列索引加速按状态和下次执行时间扫描。
    __table_args__ = (UniqueConstraint('qa_id', 'version'), Index('ix_tasks_queue', 'status', 'next_attempt_at'))
    id = Column(String(36), primary_key=True, default=uid)
    qa_id = Column(String(36), ForeignKey('qa_pairs.id'), nullable=False)
    version = Column(Integer, nullable=False)
    status = Column(String(20), default='pending', nullable=False)
    # 记录领取次数，包含租约超时后的接管次数；达到上限后停止自动重试。
    attempts = Column(Integer, default=0, nullable=False)
    next_attempt_at = Column(DateTime, default=utcnow, nullable=False)
    lease_until = Column(DateTime, nullable=True)
    # 每次领取的新 UUID 是任务所有权标识，旧 Worker 恢复后不得沿用旧 token 提交。
    lease_token = Column(String(36), nullable=True)
    # 仅保存脱敏错误摘要，禁止保存模型完整响应、QA 正文或凭据。
    last_error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# 普通调用凭据：仅存 SHA-256 摘要、允许的知识库及写权限。
# 管理员环境变量 Key 不在此表；撤销普通 Key 只设置 active=False。
class APIKey(Base):
    __tablename__ = 'api_keys'
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(100), nullable=False)
    # 随机明文 Key 只在创建响应中返回一次；数据库泄露不能直接获得可用 Key。
    key_hash = Column(String(64), nullable=False, unique=True)
    # 授权单位是知识库；不提供用户级身份继承，多个用户共用 Key 时范围相同。
    knowledge_base_ids = Column(JSON, nullable=False)
    can_write = Column(Boolean, default=False, nullable=False)
    active = Column(Boolean, default=True, nullable=False)


# 上传文件独立扩展表；不改旧 documents/qa_pairs 表，升级时保留既有 QA。
# 原文件保存在服务端生成的路径，用户文件名仅用于展示和下载响应。
class DocumentFile(Base):
    __tablename__ = 'document_files'
    document_id = Column(String(36), ForeignKey('documents.id'), primary_key=True)
    filename = Column(String(255), nullable=False)
    storage_path = Column(String(255), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    tags = Column(JSON, nullable=False, default=list)
    version = Column(Integer, default=1, nullable=False)
    indexed_version = Column(Integer, default=0, nullable=False)
    chunk_count = Column(Integer, default=0, nullable=False)
    warnings = Column(JSON, nullable=False, default=list)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# 文件任务使用单独队列，避免给旧 QA 任务改外键或破坏已有任务。
class FileIndexTask(Base):
    __tablename__ = 'file_index_tasks'
    __table_args__ = (UniqueConstraint('document_id', 'version'), Index('ix_file_tasks_queue', 'status', 'next_attempt_at'))
    id = Column(String(36), primary_key=True, default=uid)
    document_id = Column(String(36), ForeignKey('documents.id'), nullable=False)
    version = Column(Integer, nullable=False)
    status = Column(String(20), default='pending', nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    next_attempt_at = Column(DateTime, default=utcnow, nullable=False)
    lease_until = Column(DateTime, nullable=True)
    lease_token = Column(String(36), nullable=True)
    last_error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
