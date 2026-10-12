import unittest
from unittest.mock import Mock
from langchain_core.documents import Document
from rag_modules.retrieval_optimization import RetrievalOptimizationModule


def parent(key, name, category='素菜'):
    return Document(page_content=f'# {name}\n食材和步骤', metadata={
        'parent_id': key, 'dish_name': name, 'category': category, 'source': key + '.md'})


class RecipeRetrievalTests(unittest.TestCase):
    def test_repeated_chunks_do_not_crowd_out_distinct_recipes(self):
        parents = [parent(str(i), f'测试菜{i}') for i in range(6)]
        chunks = [Document(page_content=p.page_content, metadata={**p.metadata, 'chunk_id': f'{i}-{j}'})
                  for i, p in enumerate(parents) for j in range(5)]
        store = Mock()
        store.similarity_search.return_value = chunks
        retriever = RetrievalOptimizationModule(store, chunks, parents, 'recipe')
        docs = retriever.hybrid_search('无匹配检索词', top_k=5)
        self.assertEqual(len(docs), 5)
        self.assertEqual(len({d.metadata['parent_id'] for d in docs}), 5)

    def test_alias_and_exact_dish_rank_above_generic_hits(self):
        target, other = parent('1', '西红柿炒鸡蛋'), parent('2', '糖拌西红柿')
        store = Mock()
        store.similarity_search.return_value = [other, target]
        retriever = RetrievalOptimizationModule(store, [other, target], [other, target], 'recipe')
        result = retriever.hybrid_search('番茄炒鸡蛋怎么做')
        self.assertEqual(result[0].metadata['parent_id'], '1')
        self.assertIn('西红柿', store.similarity_search.call_args.args[0])
        self.assertNotIn('rrf_score', target.metadata)

    def test_recipe_filters_apply_to_lexical_candidates(self):
        allowed, excluded = parent('1', '清蒸南瓜'), parent('2', '清蒸排骨', '荤菜')
        store = Mock()
        store.similarity_search.return_value = []
        parents = [allowed, excluded, parent('3', '凉拌黄瓜'), parent('4', '拌豆腐')]
        retriever = RetrievalOptimizationModule(store, parents, parents, 'recipe')
        result = retriever.hybrid_search('清蒸排骨或南瓜', filters={'category': '素菜'})
        self.assertEqual([d.metadata['parent_id'] for d in result], ['1'])


if __name__ == '__main__':
    unittest.main()
