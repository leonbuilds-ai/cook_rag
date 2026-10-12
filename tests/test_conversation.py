import unittest
from unittest.mock import Mock
from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from config import RAGConfig
from main import RecipeRAGSystem
from rag_modules.conversation import ConversationMemory
from rag_modules.generation_integration import GenerationIntegrationModule


def recipe(name):
    return Document(page_content=f'# {name}\n原料和步骤', metadata={'dish_name': name, 'parent_id': name})


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.memory = ConversationMemory()
        self.docs = [recipe('西红柿炒鸡蛋'), recipe('麻婆豆腐')]
        self.llm = Mock()

    def test_select_actual_answer_order_not_retrieval_order(self):
        self.memory.remember('推荐两个菜', '1. 麻婆豆腐\n2. 西红柿炒鸡蛋', self.docs)
        resolved = self.memory.resolve('第二个怎么做', [], self.llm)
        self.assertIn('西红柿炒鸡蛋', resolved.query)
        self.llm.invoke.assert_not_called()

    def test_explicit_numbers_and_markdown_heading(self):
        self.memory.remember('推荐几个菜', '### 1. **麻婆豆腐**\n### 3. **西红柿炒鸡蛋**', self.docs)
        self.assertIn('西红柿炒鸡蛋', self.memory.resolve('第3道', [], self.llm).query)
        self.assertTrue(self.memory.resolve('第二个', [], self.llm).clarification)

    def test_missing_history_or_invalid_selection_requests_clarification(self):
        self.assertTrue(self.memory.resolve('第二个', [], self.llm).clarification)
        self.assertTrue(self.memory.resolve('那个怎么做', [], self.llm).clarification)
        self.memory.remember('推荐菜', '1. 麻婆豆腐', self.docs)
        self.assertTrue(self.memory.resolve('第三个', [], self.llm).clarification)

    def test_single_dish_follow_up_and_new_topic(self):
        self.memory.remember('麻婆豆腐怎么做', '麻婆豆腐需要豆腐。', [recipe('麻婆豆腐')])
        self.assertIn('麻婆豆腐', self.memory.resolve('它需要多少盐', [], self.llm).query)
        resolved = self.memory.resolve('西红柿炒鸡蛋怎么做', ['西红柿炒鸡蛋'], self.llm)
        self.assertEqual(resolved.query, '西红柿炒鸡蛋怎么做')
        self.llm.invoke.assert_not_called()

    def test_llm_receives_history_and_rewrites_constraints(self):
        self.memory.remember('不要鸡蛋，推荐素菜', '1. 麻婆豆腐\n2. 凉拌黄瓜', [recipe('麻婆豆腐'), recipe('凉拌黄瓜')])
        self.llm.invoke.return_value = AIMessage(content='{"query":"再推荐几道不含鸡蛋的素菜", "clarification":""}')
        self.assertIn('不含鸡蛋', self.memory.resolve('换几个', [], self.llm).query)
        messages = self.llm.invoke.call_args.args[0]
        self.assertIn('不要鸡蛋', messages[1].content)
        self.assertIn('麻婆豆腐', messages[2].content)

    def test_invalid_llm_result_does_not_guess_ambiguous_dish(self):
        self.memory.remember('推荐两个菜', '1. 麻婆豆腐\n2. 西红柿炒鸡蛋', self.docs)
        self.llm.invoke.return_value = AIMessage(content='bad json')
        self.assertTrue(self.memory.resolve('那个怎么做', [], self.llm).clarification)

    def test_history_bounded_and_clear(self):
        memory = ConversationMemory(max_turns=2, max_chars=600)
        for i in range(4):
            memory.remember(str(i), '长回答' * 100, [])
        self.assertEqual(len(memory.turns), 2)
        messages = memory.messages()
        self.assertLessEqual(sum(len(m.content) for m in messages), 600)
        self.assertEqual([m.type for m in messages], ['human', 'ai', 'human', 'ai'])
        memory.clear()
        self.assertEqual(memory.messages(), [])


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.system = RecipeRAGSystem(RAGConfig())
        self.system.system_ready = True
        self.docs = [recipe('西红柿炒鸡蛋'), recipe('麻婆豆腐')]
        self.system.data_module = Mock(documents=self.docs)
        self.system.generation_module = Mock()
        self.system.retrieve = Mock(return_value=(self.docs, Mock()))

    def test_two_turns_selection_and_generation_history(self):
        self.system.generation_module.generate_basic_answer.return_value = '1. 麻婆豆腐\n2. 西红柿炒鸡蛋'
        self.system.ask_question('推荐两个菜')
        self.system.retrieve.return_value = ([self.docs[0]], Mock())
        self.system.generation_module.generate_step_by_step_answer.return_value = '西红柿炒鸡蛋：先炒蛋，再炒西红柿。'
        self.system.ask_question('第二个怎么做')
        self.assertIn('西红柿炒鸡蛋', self.system.retrieve.call_args.args[0])
        history = self.system.generation_module.generate_step_by_step_answer.call_args.kwargs['history']
        self.assertEqual(len(history), 2)
        self.assertIn('麻婆豆腐', history[1].content)
        self.assertEqual(len(self.system.conversation.turns), 2)

    def test_stream_remembered_only_after_exhaustion(self):
        self.system.generation_module.generate_basic_answer_stream.return_value = iter(['1. 麻婆', '豆腐\n2. 西红柿炒鸡蛋'])
        stream = self.system.ask_question('推荐两个菜', stream=True)
        self.assertEqual(len(self.system.conversation.turns), 0)
        self.assertEqual(next(stream), '1. 麻婆')
        self.assertEqual(len(self.system.conversation.turns), 0)
        list(stream)
        self.assertEqual(self.system.conversation.turns[0].choices[2], '西红柿炒鸡蛋')

    def test_interrupted_or_failed_stream_is_not_recorded(self):
        self.system.generation_module.generate_basic_answer_stream.return_value = iter(['hello', 'world'])
        stream = self.system.ask_question('推荐两个菜', stream=True)
        next(stream)
        stream.close()
        self.assertEqual(self.system.conversation.turns, [])
        def broken():
            yield 'partial'
            raise RuntimeError('failed')
        self.system.generation_module.generate_basic_answer_stream.return_value = broken()
        with self.assertRaises(RuntimeError):
            list(self.system.ask_question('推荐两个菜', stream=True))
        self.assertEqual(self.system.conversation.turns, [])

    def test_clarification_does_not_retrieve_and_sessions_are_isolated(self):
        answer = self.system.ask_question('第二个怎么做')
        self.assertIn('菜名', answer)
        self.system.retrieve.assert_not_called()
        other = RecipeRAGSystem(RAGConfig())
        self.system.conversation.remember('菜名', '西红柿炒鸡蛋', [self.docs[0]])
        self.assertEqual(other.conversation.turns, [])
        self.system.clear_history()
        self.assertEqual(self.system.conversation.turns, [])

    def test_actual_generation_prompt_includes_history(self):
        generation = GenerationIntegrationModule.__new__(GenerationIntegrationModule)
        generation.config = RAGConfig()
        generation.llm = FakeListChatModel(responses=['回答'])
        captured = []
        from langchain_core.callbacks import BaseCallbackHandler
        class Capture(BaseCallbackHandler):
            def on_chat_model_start(self, serialized, messages, **kwargs):
                captured.extend(messages[0])
        generation.llm.callbacks = [Capture()]
        memory = ConversationMemory()
        memory.remember('不要鸡蛋', '推荐麻婆豆腐', [recipe('麻婆豆腐')])
        generation.generate_basic_answer('这个怎么做', [recipe('麻婆豆腐')], history=memory.messages())
        self.assertIn('不要鸡蛋', captured[0].content)
        self.assertIn('推荐麻婆豆腐', captured[0].content)


if __name__ == '__main__':
    unittest.main()
