import unittest
from unittest.mock import Mock
from langchain_core.documents import Document
from config import RAGConfig
from main import RecipeRAGSystem
from rag_modules.conversation import ConversationMemory
from rag_modules.recipe_preferences import wants_light, light_evidence


def recipe(name, text, category='荤菜'):
    return Document(page_content=text, metadata={'dish_name': name, 'parent_id': name, 'category': category})


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.memory = ConversationMemory()
        self.llm = Mock()
        self.docs = [recipe('小米辣炒肉', '小米辣'), recipe('尖叫牛蛙', '泡椒'), recipe('土豆炖排骨', '排骨')]
        self.names = [d.metadata['dish_name'] for d in self.docs]
        self.memory.remember('有什么推荐', '1. 小米辣炒肉\n2. 尖叫牛蛙\n3. 土豆炖排骨', self.docs)

    def test_bare_numbers_and_short_selection_resolve_actual_menu(self):
        for question in ['2', ' 2 ', '二', '选2', '我选2号', '2！']:
            result = self.memory.resolve(question, self.names, self.llm)
            self.assertEqual(result.query, '尖叫牛蛙怎么做？')
        self.llm.invoke.assert_not_called()

    def test_invalid_number_or_no_menu_requests_clarification(self):
        self.assertTrue(self.memory.resolve('8', self.names, self.llm).clarification)
        self.assertTrue(ConversationMemory().resolve('2', self.names, self.llm).clarification)
        self.assertNotEqual(self.memory.resolve('2人份', self.names, self.llm).query, '尖叫牛蛙怎么做？')

    def test_near_name_is_confirmed_not_silently_replaced(self):
        result = self.memory.resolve('尖椒牛蛙', self.names, self.llm)
        self.assertIn('是否指“尖叫牛蛙”', result.clarification)
        self.assertEqual(result.query, '尖椒牛蛙')
        confirmed = self.memory.resolve('是', self.names, self.llm)
        self.assertEqual(confirmed.query, '尖叫牛蛙怎么做？')
        self.assertIsNone(self.memory.pending_recipe)
        self.llm.invoke.assert_not_called()

    def test_exact_name_and_rejected_correction(self):
        self.assertFalse(self.memory.resolve('尖叫牛蛙', self.names, self.llm).clarification)
        self.memory.resolve('尖椒牛蛙怎么做', self.names, self.llm)
        self.assertTrue(self.memory.resolve('不是', self.names, self.llm).clarification)
        self.assertIsNone(self.memory.pending_recipe)
        self.memory.resolve('尖椒牛蛙', self.names, self.llm)
        self.memory.clear()
        self.assertIsNone(self.memory.pending_recipe)


class PreferenceTests(unittest.TestCase):
    def test_light_request_and_negation(self):
        self.assertTrue(wants_light('晚餐我想吃的清淡点，有什么推荐'))
        self.assertTrue(wants_light('推荐少油的晚餐'))
        self.assertFalse(wants_light('不用太清淡，有什么推荐'))

    def test_negative_disclaimers_and_spicy_or_fried_sources_are_excluded(self):
        for name, text in [('小米辣炒肉', '不建议清淡饮食的尝试。'),
                           ('尖叫牛蛙', '想吃清淡点加20克豆瓣酱，这菜不可能太清淡'),
                           ('蒸鱼', '放入泡椒蒸熟'), ('清淡炸鸡', '油炸至金黄')]:
            self.assertEqual(light_evidence(recipe(name, text)), '')

    def test_positive_source_evidence_and_no_meat_vegetable_assumption(self):
        self.assertTrue(light_evidence(recipe('清蒸鲈鱼', '放入蒸锅蒸熟')))
        self.assertTrue(light_evidence(recipe('鸡汤', '排骨炖煮', '汤品')))
        self.assertTrue(light_evidence(recipe('蒜蓉菜', '少油烹饪', '素菜')))
        self.assertFalse(light_evidence(recipe('土豆炖排骨', '排骨煎炒后炖煮')))
        self.assertFalse(light_evidence(recipe('炒青菜', '普通炒制', '素菜')))

    def test_preference_is_applied_before_retrieval_with_grounded_reasons(self):
        spicy = recipe('小米辣炒肉', '不建议清淡饮食')
        light = recipe('清蒸鲈鱼', '蒸熟即可')
        system = RecipeRAGSystem(RAGConfig())
        system.system_ready = True
        system.data_module = Mock(documents=[spicy, light])
        system.data_module.get_supported_categories.return_value = []
        system.data_module.get_supported_difficulties.return_value = []
        system.graph_rag_retrieval = Mock()
        system.graph_rag_retrieval.extract_entities.return_value = {'recipes': []}
        system.query_router = Mock()
        system.query_router.route_query.return_value = ([spicy, light], Mock())
        docs, _ = system.retrieve('晚餐清淡点有什么推荐')
        self.assertEqual(system.query_router.route_query.call_args.kwargs['filters']['parent_id'], ['清蒸鲈鱼'])
        self.assertEqual([d.metadata['dish_name'] for d in docs], ['清蒸鲈鱼'])
        self.assertIn('清蒸', docs[0].metadata['preference_evidence'])
        self.assertNotIn('preference_evidence', light.metadata)

    def test_name_correction_does_not_search_wrong_ingredient_graph(self):
        system = RecipeRAGSystem(RAGConfig())
        system.system_ready = True
        system.data_module = Mock(documents=[recipe('尖叫牛蛙', '牛蛙泡椒')])
        system.generation_module = Mock()
        system.retrieve = Mock()
        self.assertIn('是否指', system.ask_question('尖椒牛蛙'))
        system.retrieve.assert_not_called()


if __name__ == '__main__':
    unittest.main()
