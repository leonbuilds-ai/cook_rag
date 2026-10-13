"""Offline regressions for persistence, constrained retrieval, and context assembly."""
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from langchain_core.documents import Document
from qdrant_client import QdrantClient
from config import RAGConfig
from rag_modules.data_preparation import DataPreparationModule
from rag_modules.graph_data_preparation import GraphDataPreparationModule
from rag_modules.graph_rag_retrieval import GraphRAGRetrieval
from rag_modules.index_construction import QdrantIndexConstructionModule
from rag_modules.retrieval_optimization import RetrievalOptimizationModule, fuse
from rag_modules.intelligent_query_router import IntelligentQueryRouter, SearchStrategy
from rag_modules.generation_integration import GenerationIntegrationModule


def document(text, key='1', category='素菜'):
    return Document(page_content=text, metadata={'chunk_id': str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
        'parent_id': key, 'category': category, 'dish_name': text[:20], 'source': 'test.md'})


class FakeEmbeddings:
    def __init__(self):
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        return [[1.0, 0.0] if '鸡蛋' in text else [0.0, 1.0] for text in texts]

    def embed_query(self, query):
        return [1.0, 0.0] if '鸡蛋' in query else [0.0, 1.0]


class IndexTests(unittest.TestCase):
    def setUp(self):
        workspace_temp = Path(__file__).resolve().parents[1] / 'vector_index'
        workspace_temp.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=workspace_temp)
        self.config = RAGConfig(index_save_path=self.temp.name, embedding_batch_size=1)
        self.client = QdrantClient(':memory:')
        self.embeddings = FakeEmbeddings()
        self.index = QdrantIndexConstructionModule(self.config, self.embeddings, self.client)
        self.chunks = [document('西红柿鸡蛋', '1'), document('炒牛肉', '2', '荤菜')]

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_build_reuse_and_filter(self):
        self.index.build_vector_index(self.chunks)
        self.assertEqual(self.index.get_collection_stats()['row_count'], 2)
        self.assertIs(self.index.load_index(self.chunks), self.index)
        self.assertEqual(self.embeddings.calls, 2)
        results = self.index.similarity_search('鸡蛋', filters={'category': ['荤菜']})
        self.assertEqual([d.page_content for d in results], ['炒牛肉'])

    def test_changed_data_or_model_uses_new_collection(self):
        self.index.build_vector_index(self.chunks)
        original = self.index.collection_name
        changed = [document('新菜谱', '3')]
        self.assertIsNone(self.index.load_index(changed))
        self.assertNotEqual(self.index.collection_name, original)
        self.assertTrue(self.client.collection_exists(original))
        self.index.config = replace(self.config, embedding_model='other')
        self.index.prepare_collection(self.chunks)
        self.assertNotEqual(self.index.collection_name, original)

    def test_incomplete_collection_cannot_be_reused(self):
        self.index.build_vector_index(self.chunks)
        self.client.delete(self.index.collection_name, [self.chunks[0].metadata['chunk_id']], wait=True)
        self.assertIsNone(self.index.load_index(self.chunks))
        self.index.build_vector_index(self.chunks)
        self.assertIsNotNone(self.index.load_index(self.chunks))

    def test_upsert_does_not_duplicate_chunks(self):
        self.index.build_vector_index(self.chunks)
        self.index.build_vector_index(self.chunks)
        self.assertEqual(self.index.get_collection_stats()['row_count'], 2)

    def test_incremental_index_reuses_existing_vectors(self):
        self.index.build_vector_index(self.chunks)
        original=self.index.collection_name
        self.index.build_vector_index(self.chunks + [document('新菜','3')],reuse_collection=original)
        self.assertEqual(self.index.last_build,{'reused_chunks':2,'embedded_chunks':1})
        self.assertEqual(self.embeddings.calls,3)
        self.assertTrue(self.client.collection_exists(original))
        self.assertEqual(self.index.get_collection_stats()['row_count'],3)

    def test_hybrid_metadata_filter(self):
        self.index.build_vector_index(self.chunks)
        retrieval = RetrievalOptimizationModule(self.index, self.chunks)
        results = retrieval.metadata_filtered_search('牛肉', {'category': '素菜'}, top_k=10)
        self.assertTrue(results)
        self.assertTrue(all(d.metadata['category'] == '素菜' for d in results))


class DataTests(unittest.TestCase):
    def test_new_document_does_not_change_existing_chunk_ids(self):
        data=DataPreparationModule('.')
        original=Document(page_content='# 原食谱\n## 步骤\n- 煮熟。',metadata={'parent_id':'a','source':'a.md'})
        data.documents=[original]; before=[d.metadata['chunk_id'] for d in data.chunk_documents(100,10)]
        data.documents=[Document(page_content='# 新食谱\n## 步骤\n- 蒸熟。',metadata={'parent_id':'b','source':'b.md'}),original]
        after=[d.metadata['chunk_id'] for d in data.chunk_documents(100,10) if d.metadata['parent_id']=='a']
        self.assertEqual(before,after)

    def test_chunk_ids_stable_and_size_bounded(self):
        data = DataPreparationModule('.')
        data.documents = [Document(page_content='# 菜谱\n## 操作\n' + '搅拌。' * 300,
                                   metadata={'parent_id': 'a', 'source': 'a.md'})]
        first = data.chunk_documents(100, 10)
        second = data.chunk_documents(100, 10)
        self.assertEqual([d.metadata['chunk_id'] for d in first], [d.metadata['chunk_id'] for d in second])
        self.assertTrue(all(len(d.page_content) <= 100 for d in first))
        self.assertEqual(len(data.get_parent_documents(first)), 1)

    def test_markdown_import_preserves_steps_and_quantities(self):
        ingredients, steps = GraphDataPreparationModule.extract_local_sections(
            '# 测试\n## 必备原料和工具\n- 鸡蛋 2 个\n- 西红柿（切块）\n## 操作\n1. 打散鸡蛋。\n* 炒熟。')
        self.assertEqual([i['name'] for i in ingredients], ['鸡蛋', '西红柿'])
        self.assertEqual(ingredients[0]['amount'], '鸡蛋 2 个')
        self.assertEqual(steps, ['打散鸡蛋。', '炒熟。'])

    def test_config_redacts_secrets(self):
        config = RAGConfig(neo4j_password='secret1', qdrant_api_key='secret2')
        self.assertNotIn('secret1', repr(config))
        self.assertNotIn('secret2', str(config.to_dict()))

    def test_context_keeps_long_first_recipe(self):
        generation = GenerationIntegrationModule.__new__(GenerationIntegrationModule)
        generation.config = RAGConfig()
        context = generation._build_context([document('长食谱' * 3000)], max_length=500)
        self.assertIn('长食谱', context)
        self.assertIn('test.md', context)
        self.assertIn('截断', context)

    def test_rrf_distinguishes_identical_text_from_different_recipes(self):
        a, b = document('加盐', 'a'), document('加盐', 'b')
        result = fuse([a, b], [a], top_k=5)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].metadata['parent_id'], 'a')
        self.assertNotIn('rrf_score', a.metadata)
        graph = Document(page_content=a.page_content, metadata={**a.metadata, 'graph_evidence': ['真实关系']})
        combined = fuse([graph], [a], top_k=1)
        self.assertEqual(combined[0].metadata['graph_evidence'], ['真实关系'])


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.graph = GraphRAGRetrieval.__new__(GraphRAGRetrieval)
        self.graph.entities = [{'name': '鸡蛋', 'labels': ['Ingredient']},
            {'name': '西红柿', 'labels': ['Ingredient']},
            {'name': '西红柿炒鸡蛋', 'labels': ['Recipe']}]
        self.graph.graph_rag_search = Mock(return_value=[])
        self.graph.data_module = Mock(documents=[document('测试')])
        self.traditional = Mock()
        self.router = IntelligentQueryRouter(self.traditional, self.graph, RAGConfig())

    def test_route_detail_relation_and_constraint(self):
        self.assertEqual(self.router.analyze_query('西红柿炒鸡蛋怎么做').recommended_strategy,
                         SearchStrategy.HYBRID_TRADITIONAL)
        self.assertEqual(self.router.analyze_query('与西红柿炒鸡蛋相似的菜').recommended_strategy,
                         SearchStrategy.COMBINED)
        self.assertEqual(self.router.analyze_query('鸡蛋和西红柿能做哪些菜').recommended_strategy,
                         SearchStrategy.GRAPH_RAG)

    def test_exclusion_and_no_unconstrained_fallback(self):
        self.assertEqual(self.graph.extract_entities('不要鸡蛋的菜')['excluded'], ['鸡蛋'])
        docs, analysis = self.router.route_query('不要鸡蛋的菜')
        self.assertEqual(docs, [])
        self.traditional.hybrid_search.assert_not_called()
        self.assertEqual(self.router.get_route_statistics()['total_queries'], 1)

    def test_forced_vector_route_still_honors_exclusion(self):
        docs, _ = self.router.route_query('不要鸡蛋的菜', strategy='hybrid_traditional')
        self.assertEqual(docs, [])
        self.traditional.hybrid_search.assert_not_called()


if __name__ == '__main__':
    unittest.main()
