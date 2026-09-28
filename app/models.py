from datetime import datetime
from uuid import uuid4
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def uid():
    return str(uuid4())


def utcnow():
    return datetime.utcnow()


class KnowledgeBase(Base):
    __tablename__ = 'knowledge_bases'
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class Document(Base):
    __tablename__ = 'documents'
    id = Column(String(36), primary_key=True, default=uid)
    knowledge_base_id = Column(String(36), ForeignKey('knowledge_bases.id'), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    source_uri = Column(String(1000), default='', nullable=False)
    deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class QAPair(Base):
    __tablename__ = 'qa_pairs'
    __table_args__ = (UniqueConstraint('document_id', 'external_id'),)
    id = Column(String(36), primary_key=True, default=uid)
    document_id = Column(String(36), ForeignKey('documents.id'), nullable=False, index=True)
    external_id = Column(String(128), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    stand_query = Column(Text, nullable=False)
    tags = Column(JSON, nullable=False, default=list)
    version = Column(Integer, default=1, nullable=False)
    indexed_version = Column(Integer, default=0, nullable=False)
    deleted = Column(Boolean, default=False, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class IndexTask(Base):
    __tablename__ = 'index_tasks'
    __table_args__ = (UniqueConstraint('qa_id', 'version'), Index('ix_tasks_queue', 'status', 'next_attempt_at'))
    id = Column(String(36), primary_key=True, default=uid)
    qa_id = Column(String(36), ForeignKey('qa_pairs.id'), nullable=False)
    version = Column(Integer, nullable=False)
    status = Column(String(20), default='pending', nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    next_attempt_at = Column(DateTime, default=utcnow, nullable=False)
    lease_until = Column(DateTime, nullable=True)
    lease_token = Column(String(36), nullable=True)
    last_error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class APIKey(Base):
    __tablename__ = 'api_keys'
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(100), nullable=False)
    key_hash = Column(String(64), nullable=False, unique=True)
    knowledge_base_ids = Column(JSON, nullable=False)
    can_write = Column(Boolean, default=False, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
