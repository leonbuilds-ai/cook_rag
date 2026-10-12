"""Compare the preserved chunk-RRF baseline with recipe retrieval on the same index."""
import argparse
import hashlib
import json
import logging
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from config import ROOT
from main import RecipeRAGSystem
from rag_modules.sources import canonical_source


def unique_sources(docs, k=5):
    return list(dict.fromkeys(canonical_source(d.metadata['source']) for d in docs))[:k]


def metrics(retrieved, relevant, k=5):
    truth = set(relevant)
    if not truth:
        raise ValueError('每条评估问题必须有相关文档')
    retrieved = list(dict.fromkeys(retrieved))[:k]
    recall = len(set(retrieved) & truth) / len(truth)
    mrr = next((1 / rank for rank, source in enumerate(retrieved, 1) if source in truth), 0)
    return {'recall_at_5': recall, 'mrr_at_5': mrr}


def evaluate(dataset, system, split='all'):
    raw = Path(dataset).read_bytes()
    cases = json.loads(raw)
    if split != 'all':
        cases = [case for case in cases if case['split'] == split]
    if not cases or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('评估集为空或 ID 重复')
    available = {canonical_source(d.metadata['source']) for d in system.data_module.documents}
    for case in cases:
        missing = set(case['relevant']) - available
        if missing:
            raise ValueError(f"{case['id']} 的标注文档不存在: {missing}")
    retrieval = system.retrieval_module
    records = []
    # Both algorithms see the same corpus, embedding model and Qdrant collection.
    for case in cases:
        row = dict(case)
        for mode in ('baseline', 'recipe'):
            started = time.perf_counter()
            # Old application requested 15 fused chunks before recipe deduplication.
            docs = retrieval.baseline_search(case['query'], top_k=15) if mode == 'baseline' else retrieval.recipe_search(case['query'], top_k=5)
            sources = unique_sources(docs)
            row[mode] = {**metrics(sources, case['relevant']), 'retrieved': sources,
                         'latency_ms': round((time.perf_counter() - started) * 1000, 2)}
        records.append(row)
        print(case['id'], {mode: row[mode]['mrr_at_5'] for mode in ('baseline', 'recipe')}, flush=True)
    summary = {}
    for group in ('all', 'dev', 'test'):
        subset = [r for r in records if group == 'all' or r['split'] == group]
        if subset:
            summary[group] = {mode: {'queries': len(subset), **{
                metric: round(statistics.mean(r[mode][metric] for r in subset), 6)
                for metric in ('recall_at_5', 'mrr_at_5', 'latency_ms')}} for mode in ('baseline', 'recipe')}
    corpus = [(canonical_source(d.metadata['source']), d.page_content) for d in system.data_module.documents]
    return {'created_at': datetime.now(timezone.utc).isoformat(), 'k': 5,
        'dataset_sha256': hashlib.sha256(raw).hexdigest(),
        'corpus_sha256': hashlib.sha256(json.dumps(sorted(corpus), ensure_ascii=False).encode()).hexdigest(),
        'recipes': len(corpus), 'chunks': len(system.data_module.chunks),
        'embedding_model': system.config.embedding_model, 'collection': system.index_module.collection_name,
        'scope': 'hybrid retriever only; no routing, history, filters or answer generation',
        'summary': summary, 'queries': records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, default=ROOT / 'evaluation/queries.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'evaluation/results.json')
    parser.add_argument('--split', choices=['dev', 'test', 'all'], default='all')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    system = RecipeRAGSystem()
    try:
        system.initialize_system()
        system.build_knowledge_base()
        result = evaluate(args.dataset, system, args.split)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result['summary'], ensure_ascii=False, indent=2))
    finally:
        system.close()


if __name__ == '__main__':
    main()
