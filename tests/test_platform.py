import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from redis.exceptions import ConnectionError
from langchain_core.documents import Document
from fastapi.testclient import TestClient
from config import RAGConfig
from api import create_app
from service_config import ServiceConfig
from storage import SQLStore
from redis_services import RedisServices, CachedEmbeddings
from rag_modules.reranking import CrossEncoderReranker
from evaluate_answers import score_sample, METRICS
from test_web_routes import FakeSystem

class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'history.sqlite3'
        self.store=SQLStore(self.path); self.store.initialize(); self.addCleanup(self.store.close)

    def test_documents_jobs_and_evaluation_cache(self):
        body='# 示例\n## 食材\n- 豆腐\n## 步骤\n1. 煮熟。'
        job=self.store.submit('a'*64,'示例',body,'汤品')
        self.assertIsNone(self.store.job('b'*64,job['id']))
        self.assertEqual(self.store.submit('a'*64,'示例',body,'汤品')['id'],job['id'])
        self.store.job_progress(job['id'],'完成',100,'complete')
        self.assertTrue(self.store.submit('a'*64,'示例',body,'汤品')['duplicate'])
        scorer=Mock(return_value={k:.75 for k in METRICS})
        sample={'response':'有依据的回答','reference':'人工答案'}
        self.assertFalse(score_sample(sample,{'model':'m1'},self.store,scorer)[1])
        self.assertTrue(score_sample(sample,{'model':'m1'},self.store,scorer)[1])
        self.assertEqual(scorer.call_count,1)
        self.assertFalse(score_sample(sample,{'model':'m2'},self.store,scorer)[1])

    def test_real_pairwise_rerank_and_failure_preserves_candidates(self):
        config=RAGConfig(rerank_enabled=True)
        model=Mock(); model.predict.return_value=[-.1,1.4]
        ranker=CrossEncoderReranker(config,model)
        docs=[Document(page_content=name,metadata={'parent_id':str(i),'dish_name':name,'category':'早餐'})
              for i,name in enumerate(['粥','鸡蛋饼'])]
        ranked,trace=ranker.rerank('鸡蛋早餐',docs,1)
        self.assertTrue(trace['applied']); self.assertEqual(ranked[0].page_content,'鸡蛋饼')
        self.assertEqual(ranked[0].metadata['category'],'早餐')
        model.predict.return_value=[float('nan'),0]
        ranked,trace=ranker.rerank('鸡蛋早餐',docs,1)
        self.assertFalse(trace['applied']); self.assertEqual(ranked[0].page_content,'粥')

    def test_embedding_cache_reuses_vectors_and_redis_failure_falls_back(self):
        client=Mock(); values={}
        client.get.side_effect=lambda key:values.get(key)
        client.setex.side_effect=lambda key,ttl,value:values.__setitem__(key,value)
        cache=RedisServices(client=client); base=Mock()
        base.embed_documents.return_value=[[1.,0.]]
        wrapped=CachedEmbeddings(base,cache,{'model':'m1'})
        self.assertEqual(wrapped.embed_documents(['豆腐']),[[1.,0.]])
        wrapped.embed_documents(['豆腐']); self.assertEqual(base.embed_documents.call_count,1)
        client.get.side_effect=ConnectionError('offline')
        wrapped.embed_documents(['豆腐']); self.assertEqual(base.embed_documents.call_count,2)
        self.assertNotEqual(cache.key('retrieval',{'collection':'a'}),cache.key('retrieval',{'collection':'b'}))

    def test_upload_validation_owner_isolation_and_duplicate(self):
        settings=ServiceConfig(history_db_path=str(self.path))
        app=create_app(RAGConfig(),settings,FakeSystem,lambda _: {'neo4j':'ok'})
        with TestClient(app) as client:
            app.state.redis.ping=Mock(return_value='ok'); app.state.redis.enqueue=Mock()
            data=b'# example\n## '+ '食材'.encode()+b'\n- tofu\n## '+ '步骤'.encode()+b'\n1. cook\n'
            response=client.post('/documents/upload',files={'file':('recipe.md',data)},data={'category':'汤品'})
            self.assertEqual(response.status_code,202); job=response.json()
            self.assertEqual(client.get('/jobs/'+job['id']).status_code,200)
            with TestClient(create_app(RAGConfig(),settings,FakeSystem,lambda _: {'neo4j':'ok'})) as stranger:
                self.assertEqual(stranger.get('/jobs/'+job['id']).status_code,404)
            self.assertEqual(client.post('/documents/upload',files={'file':('x.pdf',data)}).status_code,422)
            self.assertEqual(client.post('/documents/upload',files={'file':('x.md',b'\xff\xfe')}).status_code,422)
            self.assertEqual(client.post('/documents/upload',files={'file':('x.md',b'no sections')}).status_code,422)
            self.assertEqual(client.post('/documents/upload',files={'file':('x.md',data)},data={'category':'invalid'}).status_code,422)
            app.state.redis.ping.return_value='unavailable'
            self.assertEqual(client.post('/documents/upload',files={'file':('x.md',data)}).status_code,503)

    def test_failed_ingestion_restores_old_index_and_releases_lock(self):
        import threading
        from ingestion import IngestWorker
        job=self.store.submit('a'*64,'示例','# 示例\n## 食材\n- 豆腐\n## 步骤\n1. 煮熟','汤品')
        data=SimpleNamespace(documents=['original'],chunks=['original_chunk'],parent_child_map={},
                             upsert_uploaded=Mock(),remove_failed_upload=Mock())
        index=SimpleNamespace(collection_name='original_collection',manifest_path='original_manifest')
        system=SimpleNamespace(data_module=data,index_module=index,retrieval_module='r',query_router='q',graph_rag_retrieval='g',
                               build_knowledge_base=Mock(side_effect=RuntimeError('failed')))
        app=SimpleNamespace(state=SimpleNamespace(system=system,engine_lock=threading.Lock()))
        IngestWorker(app,self.store,Mock()).process(job['id'])
        self.assertEqual(self.store.job('a'*64,job['id'])['status'],'failed')
        self.assertEqual(index.collection_name,'original_collection')
        self.assertEqual(data.documents,['original'])
        self.assertFalse(app.state.engine_lock.locked())
        data.remove_failed_upload.assert_called_once_with(job['document_id'])

        # A graph import can partially commit before raising; clean that upload
        # too, without replacing the previously working collection.
        job2=self.store.submit('a'*64,'示例二','# 示例二\n## 食材\n- 豆腐\n## 步骤\n1. 煮熟','汤品')
        data.upsert_uploaded.side_effect=RuntimeError('partial graph write')
        data.remove_failed_upload.reset_mock()
        IngestWorker(app,self.store,Mock()).process(job2['id'])
        data.remove_failed_upload.assert_called_once_with(job2['document_id'])
        self.assertEqual(self.store.job('a'*64,job2['id'])['status'],'failed')
        self.assertEqual(index.collection_name,'original_collection')
        self.assertFalse(app.state.engine_lock.locked())

if __name__=='__main__': unittest.main()
