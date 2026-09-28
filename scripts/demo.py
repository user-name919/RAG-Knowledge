"""Import fictional QA and call the running API. Reads secrets locally without printing them."""
import argparse
import json
from pathlib import Path
import time
import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--query', default='题布置下去了，孩子那边怎么没显示？')
parser.add_argument('--url', default='http://127.0.0.1:8000')
args = parser.parse_args()
env = dotenv_values(root / '.env')
state_path = root / 'data' / 'demo.json'
state_path.parent.mkdir(exist_ok=True)
with httpx.Client(base_url=args.url, headers={'api-key': env['ADMIN_API_KEY']}, timeout=90, trust_env=False) as client:
    def call(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        if response.is_error:
            raise SystemExit(f'HTTP {response.status_code}: {response.text}')
        return response.json()
    health = call('GET', '/health')
    if not health['embedding_configured']:
        raise SystemExit('Fill EMBEDDING_API_KEY in .env, then recreate api and worker. No demo data was imported.')
    if state_path.exists():
        state = json.loads(state_path.read_text())
    else:
        kb = call('POST', '/v1/knowledge_bases', json={'name': '虚构演示知识库'})['id']
        doc = call('POST', f'/v1/knowledge_bases/{kb}/documents', json={'title': '虚构教学与运维 QA', 'source_uri': 'examples/qa_pairs.json'})['id']
        state = {'knowledge_base_id': kb, 'document_id': doc}
        state_path.write_text(json.dumps(state))
    path = f"/v1/knowledge_bases/{state['knowledge_base_id']}/documents/{state['document_id']}"
    call('POST', path + '/qa_pairs/batch_create', json=json.loads((root / 'examples/qa_pairs.json').read_text()))
    call('POST', path + '/retry_failed')
    for _ in range(60):
        rows = call('GET', path + '/index_status')['data']
        if rows and all(row['index_status'] == 'ready' for row in rows):
            break
        if any(row['index_status'] == 'failed' for row in rows):
            raise SystemExit(json.dumps(rows, ensure_ascii=False, indent=2))
        time.sleep(2)
    else:
        raise SystemExit('Indexing still pending; inspect index_status and worker logs.')
    result = call('POST', '/v1/knowledge_bases/recall', json={'knowledge_base_ids': [state['knowledge_base_id']], 'query': args.query})
    print(json.dumps(result, ensure_ascii=False, indent=2))
