"""旧系统迁移工具：先停写导出，再导入空 PostgreSQL；不删除或改写旧库。

导出需要单独安装 scripts/requirements-migration.txt。快照含内部资料和授权摘要，
只能放到被 Git 忽略的私有目录。导入保留 ID/版本/任务/向量，不调用 Embedding。
原文件不在数据库快照中，Compose 在本机复用原 upload_data 卷；跨机需另备份该卷。
"""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values
from sqlalchemy import create_engine, select, DateTime, text
from app.models import Base


def export_snapshot(source_env, destination):
    import httpx
    config = dotenv_values(source_env)
    source = create_engine(config['DATABASE_URL'])
    tables = {}
    with source.connect() as conn:
        for table in Base.metadata.sorted_tables:
            tables[table.name] = [dict(r) for r in conn.execute(select(table)).mappings()]
    index = config.get('ES_INDEX', 'team-rag-bge-zh-v1')
    chunks, scroll = [], None
    with httpx.Client(base_url=config.get('ES_URL', 'http://127.0.0.1:9200'), timeout=60, trust_env=False) as client:
        def request(method, path, **kwargs):
            r = client.request(method, path, **kwargs)
            r.raise_for_status()
            return r.json()
        metadata = request('GET', '/' + index + '/_mapping')[index]['mappings']
        try:
            page = request('POST', '/' + index + '/_search?scroll=2m', json={'size': 500, 'query': {'match_all': {}}})
            while True:
                scroll = page.get('_scroll_id')
                hits = page['hits']['hits']
                if not hits:
                    break
                chunks.extend(hit['_source'] for hit in hits)
                page = request('POST', '/_search/scroll', json={'scroll': '2m', 'scroll_id': scroll})
        finally:
            if scroll:
                request('DELETE', '/_search/scroll', json={'scroll_id': [scroll]})
    snapshot = {'format': 1, 'model': metadata['_meta']['embedding_model'],
                'dimensions': metadata['properties']['embedding']['dims'], 'tables': tables, 'chunks': chunks}
    # 不覆盖已有备份；0600 限制本地其他账号读取。
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(snapshot, handle, ensure_ascii=False, default=lambda value: value.isoformat())
    print('Exported:', {name: len(rows) for name, rows in tables.items()}, 'chunks:', len(chunks))


def import_snapshot(source):
    from app.config import get_settings
    from app.db import engine
    from app.search import SearchStore
    snapshot = json.loads(Path(source).read_text())
    settings = get_settings()
    if snapshot['format'] != 1 or (snapshot['model'], snapshot['dimensions']) != (settings.embedding_model, settings.embedding_dimensions):
        raise RuntimeError('Snapshot format/model/dimensions mismatch')
    Base.metadata.create_all(engine)
    store = SearchStore()
    store.ensure_index()
    with engine.begin() as conn:
        # 只允许导入空目标库，避免覆盖用户新增数据；任何失败会回滚整个数据导入事务。
        for table in Base.metadata.sorted_tables:
            if conn.execute(select(table).limit(1)).first():
                raise RuntimeError('Target business tables must be empty')
        if conn.execute(text('SELECT count(*) FROM knowledge_chunks')).scalar_one():
            raise RuntimeError('Target chunks must be empty')
        for table in Base.metadata.sorted_tables:
            rows = snapshot['tables'][table.name]
            for row in rows:
                for column in table.columns:
                    if isinstance(column.type, DateTime) and row.get(column.name) is not None:
                        row[column.name] = datetime.fromisoformat(row[column.name])
            if rows:
                conn.execute(table.insert(), rows)
        chunks = snapshot['chunks']
        for start in range(0, len(chunks), 100):
            batch = chunks[start:start + 100]
            store.put_chunks([{k: v for k, v in c.items() if k != 'embedding'} for c in batch], [c['embedding'] for c in batch], db=conn)
    print('Imported:', {name: len(rows) for name, rows in snapshot['tables'].items()}, 'chunks:', store.count())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['export', 'import'])
    parser.add_argument('snapshot', help='私有快照路径，例如 data/migration-backup/snapshot.json')
    parser.add_argument('--source-env', help='旧版 .env，仅 export 需要')
    args = parser.parse_args()
    try:
        if args.action == 'export':
            if not args.source_env:
                parser.error('export requires --source-env')
            export_snapshot(args.source_env, args.snapshot)
        else:
            import_snapshot(args.snapshot)
    except Exception as exc:
        # SQL 异常可能携带凭据和文档内容，不打印完整堆栈或参数。
        print('Migration failed:', type(exc).__name__, file=sys.stderr)
        sys.exit(1)
