import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from fastapi.testclient import TestClient
from langchain_core.documents import Document
from api import create_app
from config import RAGConfig
from service_config import ServiceConfig
from rag_modules.sources import references
from recipe_media import RecipeMedia


class FakeSystem:
    def __init__(self, config):
        self.config = config
        docs = [Document(page_content=f'# 早餐{i}\n步骤原文', metadata={'dish_name': f'早餐{i}',
                'parent_id': str(i), 'category': '早餐', 'source': f'data/cook/{i}.md'}) for i in range(1, 7)]
        self.data_module = SimpleNamespace(documents=docs)

    def initialize_system(self): pass
    def build_knowledge_base(self): pass
    def close(self): pass

    def ask_question_with_routing(self, question, stream, strategy, memory, result_info, exclude_ids=None):
        docs = self.data_module.documents
        if question == '早餐吃什么':
            chosen = [d for d in docs if d.metadata['parent_id'] not in (exclude_ids or [])][:3]
            answer = '\n'.join(f"{i}. {d.metadata['dish_name']}" for i, d in enumerate(chosen, 1))
            result_info.update(sources=references(chosen), resolved_query=question, kind='recommendation')
            memory.remember(question, answer, chosen)
            return answer, None
        resolved = memory.resolve(question, [d.metadata['dish_name'] for d in docs], Mock())
        result_info.update(sources=[], resolved_query=resolved.query, kind='answer')
        def tokens():
            yield resolved.query
            time.sleep(.01)
            yield '：做法正文'
            memory.remember(question, resolved.query + '：做法正文', docs[:1])
        return tokens(), None


def events(response):
    return [(block.splitlines()[0].removeprefix('event: '), json.loads(block.splitlines()[1][6:]))
            for block in response.text.split('\n\n') if block.startswith('event:')]


class WebRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = RAGConfig(data_path=str(self.root / 'cook'))
        self.settings = ServiceConfig(history_db_path=str(self.root / 'history.sqlite3'))
        self.app = self.make_app()

    def make_app(self):
        return create_app(self.config, self.settings, FakeSystem, lambda s: {'neo4j': 'ok'})

    def test_home_suggestions_respect_pool_and_exclusions(self):
        with TestClient(self.app) as client:
            docs=self.app.state.system.data_module.documents
            for index in range(6):
                docs[index].metadata['difficulty']='简单'
                docs.append(Document(page_content='清淡的煮汤', metadata={'dish_name':f'汤{index}',
                    'parent_id':f'soup{index}','category':'汤品','source':f'data/cook/soup{index}.md'}))
            first=client.post('/suggestions',json={}).json()['suggestions']
            excluded=[item['document_id'] for item in first]
            second=client.post('/suggestions',json={'exclude_ids':excluded}).json()['suggestions']
            self.assertEqual(len(first),3); self.assertEqual(len(second),3)
            self.assertFalse(set(excluded)&{item['document_id'] for item in second})
            self.assertEqual({item['topic'] for item in second},{'breakfast','light','quick'})
            docs.append(Document(page_content='简单饮品',metadata={'dish_name':'饮品测试','parent_id':'drink0',
                'category':'饮品','difficulty':'简单','source':'data/cook/drink0.md'}))
            exhausted=client.post('/suggestions',json={'exclude_ids':[doc.metadata['parent_id'] for doc in docs if doc.metadata['parent_id']!='drink0']}).json()
            quick=next(item for item in exhausted['suggestions'] if item['topic']=='quick')
            self.assertNotEqual(quick['document_id'],'drink0')
            self.assertIn('quick',exhausted['reset_topics'])

    def test_new_chat_recommendation_excludes_browser_previous_batch(self):
        with TestClient(self.app) as client:
            chat=client.post('/chats').json()
            first=events(client.post('/ask/stream',json={'chat_id':chat['id'],'question':'早餐吃什么'}))[-1][1]['sources']
            chat2=client.post('/chats').json()
            second=events(client.post('/ask/stream',json={'chat_id':chat2['id'],'question':'早餐吃什么',
                'exclude_ids':[source['document_id'] for source in first]}))[-1][1]['sources']
            self.assertFalse({source['document_id'] for source in first}&{source['document_id'] for source in second})

    def test_sse_deltas_persist_and_menu_survives_restart(self):
        with TestClient(self.app) as client:
            chat = client.post('/chats').json()
            token = client.cookies.get('cook_browser')
            response = client.post('/ask/stream', json={'chat_id': chat['id'], 'question': '早餐吃什么'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/event-stream', response.headers['content-type'])
            records = events(response)
            self.assertEqual(records[-1][0], 'done')
            first = records[-1][1]['sources']
            second = events(client.post('/ask/stream', json={'chat_id': chat['id'], 'question': '早餐吃什么', 'topic': 'breakfast'}))[-1][1]['sources']
            self.assertFalse({s['document_id'] for s in first} & {s['document_id'] for s in second})
            stored = client.get('/chats/' + chat['id']).json()
            self.assertEqual(len(stored['messages']), 4)
            self.assertTrue(all(m['status'] == 'complete' for m in stored['messages']))
        with TestClient(self.make_app()) as client:
            client.cookies.set('cook_browser', token)
            response = client.post('/ask/stream', json={'chat_id': chat['id'], 'question': '第一个怎么做'})
            records = events(response)
            self.assertEqual(sum(name == 'delta' for name, _ in records), 2)
            self.assertIn('早餐4', records[-1][1]['resolved_query'])

    def test_cookie_ownership_rename_soft_delete_and_restore(self):
        with TestClient(self.app) as owner, TestClient(self.make_app()) as other:
            chat = owner.post('/chats').json()
            self.assertEqual(other.get('/chats/' + chat['id']).status_code, 404)
            self.assertEqual(other.delete('/chats/' + chat['id']).status_code, 404)
            self.assertEqual(other.post('/chats/' + chat['id'] + '/stop').status_code, 404)
            self.assertEqual(owner.patch('/chats/' + chat['id'], json={'title': '我的早餐'}).status_code, 200)
            self.assertEqual(owner.get('/chats').json()['chats'][0]['title'], '我的早餐')
            owner.delete('/chats/' + chat['id'])
            self.assertEqual(owner.get('/chats').json()['chats'], [])
            self.assertEqual(owner.get('/chats?trash=true').json()['chats'][0]['id'], chat['id'])
            self.assertEqual(owner.get('/chats/' + chat['id']).status_code, 404)
            owner.post('/chats/' + chat['id'] + '/restore')
            self.assertEqual(owner.get('/chats/' + chat['id']).status_code, 200)

    def test_stream_failure_is_recorded_and_lock_is_released(self):
        with TestClient(self.app) as client:
            chat = client.post('/chats').json()
            def broken(*args, **kwargs): raise RuntimeError('secret')
            self.app.state.system.ask_question_with_routing = broken
            response = client.post('/ask/stream', json={'chat_id': chat['id'], 'question': '你好'})
            self.assertEqual(events(response)[-1][0], 'error')
            self.assertNotIn('secret', response.text)
            self.assertFalse(self.app.state.engine_lock.locked())
            self.assertEqual(client.get('/chats/' + chat['id']).json()['messages'][-1]['status'], 'error')

    def test_legacy_import_keeps_menu_and_startup_marks_interruption(self):
        with TestClient(self.app) as client:
            chat = client.post('/chats/import', json={'turns': [{'question': '早餐吃什么',
                'answer': '1. 早餐1\n2. 早餐2', 'document_ids': ['1', '2', 'untrusted-id']}]}).json()
            self.assertEqual(len(chat['messages'][1]['payload']['sources']), 2)
            reply = events(client.post('/ask/stream', json={'chat_id': chat['id'], 'question': '2'}))[-1][1]
            self.assertIn('早餐2', reply['resolved_query'])
            cookie = client.cookies.get('cook_browser')
            import hashlib
            owner = hashlib.sha256(cookie.encode()).hexdigest()
            _, answer_id = self.app.state.chat_store.begin(owner, chat['id'], '未完成的问题')
        with TestClient(self.make_app()) as client:
            client.cookies.set('cook_browser', cookie)
            messages = client.get('/chats/' + chat['id']).json()['messages']
            self.assertEqual(messages[-1]['id'], answer_id)
            self.assertEqual(messages[-1]['status'], 'interrupted')


class RecipeMediaTests(unittest.TestCase):
    def test_chinese_relative_images_and_path_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'cook'
            path = root / '汤'
            path.mkdir(parents=True)
            image = path / '成品.png'
            image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jY2QAAAAASUVORK5CYII='))
            recipe = path / '测试汤.md'
            recipe.write_text('# 测试汤\n![成品](成品.png)', encoding='utf-8')
            media = RecipeMedia(root, Path(folder)/'generated')
            source = 'data/cook/汤/测试汤.md'
            self.assertEqual(media.describe(source)['kind'], 'original')
            self.assertIn('/recipe-media/', media.rewrite(source, recipe.read_text(encoding='utf-8')))
            self.assertEqual(media.public_file('汤/成品.png'), image)
            self.assertIsNone(media.public_file('../private.png'))
            self.assertIsNone(media.public_file('汤/测试汤.md'))
            self.assertIsNone(media.local_image(path, 'https://example.com/a.png'))
            image.write_bytes(b'')
            self.assertEqual(media.describe(source)['kind'], 'missing')
            self.assertIsNone(media.public_file('汤/成品.png'))
            image.write_text('version https://git-lfs.github.com/spec/v1', encoding='utf-8')
            self.assertEqual(media.describe(source)['kind'], 'missing')


if __name__ == '__main__': unittest.main()
