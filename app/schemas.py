# Pydantic 请求结构及输入校验。这里的约束会同步显示在 OpenAPI 文档中。
# 字段名部分参考既有知识库接口，但检索分数及任务状态按本项目自己的定义执行。

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# 所有输入模型的共同约束：去除字符串首尾空白，拒绝未知字段，避免拼错参数却被静默忽略。
class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


# 创建知识库只需要非空名称，知识库 ID 由后端生成。
class KBCreate(Input):
    name: str = Field(min_length=1, max_length=200)


# 创建 QA 文档节点的元数据；source_uri 只记录来源，不触发网络抓取。
class DocumentCreate(Input):
    title: str = Field(min_length=1, max_length=200)
    source_uri: str = Field(default='', max_length=1000)


# 新增和整体更新共享的正文格式。标准问法缺省时由接口回退到原问题。
# 问题长度控制在短向量模型的预算内，长答案由后台切分；标签是检索条件，不是授权规则。
class QAContent(Input):
    question: str = Field(min_length=1, max_length=80)
    answer: str = Field(min_length=1, max_length=12000)
    stand_query: Optional[str] = Field(default=None, min_length=1, max_length=80)
    tags: List[str] = Field(default_factory=list, max_length=20)

    # 去掉标签首尾空白并按首次出现顺序去重，同时拒绝空标签和过长标签。
    @field_validator('tags')
    @classmethod
    def valid_tags(cls, values):
        values = list(dict.fromkeys(x.strip() for x in values))
        if any(not x or len(x) > 64 for x in values):
            raise ValueError('tags must be nonempty and at most 64 characters')
        return values


# 新增时可提供外部业务 ID；后续改内容应通过更新接口，而不是以同一外部 ID 重复新增。
class QACreate(QAContent):
    external_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


# 限制单次最多 50 条，控制请求和事务大小；所有记录在同一个数据库事务中处理。
class QABatch(Input):
    qa_pairs: List[QACreate] = Field(min_length=1, max_length=50)


# 控制最终条数、融合后阈值和标签过滤。阈值默认为 0，尚不提供自动拒答能力。
class RetrievalOptions(Input):
    top_k: int = Field(default=8, ge=1, le=50)
    score_threshold: float = Field(default=0, ge=0, le=1)
    tags: List[str] = Field(default_factory=list, max_length=20)


# 语义向量分支的权重，默认 0.8。
class VectorWeight(Input):
    vector_weight: float = Field(default=0.8, ge=0, le=1)


# 关键词分支默认 0.2；字段仍叫 vector_weight 是为保持参考接口的嵌套字段形式。
class KeywordWeight(Input):
    # Matches the upstream API spelling supplied by the user.
    vector_weight: float = Field(default=0.2, ge=0, le=1)


# 两个分支的权重容器；子配置使用工厂创建，避免可变默认对象在请求间共享。
class Weights(Input):
    vector_setting: VectorWeight = Field(default_factory=VectorWeight)
    keyword_setting: KeywordWeight = Field(default_factory=KeywordWeight)

    # 要求两路权重之和约等于 1，允许浮点数计算带来的微小误差。
    @model_validator(mode='after')
    def sum_to_one(self):
        if abs(self.vector_setting.vector_weight + self.keyword_setting.vector_weight - 1) > 1e-6:
            raise ValueError('vector and keyword weights must sum to 1')
        return self


# 召回输入：知识库必填；空 document_ids 表示不进一步限制文档。
# query 的字符上限只是第一层校验，接口还会检查加前缀后的 模型 token 预算。
class RecallRequest(Input):
    knowledge_base_ids: List[str] = Field(min_length=1, max_length=20)
    document_ids: List[str] = Field(default_factory=list, max_length=100)
    query: str = Field(min_length=1, max_length=2000)
    retrieval_options: RetrievalOptions = Field(default_factory=RetrievalOptions)
    weights: Weights = Field(default_factory=Weights)


# 管理员创建普通 Key 的授权描述。默认只读，can_write 必须明确开启。
class KeyCreate(Input):
    name: str = Field(min_length=1, max_length=100)
    knowledge_base_ids: List[str] = Field(min_length=1, max_length=20)
    can_write: bool = False
