from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class KBCreate(Input):
    name: str = Field(min_length=1, max_length=200)


class DocumentCreate(Input):
    title: str = Field(min_length=1, max_length=200)
    source_uri: str = Field(default='', max_length=1000)


class QAContent(Input):
    question: str = Field(min_length=1, max_length=80)
    answer: str = Field(min_length=1, max_length=12000)
    stand_query: Optional[str] = Field(default=None, min_length=1, max_length=80)
    tags: List[str] = Field(default_factory=list, max_length=20)

    @field_validator('tags')
    @classmethod
    def valid_tags(cls, values):
        values = list(dict.fromkeys(x.strip() for x in values))
        if any(not x or len(x) > 64 for x in values):
            raise ValueError('tags must be nonempty and at most 64 characters')
        return values


class QACreate(QAContent):
    external_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class QABatch(Input):
    qa_pairs: List[QACreate] = Field(min_length=1, max_length=50)


class RetrievalOptions(Input):
    top_k: int = Field(default=8, ge=1, le=50)
    score_threshold: float = Field(default=0, ge=0, le=1)
    tags: List[str] = Field(default_factory=list, max_length=20)


class VectorWeight(Input):
    vector_weight: float = Field(default=0.8, ge=0, le=1)


class KeywordWeight(Input):
    # Matches the upstream API spelling supplied by the user.
    vector_weight: float = Field(default=0.2, ge=0, le=1)


class Weights(Input):
    vector_setting: VectorWeight = Field(default_factory=VectorWeight)
    keyword_setting: KeywordWeight = Field(default_factory=KeywordWeight)

    @model_validator(mode='after')
    def sum_to_one(self):
        if abs(self.vector_setting.vector_weight + self.keyword_setting.vector_weight - 1) > 1e-6:
            raise ValueError('vector and keyword weights must sum to 1')
        return self


class RecallRequest(Input):
    knowledge_base_ids: List[str] = Field(min_length=1, max_length=20)
    document_ids: List[str] = Field(default_factory=list, max_length=100)
    query: str = Field(min_length=1, max_length=300)
    retrieval_options: RetrievalOptions = Field(default_factory=RetrievalOptions)
    weights: Weights = Field(default_factory=Weights)


class KeyCreate(Input):
    name: str = Field(min_length=1, max_length=100)
    knowledge_base_ids: List[str] = Field(min_length=1, max_length=20)
    can_write: bool = False
