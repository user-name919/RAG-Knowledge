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


def qa_chunks(qa, document, settings):
    prefix = '问：' + qa.stand_query + '\n答：'
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
            'embedding_text': prefix + answer,
            'question': qa.question,
            'stand_query': qa.stand_query,
            'tags': qa.tags,
            'source_uri': document.source_uri,
            'source_location': f'QA {qa.id}, 片段 {position + 1}',
            'updated_at': qa.updated_at.isoformat() + 'Z',
            'embedding_model': settings.embedding_model,
        })
    return result
