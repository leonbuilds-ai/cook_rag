import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from fastapi.testclient import TestClient
from config import RAGConfig, load_config
from api import create_app, Sessions
from service_config import ServiceConfig
from rag_modules.sources import references, canonical_source
from langchain_core.documents import Document
from evaluate import metrics


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.config = RAGConfig()
        self.settings = ServiceConfig(queue_timeout_seconds=0)
        self.system = Mock(config=self.config)
        self.system.data_module.documents = [Document(page_content='食谱', metadata={})]
        def answer(question, strategy, memory, result_info):
            result_info.update(sources=[], resolved_query=question, kind='answer')
            return '已回答', None
        self.system.ask_question_with_routing.side_effect = answer
        self.app = create_app(self.config, self.settings, lambda cfg: self.system,
                              lambda system: {'neo4j': 'ok', 'qdrant': 'ok'})

    def test_health_ask_request_id_and_session_isolation(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.get('/health').status_code, 200)
            one = client.post('/ask', json={'question': '你好'}).json()
            two = client.post('/ask', json={'question': '你好'}).json()
            self.assertNotEqual(one['session_id'], two['session_id'])
            memories = [call.kwargs['memory'] for call in self.system.ask_question_with_routing.call_args_list]
            self.assertIsNot(memories[0], memories[1])
            response = client.post('/ask', json={'question': '第一个', 'session_id': one['session_id']})
            self.assertEqual(response.headers['X-Request-ID'], response.json()['request_id'])
            self.assertIs(self.system.ask_question_with_routing.call_args.kwargs['memory'], memories[0])
            client.post('/ask', json={'question': '你好', 'session_id': one['session_id'], 'reset': True})
            self.assertIsNot(self.system.ask_question_with_routing.call_args.kwargs['memory'], memories[0])
        self.system.close.assert_called_once()

    def test_invalid_input_not_ready_busy_and_backend_failure(self):
        with TestClient(self.app) as client:
            for payload in ({'question': '  '}, {'question': 'a' * 2001},
                            {'question': '你好', 'strategy': 'bad'}, {'question': '你好', 'session_id': 'bad'}):
                self.assertEqual(client.post('/ask', json=payload).status_code, 422)
            self.app.state.ready = False
            self.assertEqual(client.post('/ask', json={'question': '你好'}).status_code, 503)
            self.app.state.ready = True
            self.app.state.engine_lock.acquire()
            try:
                response = client.post('/ask', json={'question': '你好'})
                self.assertEqual(response.status_code, 429)
                self.assertIn('Retry-After', response.headers)
            finally:
                self.app.state.engine_lock.release()
            self.system.ask_question_with_routing.side_effect = RuntimeError('secret database password')
            response = client.post('/ask', json={'question': '你好'})
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('secret', response.text)
            self.assertFalse(self.app.state.engine_lock.locked())

    def test_auth_and_health_degraded(self):
        app = create_app(self.config, replace(self.settings, api_key='test-key'), lambda cfg: self.system,
                         lambda system: {'neo4j': 'unavailable'})
        with TestClient(app) as client:
            self.assertEqual(client.get('/health').status_code, 503)
            self.assertEqual(client.post('/ask', json={'question': '你好'}).status_code, 401)
            self.assertEqual(client.post('/ask', json={'question': '你好'},
                headers={'Authorization': 'Bearer test-key'}).status_code, 200)

    def test_startup_failure_keeps_health_available(self):
        self.system.initialize_system.side_effect = RuntimeError('credentials')
        with TestClient(self.app) as client:
            self.assertEqual(client.get('/health').status_code, 503)
            self.assertEqual(client.post('/ask', json={'question': '你好'}).status_code, 503)


class ContractTests(unittest.TestCase):
    def test_sources_migration_safe_paths_and_url_encoding(self):
        docs = [Document(page_content='', metadata={'parent_id': '1', 'dish_name': '面',
                 'source': r'E:\Leon\code_c8\data\C8\cook\staple\葱油拌面.md'})]
        refs = references(docs * 2, 'https://example.com/blob/main/')
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]['path'], 'data/cook/staple/葱油拌面.md')
        self.assertIn('%E8', refs[0]['url'])
        self.assertEqual(canonical_source('neo4j:123'), 'neo4j:123')

    def test_metrics_multiple_relevant_and_no_hit(self):
        self.assertEqual(metrics(['x', 'b', 'b'], ['a', 'b']), {'recall_at_5': .5, 'mrr_at_5': .5})
        self.assertEqual(metrics(['x'], ['a']), {'recall_at_5': 0, 'mrr_at_5': 0})
        self.assertEqual(metrics(['x'] * 5 + ['a'], ['a'])['mrr_at_5'], .5)

    def test_session_limit_and_ttl(self):
        sessions = Sessions(ServiceConfig(max_sessions=1, session_ttl_seconds=1), RAGConfig())
        first, memory = sessions.get()
        second, _ = sessions.get()
        self.assertNotIn(first, sessions.items)
        sessions.items[second] = (0, memory)
        _, next_memory = sessions.get(second)
        self.assertIsNot(memory, next_memory)

    def test_config_rejects_unknown_and_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.toml'
            for field in ('unknown = 1', 'neo4j_password = "secret"'):
                path.write_text('[rag]\n' + field, encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_config(path)


if __name__ == '__main__':
    unittest.main()
