import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch
from langchain_core.documents import Document
from config import RAGConfig
from main import RecipeRAGSystem, main
from rag_modules.dialogue_intent import direct_intent


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.system = RecipeRAGSystem(RAGConfig())
        self.system.system_ready = True
        self.docs = [Document(page_content='葱油拌面做法',
            metadata={'dish_name': '葱油拌面', 'category': '主食', 'parent_id': '1'})]
        self.system.data_module = Mock(documents=self.docs)
        self.system.generation_module = Mock()
        self.system.retrieve = Mock()

    def test_greeting_does_not_retrieve_or_call_model(self):
        for question in ['你好', '你好！', ' 您好呀 ', 'Hello!', '晚上好']:
            answer, analysis = self.system.ask_question_with_routing(question, stream=True, explain_routing=True)
            self.assertIsNone(analysis)
            self.assertIsInstance(answer, str)
            self.assertIn('你好', answer)
            self.assertNotIn('水煮鱼', answer)
            self.assertNotIn('知识库中详细记录', answer)
        self.system.retrieve.assert_not_called()
        self.system.generation_module.llm.invoke.assert_not_called()
        self.system.generation_module.generate_basic_answer_stream.assert_not_called()

    def test_social_message_preserves_recipe_focus_and_menu(self):
        self.system.conversation.remember('推荐主食', '1. 葱油拌面', self.docs)
        turns = list(self.system.conversation.turns)
        menus = list(self.system.conversation.menus)
        for question in ['你好', '谢谢', '好的']:
            self.assertTrue(self.system.ask_question(question))
        self.assertEqual(self.system.conversation.turns, turns)
        self.assertEqual(self.system.conversation.menus, menus)
        self.assertIn('葱油拌面', self.system.conversation.resolve('它需要多少油', [], Mock()).query)

    def test_mixed_greeting_and_recipe_request_is_not_intercepted(self):
        for question in ['你好，葱油拌面怎么做', '你好，推荐几个素菜', '谢谢，第二个需要多少盐',
                         '好吃的晚餐推荐', '早上好，早餐吃什么', '您好，我想做肉菜']:
            self.assertIsNone(direct_intent(question))
        self.system.retrieve.return_value = (self.docs, Mock())
        self.system.generation_module.generate_step_by_step_answer.return_value = '葱油拌面做法'
        self.system.ask_question('你好，葱油拌面怎么做')
        self.system.retrieve.assert_called_once()

    def test_catalog_uses_full_corpus_not_retrieval_subset(self):
        self.system.data_module.documents = self.docs + [Document(page_content='蹄花',
            metadata={'dish_name': '老妈蹄花', 'category': '荤菜', 'parent_id': '2'})]
        answer = self.system.ask_question('你有多少食谱？')
        self.assertIn('2 份食谱', answer)
        self.assertIn('主食（1份）', answer)
        self.assertIn('荤菜（1份）', answer)
        self.system.retrieve.assert_not_called()
        self.system.generation_module.llm.invoke.assert_not_called()

    def test_interactive_prompt_explains_waiting_and_accepts_greeting(self):
        out = io.StringIO()
        with redirect_stdout(out), patch('builtins.input', side_effect=['你好', 'quit']) as read:
            self.system.run_interactive()
        self.assertIn('系统已就绪，正在等待输入', out.getvalue())
        self.assertIn('你好！我是食谱助手', out.getvalue())
        self.assertNotIn('检索策略', out.getvalue())
        self.assertIn('Enter', read.call_args_list[0].args[0])
        self.system.retrieve.assert_not_called()

    def test_interactive_eof_exits_without_retry_loop(self):
        with redirect_stdout(io.StringIO()), patch('builtins.input', side_effect=EOFError) as read:
            self.system.run_interactive()
        read.assert_called_once()

    def test_main_startup_completes_before_input_and_logging_uses_stdout(self):
        out = io.StringIO()
        fake = Mock()
        fake.initialize_system.side_effect = lambda: print('CONNECTED')
        fake.build_knowledge_base.side_effect = lambda *args: print('READY')
        fake.run_interactive.side_effect = lambda: print('INPUT')
        with redirect_stdout(out), patch.object(sys, 'argv', ['main.py']), \
                patch('main.RecipeRAGSystem', return_value=fake), patch('main.logging.basicConfig') as setup:
            main()
        text = out.getvalue()
        self.assertLess(text.index('正在连接'), text.index('CONNECTED'))
        self.assertLess(text.index('CONNECTED'), text.index('正在加载'))
        self.assertLess(text.index('READY'), text.index('INPUT'))
        handlers = setup.call_args.kwargs['handlers']
        self.assertIs(handlers[1].stream, out)
        for handler in handlers:
            handler.close()
        self.assertTrue(setup.call_args.kwargs['force'])
        fake.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
