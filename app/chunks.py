# 将一条 QA 拆成可向量化的片段，同时携带来源和版本信息。
# 当前按 UTF-8 字节预算保守切分，不是 tokenizer 切分，也不负责 PDF 等文件解析。

# 按字符边界累计 UTF-8 字节数，输出不超过 budget 的文本片段。
# 不会从多字节字符中间截断，也不会丢弃空格或换行；调用方必须提供足够容纳单字符的预算。
def byte_chunks(text, budget):
    """Lossless UTF-8-aware splitting; no model downloads required for the QA MVP."""
    part, size = [], 0
    for char in text:
        width = len(char.encode('utf-8'))
        if size + width > budget:
            yield ''.join(part)
            part, size = [], 0
        part.append(char)
        size += width
    if part:
        yield ''.join(part)


# 把标准问法与每段答案组合用于向量化，把原问法与答案组合用于展示。
# 片段 ID 包含 QA ID、版本和位置，使同版本重试写入覆盖同一 ES 记录。
def qa_chunks(qa, document, settings):
    # 每个答案片段都附带标准问法，使单独召回某个片段时仍有问题语义。
    prefix = '问：' + qa.stand_query + '\n答：'
    # 480 是保守的输入字节上限，先扣掉问法和格式字符，再分配剩余预算给答案。
    budget = 480 - len(prefix.encode('utf-8'))
    if budget < 32:
        raise ValueError('QA question too long for embedding budget')
    result = []
    for position, answer in enumerate(byte_chunks(qa.answer, budget)):
        result.append({
            'id': f'{qa.id}:{qa.version}:{position}',
            'qa_id': qa.id,
            'knowledge_base_id': document.knowledge_base_id,
            'document_id': document.id,
            'version': qa.version,
            'title': document.title,
            'content': '问：' + qa.question + '\n答：' + answer,
            # 只在调用模型时使用此字段；写入 ES 前会移除，避免存两份近似正文。
            'embedding_text': prefix + answer,
            'question': qa.question,
            'stand_query': qa.stand_query,
            'tags': qa.tags,
            'source_uri': document.source_uri,
            'source_location': f'QA {qa.id}, 片段 {position + 1}',
            # 数据库统一存 UTC 无时区时间，追加 Z 后按 ISO 8601 UTC 时间写入 ES。
            'updated_at': qa.updated_at.isoformat() + 'Z',
            'embedding_model': settings.embedding_model,
        })
    return result
