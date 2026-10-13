"""Namespaced cache and durable-job dispatch; cache failure never fabricates a hit."""
import hashlib
import json
import logging
from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

class RedisServices:
    queue = 'cook-rag:v2:ingest'

    def __init__(self, url='', client=None):
        self.client = client or (Redis.from_url(url, decode_responses=True, socket_connect_timeout=2,
                                               socket_timeout=5) if url else None)
        self.hits = self.misses = self.errors = 0

    def ping(self):
        if not self.client: return 'disabled'
        try: self.client.ping(); return 'ok'
        except RedisError: return 'unavailable'

    def key(self, kind, identity):
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return f'cook-rag:v2:{kind}:{digest}'

    def get(self, kind, identity):
        if not self.client: return None
        try:
            raw = self.client.get(self.key(kind, identity))
            if raw is not None: self.hits += 1; return json.loads(raw)
            self.misses += 1
        except (RedisError, ValueError): self.errors += 1
        return None

    def put(self, kind, identity, value, ttl=3600):
        if self.client:
            try: self.client.setex(self.key(kind, identity), ttl, json.dumps(value, ensure_ascii=False))
            except RedisError: self.errors += 1

    def enqueue(self, job_id):
        if not self.client: raise RuntimeError('Redis is not configured')
        self.client.lpush(self.queue, job_id)

    def take(self):
        if not self.client: return None
        row = self.client.brpop(self.queue, timeout=1)
        return row[1] if row else None

    def close(self):
        if self.client: self.client.close()

class CachedEmbeddings:
    def __init__(self, base, cache, identity):
        self.base, self.cache, self.identity = base, cache, identity

    def _identity(self, text): return {**self.identity, 'text':text}

    def embed_documents(self, texts):
        values = [self.cache.get('embedding', self._identity(text)) for text in texts]
        missing = [i for i, value in enumerate(values) if value is None]
        if missing:
            vectors = self.base.embed_documents([texts[i] for i in missing])
            if len(vectors) != len(missing): raise ValueError('Embedding result count mismatch')
            for i, vector in zip(missing, vectors):
                values[i] = vector; self.cache.put('embedding', self._identity(texts[i]), vector, 7*86400)
        return values

    def embed_query(self, text):
        identity = {**self._identity(text), 'purpose':'query'}
        value = self.cache.get('embedding', identity)
        if value is None:
            value = self.base.embed_query(text); self.cache.put('embedding', identity, value, 86400)
        return value
