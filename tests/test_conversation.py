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


DINNER = '''晚餐推荐：
### 1. 经典家常组合（推荐）
可以搭配一道**虎皮青椒**和一份**肉蛋盖饭**。
* **虎皮青椒**：口感酸辣开胃。
* **肉蛋盖饭**：适合单人晚餐。
### 2. 快手简单晚餐
时间紧迫可以选**葱油拌面**，只需约20分钟。
### 3. 进阶大餐（需提前准备）
可以尝试**老妈蹄花**，需要提前浸泡芸豆。
### 💡 温馨提示
“地三鲜”的信息不完整，暂时不选。
'''
DINNER_DOCS = [recipe(n) for n in ('虎皮青椒', '肉蛋盖饭', '葱油拌面', '老妈蹄花', '地三鲜')]


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

    def test_drink_display_spaces_preserve_numbered_selection(self):
        docs = [recipe(n) for n in ('冬瓜茶', 'B52轰炸机', '酸梅汤', '金菲士', '百香果橙子特调')]
        answer = ('### 🍹 推荐饮品清单\n1. **B52 轰炸机** [S1]\n- 原料：咖啡酒。\n'
                  '2. **冬瓜茶** [S2]\n3. **酸梅汤** [S3]\n4. **金菲士** [S4]\n5. **百香果橙子特调** [S5]')
        self.memory.remember('我想喝饮品', answer, docs)
        self.assertEqual(self.memory.menus[-1][1].recipes, ('B52轰炸机',))
        restored=ConversationMemory().restore(self.memory.snapshot())
        for question in ('第一个','1','第一个怎么做'):
            result=restored.resolve(question,[],self.llm)
            self.assertFalse(result.clarification)
            self.assertIn('B52轰炸机',result.query)
        self.llm.invoke.assert_not_called()

    def test_restore_repairs_incomplete_menu_using_only_answer_sources(self):
        from web_routes import menu_source_history
        answer='1. **B52 轰炸机** [S1]\n2. **冬瓜茶** [S2]'
        # Reproduce the old implementation's incomplete menu.
        self.memory.remember('推荐饮品',answer,[recipe('冬瓜茶')])
        self.assertNotIn(1,self.memory.menus[-1])
        messages=[{'role':'user','content':'我想喝饮品'},
                  {'role':'assistant','status':'complete','content':answer,
                   'payload':{'sources':[{'title':'B52轰炸机'},{'title':'冬瓜茶'}]}}]
        restored=ConversationMemory().restore(self.memory.snapshot(), menu_source_history(messages))
        self.assertEqual(len(restored.menus),1)
        self.assertIn('B52轰炸机',restored.resolve('1',[],self.llm).query)
        messages[-1]['status']='stopped'
        untouched=ConversationMemory().restore(self.memory.snapshot(), menu_source_history(messages))
        self.assertTrue(untouched.resolve('1',[],self.llm).clarification)

    def test_display_normalization_does_not_fuzzy_match_or_renumber(self):
        self.memory.remember('推荐饮品','1. **B52 神秘饮料**\n3. **冬 瓜 茶**',
                             [recipe('B52轰炸机'),recipe('冬瓜茶')])
        self.assertTrue(self.memory.resolve('1',[],self.llm).clarification)
        self.assertIn('冬瓜茶',self.memory.resolve('3',[],self.llm).query)
        self.assertTrue(self.memory.resolve('2',[],self.llm).clarification)

    def test_source_labels_are_independent_of_group_numbers_and_retrieval(self):
        names=['冬瓜茶','酸梅汤','百香果橙子特调','金菲士','柠檬水']
        answer='1. 传统消暑\n- 冬瓜茶 [S1]\n- 酸梅汤 [S2]\n2. 清新特调\n- 百香果橙子特调 [S3]\n- 金菲士 [S4]\n3. 简单快捷\n- 柠檬水 [S5]'
        sources=[{'id':f'S{i}','title':name} for i,name in enumerate(names,1)]
        self.memory.remember('想喝点饮品',answer,[recipe(n) for n in reversed(names)],sources)
        self.memory.remember('S4','金菲士的做法',[recipe('金菲士')],[{'id':'S1','title':'金菲士'}])
        restored=ConversationMemory().restore(self.memory.snapshot())
        for question in ('S4','s4','[S4]','选S4','S4怎么做'):
            self.assertEqual(restored.resolve(question,[],self.llm).query,'金菲士怎么做？')
        self.assertTrue(restored.resolve('S9',[],self.llm).clarification)
        self.llm.invoke.assert_not_called()
        restored.clear()
        self.assertTrue(restored.resolve('S4',[],self.llm).clarification)

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

    def test_dinner_headings_map_whole_sections_and_combination(self):
        self.memory.remember('晚餐吃什么', DINNER, DINNER_DOCS)
        menu = self.memory.menus[0]
        self.assertEqual(menu[1].recipes, ('虎皮青椒', '肉蛋盖饭'))
        self.assertEqual(menu[2].recipes, ('葱油拌面',))
        self.assertEqual(menu[3].recipes, ('老妈蹄花',))
        self.assertNotIn('地三鲜', menu[3].recipes)
        group = self.memory.resolve('这个：1. 经典家常组合', [], self.llm)
        self.assertIn('虎皮青椒', group.query)
        self.assertIn('肉蛋盖饭', group.query)

    def test_switch_back_to_original_menu_after_follow_up(self):
        self.memory.remember('晚餐吃什么', DINNER, DINNER_DOCS)
        first = self.memory.resolve('这个： 2. 快手简单晚餐', [], self.llm)
        self.assertEqual(first.query, '葱油拌面怎么做？')
        self.memory.remember('这个： 2. 快手简单晚餐', '## 葱油拌面\n1. 炸葱油\n2. 煮面\n3. 拌匀', [recipe('葱油拌面')])
        for question in ('那这个呢？：3. 进阶大餐', '第三个怎么做', '之前的进阶大餐呢'):
            self.assertIn('老妈蹄花', self.memory.resolve(question, [], self.llm).query)
            self.assertNotIn('葱油拌面', self.memory.resolve(question, [], self.llm).query)
        self.memory.remember('3. 进阶大餐', '老妈蹄花的详细做法', [recipe('老妈蹄花')])
        self.assertIn('葱油拌面', self.memory.resolve('再看2. 快手简单晚餐', [], self.llm).query)
        self.llm.invoke.assert_not_called()

    def test_menu_anchors_survive_long_answers_and_turn_pruning(self):
        memory = ConversationMemory(max_turns=2, max_chars=600)
        memory.remember('晚餐吃什么', DINNER, DINNER_DOCS)
        for _ in range(3):
            memory.remember('它怎么做', '葱油拌面' + '详细做法' * 200, [recipe('葱油拌面')])
        self.assertNotIn(DINNER, [t.answer for t in memory.turns])
        self.assertIn('老妈蹄花', memory.resolve('3. 进阶大餐', [], self.llm).query)
        memory.clear()
        self.assertTrue(memory.resolve('3. 进阶大餐', [], self.llm).clarification)
        self.assertEqual(memory.menus, [])

    def test_new_recommendation_menu_takes_number_precedence(self):
        self.memory.remember('晚餐吃什么', DINNER, DINNER_DOCS)
        self.memory.remember('重新推荐', '1. 麻婆豆腐\n2. 西红柿炒鸡蛋', self.docs)
        self.assertIn('西红柿炒鸡蛋', self.memory.resolve('第二个怎么做', [], self.llm).query)
        self.assertTrue(self.memory.resolve('第三个', [], self.llm).clarification)
        self.assertIn('老妈蹄花', self.memory.resolve('之前的3. 进阶大餐', [], self.llm).query)

    def test_conflicting_number_and_title_does_not_pin_recent_dish(self):
        self.memory.remember('晚餐吃什么', DINNER, DINNER_DOCS)
        self.memory.remember('第二个', '葱油拌面做法', [recipe('葱油拌面')])
        self.assertTrue(self.memory.resolve('这个2. 进阶大餐', [], self.llm).clarification)
        self.assertTrue(self.memory.resolve('这个：4. 不存在的套餐', [], self.llm).clarification)
        self.assertTrue(self.memory.resolve('这个：2. 不存在的套餐', [], self.llm).clarification)

    def test_combo_detail_does_not_replace_recommendation_numbering(self):
        self.memory.remember('晚餐吃什么', DINNER, DINNER_DOCS)
        self.memory.remember('这个：1. 经典家常组合（推荐）',
            '### 1. 虎皮青椒\n制作方法\n### 2. 肉蛋盖饭\n制作方法', DINNER_DOCS[:2])
        self.assertEqual(len(self.memory.menus), 1)
        self.assertIn('老妈蹄花', self.memory.resolve('第三个怎么做', [], self.llm).query)


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.system = RecipeRAGSystem(RAGConfig())
        self.system.system_ready = True
        self.docs = [recipe('西红柿炒鸡蛋'), recipe('麻婆豆腐')]
        self.system.data_module = Mock(documents=self.docs)
        self.system.generation_module = Mock()
        self.system.retrieve = Mock(return_value=(self.docs, Mock()))

    def test_two_turns_selection_and_generation_history(self):
        self.system.generation_module.generate_list_answer.return_value = '1. 麻婆豆腐\n2. 西红柿炒鸡蛋'
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
        stream = self.system.ask_question('比较这两道菜', stream=True)
        self.assertEqual(len(self.system.conversation.turns), 0)
        self.assertEqual(next(stream), '1. 麻婆')
        self.assertEqual(len(self.system.conversation.turns), 0)
        list(stream)
        self.assertEqual(self.system.conversation.turns[0].choices[2], '西红柿炒鸡蛋')

    def test_interrupted_or_failed_stream_is_not_recorded(self):
        self.system.generation_module.generate_basic_answer_stream.return_value = iter(['hello', 'world'])
        stream = self.system.ask_question('比较这两道菜', stream=True)
        next(stream)
        stream.close()
        self.assertEqual(self.system.conversation.turns, [])
        def broken():
            yield 'partial'
            raise RuntimeError('failed')
        self.system.generation_module.generate_basic_answer_stream.return_value = broken()
        with self.assertRaises(RuntimeError):
            list(self.system.ask_question('比较这两道菜', stream=True))
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

    def test_user_reported_three_turn_flow_retrieves_new_recipe(self):
        self.system.data_module.documents = DINNER_DOCS
        self.system.retrieve.return_value = (DINNER_DOCS, Mock())
        self.system.generation_module.generate_list_answer.return_value = DINNER
        self.system.ask_question('晚餐吃什么')
        self.system.retrieve.return_value = ([recipe('葱油拌面')], Mock())
        self.system.generation_module.generate_step_by_step_answer.return_value = '葱油拌面的详细做法'
        self.system.ask_question('这个： 2. 快手简单晚餐')
        self.assertEqual(self.system.retrieve.call_args.args[0], '葱油拌面怎么做？')
        self.system.retrieve.return_value = ([recipe('老妈蹄花')], Mock())
        self.system.generation_module.generate_step_by_step_answer.return_value = '老妈蹄花的详细做法'
        self.assertIn('老妈蹄花', self.system.ask_question('那这个呢？：3. 进阶大餐'))
        self.assertEqual(self.system.retrieve.call_args.args[0], '老妈蹄花怎么做？')


if __name__ == '__main__':
    unittest.main()
