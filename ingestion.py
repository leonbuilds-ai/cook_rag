"""Single-worker background ingestion with a SQL ledger and Redis dispatch queue."""
import logging
import threading
from langchain_core.documents import Document

logger = logging.getLogger(__name__)

class IngestWorker:
    def __init__(self, app, store, redis):
        self.app, self.store, self.redis = app, store, redis
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        if self.redis.ping() != 'ok': return
        # The SQL ledger repairs a crash between commit and dispatch, or after BRPOP.
        for key in self.store.unfinished_jobs():
            self.store.job_progress(key, '等待恢复入库', 0, 'queued')
            self.redis.enqueue(key)
        self.thread = threading.Thread(target=self.run, name='cook-ingest', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=5)

    def run(self):
        while not self.stop.is_set():
            try:
                key = self.redis.take()
                if key: self.process(key)
            except Exception as exc:
                logger.error('Ingest worker failure', extra={'error_type':type(exc).__name__})
                self.stop.wait(2)

    def process(self, key):
        job = self.store.job(None, key)
        if not job or job['status'] != 'queued': return
        while not self.stop.is_set():
            if self.app.state.engine_lock.acquire(timeout=1): break
        else: return
        system = self.app.state.system
        old = {'documents':system.data_module.documents, 'chunks':system.data_module.chunks,
               'parent_child_map':system.data_module.parent_child_map,
               'collection':system.index_module.collection_name,
               'manifest':system.index_module.manifest_path,
               'retrieval':system.retrieval_module, 'router':system.query_router, 'graph':system.graph_rag_retrieval}
        imported = False
        try:
            record = self.store.document(job['document_id'])
            self.store.job_progress(key, '解析与校验食谱', 10)
            doc = Document(page_content=record['content'], metadata=record['meta'])
            self.store.job_progress(key, '提取食材与步骤关系', 20)
            # Graph writes may fail after an earlier transaction has committed.
            imported = True
            system.data_module.upsert_uploaded(doc)
            self.store.job_progress(key, '切块、生成嵌入与构建索引', 30)
            def progress(done, total):
                self.store.job_progress(key, '生成嵌入与写入 Qdrant', 30 + int(55*done/max(total,1)))
            system.build_knowledge_base(reuse_collection=old['collection'], progress=progress)
            self.store.job_progress(key, '更新检索与文档目录', 90)
            self.store.sync_documents(system.data_module.documents)
            self.store.job_progress(key, '入库完成', 100, 'complete')
            self.redis.put('job-status', key, self.store.job(None,key),86400)
        except Exception as exc:
            logger.error('Ingestion failed; restoring previous active index', extra={'error_type':type(exc).__name__})
            if imported:
                try: system.data_module.remove_failed_upload(job['document_id'])
                except Exception: logger.error('Uploaded graph rollback failed')
            system.data_module.documents, system.data_module.chunks = old['documents'], old['chunks']
            system.data_module.parent_child_map = old['parent_child_map']
            system.index_module.collection_name, system.index_module.manifest_path = old['collection'], old['manifest']
            system.retrieval_module, system.query_router, system.graph_rag_retrieval = old['retrieval'],old['router'],old['graph']
            system.system_ready = True
            self.store.job_progress(key, '入库失败', 0, 'failed', '解析或数据库操作失败，请检查服务日志后重试。')
            self.redis.put('job-status', key, self.store.job(None,key),86400)
        finally: self.app.state.engine_lock.release()
