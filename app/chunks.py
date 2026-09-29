# 将一条 QA 按模型 token 预算拆成片段，同时携带来源和版本信息。
from .tokenization import text_chunks


# 把标准问法与每段答案组合用于向量化，把原问法与答案组合用于展示。
# 片段 ID 包含 QA ID、版本和位置，使同版本重试写入覆盖同一 检索片段 记录。
def qa_chunks(qa, document, settings):
    # 每个答案片段都附带标准问法，使单独召回某个片段时仍有问题语义。
    prefix = '问：' + qa.stand_query + '\n答：'
    result = []
    for position, answer in enumerate(text_chunks(qa.answer, prefix=prefix)):
        result.append({
            'id': f'{qa.id}:{qa.version}:{position}',
            'qa_id': qa.id,
            'knowledge_base_id': document.knowledge_base_id,
            'document_id': document.id,
            'version': qa.version,
            'title': document.title,
            'content': '问：' + qa.question + '\n答：' + answer,
            # 只在调用模型时使用此字段；写入 检索片段 前会移除，避免存两份近似正文。
            'embedding_text': prefix + answer,
            'question': qa.question,
            'stand_query': qa.stand_query,
            'tags': qa.tags,
            'source_uri': document.source_uri,
            'source_location': f'QA {qa.id}, 片段 {position + 1}',
            # 数据库统一存 UTC 无时区时间，追加 Z 后按 ISO 8601 UTC 时间写入 检索索引。
            'updated_at': qa.updated_at.isoformat() + 'Z',
            'embedding_model': settings.embedding_model,
        })
    return result
