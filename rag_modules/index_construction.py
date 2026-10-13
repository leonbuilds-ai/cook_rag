"""Qdrant persistence, deterministic IDs and versioned, non-destructive collections."""
import hashlib
import json
import logging
import uuid
from pathlib import Path
from qdrant_client import QdrantClient, models
from langchain_core.documents import Document
from .model_clients import create_embeddings

logger = logging.getLogger(__name__)


class QdrantIndexConstructionModule:
    def __init__(self, config, embeddings=None, client=None):
        self.config = config
        self.embeddings = embeddings or create_embeddings(config)
        self.client = client or QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key or None,
                                            timeout=120, check_compatibility=False)
        self.collection_name = None
        self.vectorstore = self

    def collection_identity(self, chunks, schema=2):
        return {'schema': schema, 'model': self.config.embedding_model,
                    'provider': self.config.embedding_provider, 'url': self.config.embedding_base_url,
                    'num_ctx': self.config.embedding_num_ctx,
                    'chunks': [(d.page_content, d.metadata) for d in chunks]}

    def prepare_collection(self, chunks):
        identity = self.collection_identity(chunks)
        signature = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
        self.collection_name = f'{self.config.qdrant_collection_name}_{signature}'
        self.manifest_path = Path(self.config.index_save_path) / f'{self.collection_name}.json'

    def find_legacy_collection(self, chunks):
        legacy = [Document(page_content=d.page_content, metadata={**d.metadata,
                  'chunk_id':str(uuid.uuid5(uuid.NAMESPACE_URL,f"{d.metadata['parent_id']}:{i}:{d.page_content}"))})
                  for i,d in enumerate(chunks)]
        signature = hashlib.sha256(json.dumps(self.collection_identity(legacy,1),ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:20]
        name=f'{self.config.qdrant_collection_name}_{signature}'
        manifest=Path(self.config.index_save_path)/f'{name}.json'
        if manifest.exists() and self.client.collection_exists(name) and self.client.count(name,exact=True).count==len(chunks):
            return name
        return None

    def load_index(self, chunks):
        self.prepare_collection(chunks)
        if not self.client.collection_exists(self.collection_name) or not self.manifest_path.exists():
            return None
        manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        count = self.client.count(self.collection_name, exact=True).count
        if manifest.get('count') == count == len(chunks):
            logger.info('复用 Qdrant 集合 %s (%s chunks)', self.collection_name, count)
            return self
        return None

    def build_vector_index(self, chunks, reuse_collection=None, progress=None):
        if not chunks:
            raise ValueError('文档块列表不能为空')
        self.prepare_collection(chunks)
        current = {}
        for doc in chunks: current.setdefault((doc.metadata['parent_id'],doc.page_content),[]).append(doc)
        reused = set()
        if reuse_collection and reuse_collection != self.collection_name and self.client.collection_exists(reuse_collection):
            info = self.client.get_collection(reuse_collection)
            if not self.client.collection_exists(self.collection_name):
                self.client.create_collection(self.collection_name, vectors_config=info.config.params.vectors)
            offset = None
            while True:
                points, offset = self.client.scroll(reuse_collection, limit=128, offset=offset, with_vectors=True, with_payload=True)
                copy=[]
                for point in points:
                    if not isinstance(point.vector,list) or not any(point.vector): continue
                    payload=point.payload or {}
                    key=(payload.get('metadata',{}).get('parent_id'),payload.get('page_content'))
                    for doc in current.get(key,[]):
                        cid=doc.metadata['chunk_id']
                        if cid not in reused:
                            copy.append(models.PointStruct(id=cid,vector=point.vector,
                                        payload={'page_content':doc.page_content,'metadata':doc.metadata}))
                            reused.add(cid)
                if copy:
                    self.client.upsert(self.collection_name, points=copy, wait=True)
                    logger.info('复用向量进度: %s/%s',len(reused),len(chunks))
                if offset is None: break
        pending = [d for d in chunks if d.metadata['chunk_id'] not in reused]
        self.last_build = {'reused_chunks':len(reused), 'embedded_chunks':len(pending)}
        for start in range(0, len(pending), self.config.embedding_batch_size):
            batch = pending[start:start + self.config.embedding_batch_size]
            vectors = self.embeddings.embed_documents([d.page_content for d in batch])
            if len(vectors) != len(batch) or not vectors or not vectors[0]:
                raise ValueError('嵌入接口返回数量或维度无效')
            dimension = len(vectors[0])
            if not self.client.collection_exists(self.collection_name):
                self.client.create_collection(self.collection_name,
                    vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE))
            info = self.client.get_collection(self.collection_name)
            if info.config.params.vectors.size != dimension or any(len(v) != dimension for v in vectors):
                raise ValueError('嵌入维度与 Qdrant 集合不一致')
            points = [models.PointStruct(id=d.metadata['chunk_id'], vector=v,
                       payload={'page_content': d.page_content, 'metadata': d.metadata})
                      for d, v in zip(batch, vectors)]
            self.client.upsert(self.collection_name, points=points, wait=True)
            logger.info('向量化进度: %s/%s (复用 %s)', start + len(batch), len(pending), len(reused))
            if progress: progress(start + len(batch), len(pending))
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if self.client.count(self.collection_name, exact=True).count != len(chunks):
            raise ValueError('Qdrant 点数与分块数不一致，索引不能标记为完成')
        temp = self.manifest_path.with_suffix('.tmp')
        temp.write_text(json.dumps({'count': len(chunks)}), encoding='utf-8')
        temp.replace(self.manifest_path)
        return self

    def similarity_search(self, query, k=5, filters=None):
        if not self.collection_name:
            raise ValueError('请先构建或加载索引')
        conditions = []
        for key, value in (filters or {}).items():
            match = models.MatchAny(any=value) if isinstance(value, list) else models.MatchValue(value=value)
            conditions.append(models.FieldCondition(key=f'metadata.{key}', match=match))
        response = self.client.query_points(collection_name=self.collection_name,
            query=self.embeddings.embed_query(query), limit=k, with_payload=True,
            query_filter=models.Filter(must=conditions) if conditions else None)
        return [Document(page_content=p.payload['page_content'], metadata=dict(p.payload['metadata']))
                for p in response.points]

    def get_collection_stats(self):
        return {'collection': self.collection_name,
                'row_count': self.client.count(self.collection_name, exact=True).count if self.collection_name else 0}

    def close(self):
        self.client.close()


IndexConstructionModule = QdrantIndexConstructionModule
