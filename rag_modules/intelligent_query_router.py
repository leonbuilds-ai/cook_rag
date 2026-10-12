"""C9's three retrieval strategies, grounded in entities actually present in Neo4j."""
from dataclasses import dataclass
from enum import Enum
from .retrieval_optimization import fuse


class SearchStrategy(Enum):
    HYBRID_TRADITIONAL = 'hybrid_traditional'
    GRAPH_RAG = 'graph_rag'
    COMBINED = 'combined'


@dataclass
class QueryAnalysis:
    recommended_strategy: SearchStrategy
    reasoning: str
    entity_count: int
    query_complexity: float = 0.0
    relationship_intensity: float = 0.0
    reasoning_required: bool = False


class IntelligentQueryRouter:
    def __init__(self, traditional_retrieval, graph_rag_retrieval, config):
        self.traditional = traditional_retrieval
        self.graph = graph_rag_retrieval
        self.config = config
        self.stats = {s.value: 0 for s in SearchStrategy}
        self.stats['total_queries'] = 0

    def analyze_query(self, query):
        entities = self.graph.extract_entities(query)
        count = sum(map(len, entities.values()))
        relation = any(w in query for w in ('共同', '关系', '搭配', '相似', '类似', '区别', '比较'))
        constraints = bool(entities['excluded']) or (bool(entities['ingredients']) and not entities['recipes'])
        if constraints:
            strategy, reason = SearchStrategy.GRAPH_RAG, '按已知食材关系和排除条件检索菜谱'
        elif relation and count:
            strategy, reason = SearchStrategy.COMBINED, '结合原文检索与共享食材的图关系'
        else:
            strategy, reason = SearchStrategy.HYBRID_TRADITIONAL, '使用语义向量与中文关键词查找原文'
        return QueryAnalysis(strategy, reason, count, min(count / 4, 1), float(relation or constraints), relation)

    def route_query(self, query, top_k=None, filters=None, strategy=None):
        top_k = top_k or self.config.top_k
        analysis = self.analyze_query(query)
        if strategy and strategy != 'auto':
            analysis.recommended_strategy = SearchStrategy(strategy)
            analysis.reasoning = '用户指定检索策略'
        selected = analysis.recommended_strategy
        entities = self.graph.extract_entities(query)
        constrained = bool(entities['excluded']) or (bool(entities['ingredients']) and not entities['recipes'])
        if constrained and selected != SearchStrategy.GRAPH_RAG:
            allowed_docs = self.graph.graph_rag_search(query, len(self.graph.data_module.documents), filters)
            if not allowed_docs:
                self.stats[selected.value] += 1
                self.stats['total_queries'] += 1
                return [], analysis
            filters = {**(filters or {}), 'parent_id': [d.metadata['parent_id'] for d in allowed_docs]}
        if selected == SearchStrategy.HYBRID_TRADITIONAL:
            docs = self.traditional.hybrid_search(query, top_k, filters)
        elif selected == SearchStrategy.GRAPH_RAG:
            docs = self.graph.graph_rag_search(query, top_k, filters)
            # Empty constrained results are meaningful; vector fallback would violate constraints.
        else:
            graph_docs = self.graph.graph_rag_search(query, top_k * 2, filters)
            vector_docs = self.traditional.hybrid_search(query, top_k * 3, filters)
            # Fuse at recipe level so multiple chunks cannot crowd out graph recipes.
            parents = {d.metadata['parent_id']: d for d in self.graph.data_module.documents}
            traditional_parents = [parents[d.metadata['parent_id']] for d in vector_docs
                                   if d.metadata['parent_id'] in parents]
            docs = fuse(graph_docs, traditional_parents, top_k=top_k)
        self.stats[selected.value] += 1
        self.stats['total_queries'] += 1
        return docs, analysis

    def get_route_statistics(self):
        return dict(self.stats)
