# 分词规则的边界测试：防止 SQL 查询语法、错误码拆分和中文匹配退化。
from app.search import keyword_tokens, fuse


def test_chinese_bigrams_and_exact_machine_terms():
    decode = lambda value: [bytes.fromhex(t[1:]).decode() for t in keyword_tokens(value)]
    assert decode('学生作业') == ['学生', '生作', '作业']
    assert decode('TASK_403 task_404 /v1/jobs') == ['task_403', 'task_404', 'v1/jobs']
    assert set(keyword_tokens('TASK_403')).isdisjoint(keyword_tokens('TASK_404'))
    assert set(keyword_tokens('作业')).issubset(keyword_tokens('学生作业不可见'))
    assert all(t.isalnum() for t in keyword_tokens("') | ! : * TASK_403"))
    assert keyword_tokens('?!') == []


def test_fusion_uses_normalized_postgres_rank():
    source = {'id': 'one'}
    result = fuse([{'_id': 'one', '_source': source, '_score': .9}],
                  [{'_id': 'one', '_source': source, '_score': .5}])[0]
    assert abs(result['score'] - .82) < 1e-8
    assert result['keyword_score'] == .5
