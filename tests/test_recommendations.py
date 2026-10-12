import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock
from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from config import RAGConfig
from main import RecipeRAGSystem, print_answer
from rag_modules.conversation import ConversationMemory
from rag_modules.generation_integration import GenerationIntegrationModule


def dish(name, category='早餐', difficulty='简单'):
    return Document(page_content=f'# {name}\n## 操作\n按原文制作。',
        metadata={'dish_name': name, 'category': category, 'difficulty': difficulty, 'parent_id': name})


class RecommendationTests(unittest.TestCase):
    def test_standalone_meal_switch_never_calls_query_rewriter(self):
        memory = ConversationMemory()
        memory.remember('晚餐吃什么', '1. 老妈蹄花', [dish('老妈蹄花', '荤菜')])
        llm = Mock()
        for question in ['早餐吃什么', '推荐几个早餐', '有什么素菜', '午餐吃啥']:
            resolved = memory.resolve(question, [], llm)
            self.assertEqual(resolved.query, question)
            self.assertFalse(resolved.uses_history)
        llm.invoke.assert_not_called()

    def test_json_wrapped_answer_cannot_be_used_as_query(self):
        memory = ConversationMemory()
        docs = [dish('老妈蹄花'), dish('葱油拌面')]
        memory.remember('推荐晚餐', '1. 老妈蹄花\n2. 葱油拌面', docs)
        for bad_query in ['早餐吃什么？\n### 菜品推荐\n1. 老妈蹄花', '为您推荐以下几道菜：老妈蹄花。', '长回答' * 100]:
            llm = Mock()
            llm.invoke.return_value = AIMessage(content=json.dumps({'query': bad_query, 'clarification': ''}, ensure_ascii=False))
            resolved = memory.resolve('换几个', [], llm)
            self.assertTrue(resolved.clarification)
            self.assertEqual(resolved.query, '换几个')

    def test_dinner_then_breakfast_prints_one_list_per_turn(self):
        system = RecipeRAGSystem(RAGConfig())
        system.system_ready = True
        dinner = [dish('葱油拌面', '主食')]
        breakfast = [dish('牛奶燕麦'), dish('鸡蛋三明治'), dish('茶叶蛋')]
        system.data_module = Mock(documents=dinner + breakfast)
        system.retrieve = Mock(side_effect=[(dinner, Mock()), (breakfast, Mock())])
        generation = GenerationIntegrationModule.__new__(GenerationIntegrationModule)
        generation.config = RAGConfig()
        generation.llm = Mock()
        system.generation_module = generation
        out = io.StringIO()
        with redirect_stdout(out):
            print_answer(system.ask_question_with_routing('晚餐吃什么', stream=True, explain_routing=True)[0])
            print_answer(system.ask_question_with_routing('早餐吃什么', stream=True, explain_routing=True)[0])
        self.assertEqual(out.getvalue().count('为你推荐：'), 2)
        self.assertNotIn('追问解析:', out.getvalue())
        self.assertNotIn('老妈蹄花', out.getvalue())
        self.assertEqual(system.retrieve.call_args_list[1].args[0], '早餐吃什么')
        generation.llm.invoke.assert_not_called()
        self.assertEqual(system.conversation.menus[-1][2].recipes, ('鸡蛋三明治',))
        self.assertIn('鸡蛋三明治', system.conversation.resolve('第二个怎么做', [], generation.llm).query)

    def test_recommendation_list_is_short_and_never_claims_missing_steps(self):
        generation = GenerationIntegrationModule.__new__(GenerationIntegrationModule)
        docs = [dish('老妈蹄花', '荤菜', '困难'), dish('葱油拌面', '主食')]
        answer = generation.generate_list_answer('推荐两个晚餐', docs)
        self.assertIn('老妈蹄花', answer)
        self.assertIn('困难', answer)
        self.assertLess(len(answer), 200)
        self.assertNotIn('没有', answer)
        self.assertNotIn('缺失', answer)
        self.assertNotIn('制作步骤', answer)

    def test_breakfast_filter_is_preserved(self):
        system = RecipeRAGSystem(RAGConfig())
        system.system_ready = True
        system.data_module = Mock(documents=[])
        system.data_module.get_supported_categories.return_value = ['早餐', '荤菜']
        system.data_module.get_supported_difficulties.return_value = []
        system.graph_rag_retrieval = Mock()
        system.graph_rag_retrieval.extract_entities.return_value = {'recipes': []}
        system.query_router = Mock()
        system.query_router.route_query.return_value = ([], Mock())
        system.retrieve('早餐吃什么')
        self.assertEqual(system.query_router.route_query.call_args.kwargs['filters'], {'category': '早餐'})

    def test_named_new_dish_does_not_receive_previous_answer(self):
        memory = ConversationMemory()
        memory.remember('晚餐吃什么', '1. 老妈蹄花', [dish('老妈蹄花')])
        result = memory.resolve('牛奶燕麦怎么做', ['牛奶燕麦'], Mock())
        self.assertFalse(result.uses_history)
        self.assertEqual(result.query, '牛奶燕麦怎么做')


if __name__ == '__main__':
    unittest.main()
