"""Stable document references shared by CLI and HTTP responses."""
from pathlib import Path
from urllib.parse import quote


def canonical_source(value):
    text = str(value).replace('\\', '/')
    for marker in ('data/C8/cook/', 'data/cook/'):
        if marker in text:
            relative = text.split(marker, 1)[1]
            if '..' not in Path(relative).parts:
                return 'data/cook/' + relative
    return text


def references(docs, base_url=''):
    result, seen = [], set()
    for doc in docs:
        key = doc.metadata.get('parent_id', doc.metadata.get('source', ''))
        if key in seen:
            continue
        seen.add(key)
        path = canonical_source(doc.metadata.get('source', ''))
        # Only expose repository paths or graph identifiers, never machine-specific paths.
        if not path.startswith(('data/cook/', 'neo4j:')):
            path = 'neo4j:' + str(key)
        result.append({'id': f'S{len(result) + 1}', 'title': doc.metadata.get('dish_name', '食谱'),
            'document_id': str(key), 'path': path,
            'url': base_url.rstrip('/') + '/' + quote(path, safe='/') if path.startswith('data/cook/') and base_url else None})
    return result


def source_footer(sources):
    if not sources:
        return ''
    rows = [f"- [{s['id']}] " + (f"[{s['title']}]({s['url']})" if s['url'] else s['title']) +
            f" — `{s['path']}`" for s in sources]
    return '\n\n文档来源：\n' + '\n'.join(rows)
