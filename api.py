"""Single-process FastAPI service around the existing recipe engine."""
import asyncio
import logging
import secrets
import threading
import time
import uuid
from pathlib import Path
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from config import DEFAULT_CONFIG
from main import RecipeRAGSystem
from rag_modules.conversation import ConversationMemory
from service_config import load_service_config

logger = logging.getLogger('cook_rag.api')


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: uuid.UUID | None = None
    strategy: Literal['auto', 'hybrid_traditional', 'graph_rag', 'combined'] = 'auto'
    reset: bool = False

    @field_validator('question')
    @classmethod
    def meaningful_question(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('问题不能为空')
        return value


class Source(BaseModel):
    id: str
    title: str
    document_id: str
    path: str
    url: str | None = None
    image: dict | None = None


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]
    session_id: uuid.UUID
    resolved_query: str
    retrieval_strategy: str | None
    kind: str
    request_id: str
    elapsed_ms: float


class ServiceError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


class Sessions:
    """Bounded memory with TTL. Accessed only while the engine lock is held."""
    def __init__(self, settings, rag_config):
        self.settings, self.config = settings, rag_config
        self.items = OrderedDict()

    def get(self, session_id=None, reset=False):
        now = time.monotonic()
        expired = [key for key, (stamp, _) in self.items.items()
                   if now - stamp >= self.settings.session_ttl_seconds]
        for key in expired:
            del self.items[key]
        key = str(session_id) if session_id else str(uuid.uuid4())
        existing = self.items.pop(key, None)
        memory = existing[1] if existing and not reset else ConversationMemory(
            self.config.conversation_max_turns, self.config.conversation_max_chars)
        self.items[key] = (now, memory)
        while len(self.items) > self.settings.max_sessions:
            self.items.popitem(last=False)
        return key, memory

    def touch(self, key):
        if key in self.items:
            self.items[key] = (time.monotonic(), self.items[key][1])


def check_dependencies(system):
    checks = {}
    def qdrant_probe():
        with httpx.Client(timeout=3) as client:
            response = client.get(system.config.qdrant_url.rstrip('/') + '/collections/' +
                                  system.index_module.collection_name,
                                  headers={'api-key': system.config.qdrant_api_key} if system.config.qdrant_api_key else {})
            response.raise_for_status()
            probe = client.post(system.config.qdrant_url.rstrip('/') + '/collections/' + system.index_module.collection_name + '/points/scroll',
                                json={'limit':1,'with_payload':True,'with_vector':True},
                                headers={'api-key':system.config.qdrant_api_key} if system.config.qdrant_api_key else {})
            probe.raise_for_status()
            points = probe.json()['result']['points']
            if not points or not points[0].get('payload') or not any(points[0].get('vector',[])):
                raise ValueError('Qdrant data is not readable')
    def neo4j_probe():
        from neo4j import Query
        with system.data_module.driver.session(database=system.config.neo4j_database) as session:
            session.run(Query('RETURN 1 AS ok', timeout=3)).consume()
    for name, probe in [('neo4j', neo4j_probe), ('qdrant', qdrant_probe)]:
        try:
            probe()
            checks[name] = 'ok'
        except Exception:
            checks[name] = 'unavailable'
    # Native local Ollama: check installed model names, without triggering inference.
    for kind in ('embedding', 'llm'):
        endpoint = getattr(system.config, f'{kind}_base_url')
        parsed = urlparse(endpoint)
        if parsed.hostname in {'localhost', '127.0.0.1', '::1'} and parsed.port == 11434:
            try:
                base = endpoint.rstrip('/').removesuffix('/v1')
                with httpx.Client(timeout=3) as client:
                    response = client.get(base + '/api/tags')
                    response.raise_for_status()
                models = {item['name'] for item in response.json()['models']}
                checks[kind] = 'ok' if getattr(system.config, f'{kind}_model') in models else 'model_missing'
            except Exception:
                checks[kind] = 'unavailable'
        else:
            checks[kind] = 'configured'  # remote inference is verified by /ask, not a paid health probe
    return checks


def create_app(config=None, settings=None, system_factory=RecipeRAGSystem, dependency_probe=check_dependencies):
    config = config or DEFAULT_CONFIG
    settings = settings or load_service_config()

    @asynccontextmanager
    async def lifespan(app):
        app.state.ready = False
        system = None
        try:
            app.state.chat_store.initialize()
            if settings.database_url:
                migration = app.state.chat_store.migrate_sqlite(settings.history_db_path)
                logger.info('History migration complete', extra={'migration_counts':migration})
            app.state.chat_store.recover_interrupted()
            system = system_factory(config)
            app.state.system = system
            await asyncio.to_thread(system.initialize_system)
            await asyncio.to_thread(system.build_knowledge_base)
            if settings.database_url:
                await asyncio.to_thread(app.state.chat_store.sync_documents, system.data_module.documents)
            if config.rerank_enabled:
                try: await asyncio.to_thread(system.reranker.load)
                except Exception as exc: logger.warning('Reranker startup fallback',extra={'error_type':type(exc).__name__})
            app.state.ready = True
            from ingestion import IngestWorker
            app.state.ingest_worker = IngestWorker(app, app.state.chat_store, app.state.redis)
            await asyncio.to_thread(app.state.ingest_worker.start)
            logger.info('Knowledge base ready')
        except Exception as exc:
            logger.error('Startup failed', extra={'error_type': type(exc).__name__})
        try:
            yield
        finally:
            app.state.ready = False
            if hasattr(app.state,'ingest_worker'): await asyncio.to_thread(app.state.ingest_worker.close)
            if system is not None:
                await asyncio.to_thread(system.close)
            app.state.chat_store.close()
            app.state.redis.close()

    app = FastAPI(title='Cook RAG', version='1.0.0', lifespan=lifespan)
    app.state.ready = False
    app.state.instance_id = str(uuid.uuid4())
    app.state.engine_lock = threading.Lock()
    app.state.sessions = Sessions(settings, config)
    web_root = Path(__file__).resolve().parent / 'web'
    app.mount('/assets', StaticFiles(directory=web_root), name='assets')

    @app.get('/', include_in_schema=False)
    def website():
        return FileResponse(web_root / 'index.html', headers={
            'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'same-origin',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"})

    def authenticate(authorization):
        if settings.api_key and not secrets.compare_digest((authorization or '').encode('utf-8'),
                                                          ('Bearer ' + settings.api_key).encode('utf-8')):
            raise ServiceError(401, 'unauthorized', '需要有效的 Bearer API key。')

    @app.middleware('http')
    async def request_context(request, call_next):
        request.state.request_id = str(uuid.uuid4())
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error('Unhandled request failure', extra={'request_id': request.state.request_id,
                                                             'error_type': type(exc).__name__})
            response = failure(request, 500, 'internal_error', '服务内部错误，请提供 request_id 排查。')
        response.headers['X-Request-ID'] = request.state.request_id
        logger.info('HTTP request', extra={'request_id': request.state.request_id,
            'method': request.method, 'path': request.url.path, 'status': response.status_code,
            'duration_ms': round((time.perf_counter() - started) * 1000, 2)})
        return response

    def failure(request, status, code, message):
        return JSONResponse(status_code=status, content={'error': {'code': code, 'message': message},
            'request_id': request.state.request_id}, headers={'Retry-After': '2'} if status == 429 else None)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return failure(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Pydantic error input can contain the full prompt: do not return or log it.
        return failure(request, 422, 'invalid_request', '请检查 question、session_id 和 strategy。')

    @app.get('/health')
    def health():
        if not app.state.ready:
            return JSONResponse(status_code=503, content={'status': 'not_ready', 'dependencies': {}})
        checks = dependency_probe(app.state.system)
        try: checks[app.state.chat_store.backend] = app.state.chat_store.ping()
        except Exception: checks[app.state.chat_store.backend] = 'unavailable'
        checks['redis'] = app.state.redis.ping()
        if config.rerank_enabled: checks['reranker'] = 'ok' if app.state.system.reranker.status == 'ready' else 'configured'
        ready = all(value in {'ok', 'configured', 'disabled'} for value in checks.values())
        return JSONResponse(status_code=200 if ready else 503, content={
            'status': 'ready' if ready else 'degraded', 'dependencies': checks,
            'recipes': len(app.state.system.data_module.documents),
            'retrieval_mode': config.retrieval_mode, 'auth_required': bool(settings.api_key),
            'instance_id': app.state.instance_id, 'session_ttl_seconds': settings.session_ttl_seconds})

    @app.get('/recipes/{document_id}')
    def recipe(document_id: str, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        if not app.state.ready:
            raise ServiceError(503, 'not_ready', '食谱暂时不可用，请稍后重试。')
        from rag_modules.sources import references
        for doc in app.state.system.data_module.documents:
            if doc.metadata.get('parent_id') == document_id:
                return {'title': doc.metadata.get('dish_name', '食谱'), 'content': doc.page_content,
                        'source': references([doc], config.source_base_url)[0],
                        'display_content': app.state.recipe_media.rewrite(doc.metadata.get('source', ''), doc.page_content),
                        'image': app.state.recipe_media.describe(doc.metadata.get('source', ''))}
        raise ServiceError(404, 'recipe_not_found', '没有找到这份食谱。')

    @app.post('/ask', response_model=AskResponse)
    def ask(body: AskRequest, request: Request, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        if not app.state.ready:
            raise ServiceError(503, 'not_ready', '知识库未就绪，请检查 /health 和服务日志。')
        started = time.perf_counter()
        lock = app.state.engine_lock
        if not lock.acquire(timeout=settings.queue_timeout_seconds):
            raise ServiceError(429, 'busy', '服务正在处理其他问题，请稍后重试。')
        try:
            session_id, memory = app.state.sessions.get(body.session_id, body.reset)
            info = {}
            answer, analysis = app.state.system.ask_question_with_routing(
                body.question, strategy=body.strategy, memory=memory, result_info=info)
            app.state.sessions.touch(session_id)
            return AskResponse(answer=answer, sources=info['sources'], session_id=session_id,
                resolved_query=info['resolved_query'], kind=info['kind'],
                retrieval_strategy=analysis.recommended_strategy.value if analysis else None,
                request_id=request.state.request_id, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
        except ServiceError:
            raise
        except Exception as exc:
            logger.error('Backend request failed', extra={'request_id': request.state.request_id,
                                                        'error_type': type(exc).__name__})
            raise ServiceError(502, 'backend_error', '数据库或模型请求失败，请稍后重试。') from None
        finally:
            lock.release()

    from web_routes import register_web_routes
    register_web_routes(app, config, settings, authenticate, ServiceError, AskRequest)
    return app


app = create_app()
