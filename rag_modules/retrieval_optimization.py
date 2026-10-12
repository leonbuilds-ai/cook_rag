"""Chinese BM25 + Qdrant with pre-filtering and Reciprocal Rank Fusion."""
import hashlib
import logging
import jieba
from rank_bm25 import BM25Okapi
from langchain_core.documents import Document

logger = logging.getLogger(__name__)
STOPWORDS = set('的 了 和 是 在 我 有 什么 怎么 如何 哪些 请 请问 想 要 需要 可以 吗 呢 做'.split())


def tokenize(text):
    return [t for t in jieba.lcut(text) if t.strip() and t not in STOPWORDS]


def matches(doc, filters):
    return all(doc.metadata.get(key) in value if isinstance(value, list)
               else doc.metadata.get(key) == value for key, value in (filters or {}).items())


def fuse(*rankings, top_k=5, k=60):
    scores, docs = {}, {}
    for ranking in rankings:
        seen = set()
        for rank, doc in enumerate(ranking, 1):
            key = doc.metadata.get('chunk_id') or doc.metadata.get('parent_id')
            key = key or hashlib.sha256(doc.page_content.encode()).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0) + 1 / (k + rank)
            if key in docs:
                docs[key] = Document(page_content=doc.page_content,
                                     metadata={**docs[key].metadata, **doc.metadata})
            else:
                docs[key] = doc
    result = []
    for key in sorted(scores, key=scores.get, reverse=True)[:top_k]:
        doc = docs[key]
        result.append(Document(page_content=doc.page_content,
                               metadata={**doc.metadata, 'rrf_score': scores[key]}))
    return result


class RetrievalOptimizationModule:
    def __init__(self, vectorstore, chunks):
        self.vectorstore = vectorstore
        self.chunks = chunks
        self.bm25 = BM25Okapi([tokenize(d.page_content) or ['_'] for d in chunks]) if chunks else None

    def hybrid_search(self, query, top_k=5, filters=None):
        candidates = max(top_k * 3, 15)
        vector_docs = self.vectorstore.similarity_search(query, k=candidates, filters=filters)
        bm25_docs = []
        if self.bm25:
            scores = self.bm25.get_scores(tokenize(query))
            eligible = [i for i, d in enumerate(self.chunks) if matches(d, filters) and scores[i] > 0]
            bm25_docs = [self.chunks[i] for i in sorted(eligible, key=lambda i: scores[i], reverse=True)[:candidates]]
        return fuse(vector_docs, bm25_docs, top_k=top_k)

    def metadata_filtered_search(self, query, filters, top_k=5):
        return self.hybrid_search(query, top_k, filters)

    _rrf_rerank = staticmethod(lambda vector_docs, bm25_docs, k=60: fuse(vector_docs, bm25_docs, k=k,
                                                                      top_k=len(vector_docs) + len(bm25_docs)))
