"""Tiny fictional smoke set; do not interpret it as business accuracy."""
import argparse
import json
from pathlib import Path
import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8000')
parser.add_argument('--output', default='data/evaluation.json')
args = parser.parse_args()
env = dotenv_values(root / '.env')
state = json.loads((root / 'data/demo.json').read_text())
cases = json.loads((root / 'examples/eval_cases.json').read_text())
with httpx.Client(base_url=args.url, headers={'api-key': env['ADMIN_API_KEY']}, timeout=90, trust_env=False) as client:
    def request(method, path, **kwargs):
        r = client.request(method, path, **kwargs)
        r.raise_for_status()
        return r.json()
    path = f"/v1/knowledge_bases/{state['knowledge_base_id']}/documents/{state['document_id']}"
    qa_rows = request('GET', path + '/qa_pairs')['data']
    ids = {q['id']: q['external_id'] for q in qa_rows}
    results = []
    for case in cases:
        response = request('POST', '/v1/knowledge_bases/recall', json={'knowledge_base_ids': [state['knowledge_base_id']], 'document_ids': [state['document_id']], 'query': case['query']})
        hits = response['data']
        top = ids.get(hits[0]['qa_id']) if hits else None
        results.append({**case, 'top_external_id': top, 'top1_correct': top == case['expected_external_id'], 'top_score': hits[0]['score'] if hits else None})
    probe = request('POST', '/v1/knowledge_bases/recall', json={'knowledge_base_ids': [state['knowledge_base_id']], 'query': '公司差旅酒店报销限额是多少？'})
    report = {
        'scope': '7 fictional queries over 3 fictional QA records; smoke test only, not business accuracy',
        'model': env['EMBEDDING_MODEL'],
        'weights': {'vector': .8, 'keyword': .2},
        'top1_correct': sum(r['top1_correct'] for r in results), 'total': len(results), 'results': results,
        'unanswerable_probe': {'query': '公司差旅酒店报销限额是多少？', 'returned': probe['total'], 'top_score': probe['data'][0]['score'] if probe['data'] else None,
                               'note': 'Default threshold is 0. Unrelated results are expected; calibrate with business negative examples before answer generation.'},
    }
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
