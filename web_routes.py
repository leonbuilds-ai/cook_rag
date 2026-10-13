"""Persistent browser chats, cancellable SSE generation and safe recipe media."""
import asyncio
import hashlib
import json
import logging
import queue
import re
import secrets
import threading
import time
import random
from typing import Literal
from uuid import UUID
from fastapi import Header, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from chat_store import ChatStore
from recipe_media import RecipeMedia
from rag_modules.conversation import ConversationMemory
from rag_modules.dialogue_intent import is_recommendation
from rag_modules.dialogue_intent import direct_intent
from rag_modules.sources import references
from rag_modules.recipe_preferences import light_evidence
from rag_modules.cancellation import stop_signal

logger = logging.getLogger('cook_rag.web')
COOKIE = 'cook_browser'


def menu_source_history(messages):
    question = ''
    for message in messages:
        if message['role'] == 'user':
            question = message['content']
        elif message['role'] == 'assistant' and message['status'] == 'complete':
            names = {source['title'] for source in message['payload'].get('sources', []) if source.get('title')}
            if names:
                yield question, message['content'].split('\n\n文档来源：')[0], names, {
                    source['id']:source['title'] for source in message['payload']['sources'] if source.get('id')}


def register_web_routes(app, config, settings, authenticate, Error, AskModel):
    store = ChatStore(settings.history_db_path, settings.database_url)
    media = RecipeMedia(config.data_path)
    app.state.chat_store = store
    app.state.recipe_media = media
    app.state.active_chats = set()
    app.state.stream_cancellations = {}
    active_lock = threading.Lock()
    from redis_services import RedisServices
    app.state.redis = RedisServices(settings.redis_url)

    class StreamAsk(AskModel):
        chat_id: UUID
        topic: Literal['breakfast', 'light', 'quick', 'dinner', 'drinks'] | None = None
        exclude_ids: list[str] = Field(default_factory=list, max_length=500)

    class SuggestionRequest(BaseModel):
        exclude_ids: list[str] = Field(default_factory=list, max_length=500)

    @app.post('/suggestions')
    def suggestions(body: SuggestionRequest, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        if not app.state.ready: raise Error(503, 'not_ready', '食谱助手暂时未就绪。')
        docs = app.state.system.data_module.documents
        exclusions = set(body.exclude_ids)
        selected, resets = [], []
        for topic in ('breakfast', 'light', 'quick'):
            pool = [doc for doc in docs if
                    (topic == 'breakfast' and doc.metadata.get('category') == '早餐') or
                    (topic == 'light' and light_evidence(doc)) or
                    (topic == 'quick' and '简单' in doc.metadata.get('difficulty', '')
                     and doc.metadata.get('category') in {'早餐','主食','素菜','荤菜','汤品','水产'})]
            chosen_ids = {item['document_id'] for item in selected}
            pool = [doc for doc in pool if doc.metadata['parent_id'] not in chosen_ids]
            available = [doc for doc in pool if doc.metadata['parent_id'] not in exclusions]
            if not available:
                available = pool
                if pool: resets.append(topic)
            if available:
                doc = random.SystemRandom().choice(available)
                selected.append({**references([doc])[0], 'topic':topic,
                                 'question':doc.metadata['dish_name']+'怎么做'})
        return {'suggestions':selected, 'reset_topics':resets}

    class EditChat(BaseModel):
        title: str = Field(min_length=1, max_length=60)

    class ImportTurn(BaseModel):
        question: str = Field(min_length=1, max_length=2000)
        answer: str = Field(min_length=1, max_length=20000)
        document_ids: list[str] = Field(default_factory=list, max_length=10)

    class ImportChat(BaseModel):
        turns: list[ImportTurn] = Field(min_length=1, max_length=30)

    def owner(request, response=None):
        token = request.cookies.get(COOKIE, '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            token = secrets.token_urlsafe(32)
            if response is None:
                raise Error(404, 'chat_not_found', '找不到这段对话，请开启新对话。')
            response.set_cookie(COOKIE, token, httponly=True, samesite='strict',
                                secure=request.url.scheme == 'https', max_age=31536000)
        return hashlib.sha256(token.encode()).hexdigest()

    def require_chat(request, chat_id, deleted=False):
        user = owner(request)
        chat = store.get(user, str(chat_id), deleted)
        if not chat:
            raise Error(404, 'chat_not_found', '找不到这段对话。')
        return user, chat

    def ensure_idle(chat_id):
        with active_lock:
            if str(chat_id) in app.state.active_chats:
                raise Error(409, 'chat_busy', '请先停止这段对话的回答，再进行操作。')

    @app.get('/chats')
    def list_chats(request: Request, response: Response, trash: bool = False,
                   authorization: str | None = Header(default=None)):
        authenticate(authorization)
        return {'chats': store.list(owner(request, response), trash)}

    @app.post('/chats', status_code=201)
    def create_chat(request: Request, response: Response, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        return store.create(owner(request, response))

    @app.post('/chats/import', status_code=201)
    def import_chat(body: ImportChat, request: Request, response: Response,
                    authorization: str | None = Header(default=None)):
        """One-time migration of the old tab-local chat; only import into this browser's own history."""
        authenticate(authorization)
        if not app.state.ready:
            raise Error(503, 'not_ready', '请等待食谱助手就绪后导入旧对话。')
        user = owner(request, response)
        chat = store.create(user)
        memory = ConversationMemory(config.conversation_max_turns, config.conversation_max_chars)
        for turn in body.turns:
            docs = [d for d in app.state.system.data_module.documents
                    if d.metadata.get('parent_id') in turn.document_ids]
            info = {'sources': references(docs, config.source_base_url), 'resolved_query': turn.question,
                    'kind': direct_intent(turn.question) or ('recommendation' if is_recommendation(turn.question) else 'answer')}
            if not direct_intent(turn.question):
                memory.remember(turn.question, turn.answer.split('\n\n文档来源：')[0], docs)
            decorate(info)
            _, answer_id = store.begin(user, chat['id'], turn.question)
            store.finish(user, chat['id'], answer_id, turn.answer, 'complete', info, memory.snapshot(), {})
        return store.get(user, chat['id'])

    @app.get('/chats/{chat_id}')
    def get_chat(chat_id: UUID, request: Request, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        chat = require_chat(request, chat_id)[1]
        for message in chat['messages']:
            if message['role'] == 'assistant':
                decorate(message['payload'])
        with active_lock:
            chat['generating'] = str(chat_id) in app.state.active_chats
        return chat

    @app.patch('/chats/{chat_id}')
    def rename_chat(chat_id: UUID, body: EditChat, request: Request,
                    authorization: str | None = Header(default=None)):
        authenticate(authorization)
        user, _ = require_chat(request, chat_id)
        ensure_idle(chat_id)
        title = body.title.strip()
        if not title:
            raise Error(422, 'invalid_title', '对话标题不能为空。')
        store.update(user, str(chat_id), title=title)
        return {'ok': True}

    @app.delete('/chats/{chat_id}')
    def delete_chat(chat_id: UUID, request: Request, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        user, _ = require_chat(request, chat_id)
        ensure_idle(chat_id)
        store.update(user, str(chat_id), deleted=True)
        return {'ok': True, 'recoverable': True}

    @app.post('/chats/{chat_id}/restore')
    def restore_chat(chat_id: UUID, request: Request, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        user, _ = require_chat(request, chat_id, deleted=True)
        store.update(user, str(chat_id), deleted=False)
        return {'ok': True}

    @app.post('/chats/{chat_id}/stop')
    def stop_chat(chat_id: UUID, request: Request, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        require_chat(request, chat_id)
        with active_lock:
            signal = app.state.stream_cancellations.get(str(chat_id))
            if signal:
                signal.set()
        return {'ok': True}

    @app.get('/recipe-media/{relative:path}', include_in_schema=False)
    def recipe_image(relative: str):
        path = media.public_file(relative)
        if not path:
            raise Error(404, 'image_not_found', '图片不存在。')
        return FileResponse(path, headers={'X-Content-Type-Options': 'nosniff',
                                           'Cache-Control': 'public, max-age=86400'})

    def decorate(info):
        info['sources'] = [{**source, 'image': media.describe(source['path'])}
                           for source in info.get('sources', [])]

    def event(name, data):
        return f'event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'

    @app.post('/ask/stream')
    async def stream_ask(body: StreamAsk, request: Request,
                         authorization: str | None = Header(default=None)):
        authenticate(authorization)
        if not app.state.ready:
            raise Error(503, 'not_ready', '食谱助手暂时未就绪。')
        user, chat = require_chat(request, body.chat_id)
        chat_id = str(body.chat_id)
        with active_lock:
            if chat_id in app.state.active_chats:
                raise Error(409, 'chat_busy', '这段对话正在回答中。')
            app.state.active_chats.add(chat_id)
        output = queue.Queue(maxsize=64)
        cancelled = threading.Event()
        with active_lock:
            app.state.stream_cancellations[chat_id] = cancelled
        request_id = request.state.request_id

        def emit(name, data):
            while not cancelled.is_set():
                try:
                    output.put((name, data), timeout=.2)
                    return True
                except queue.Full:
                    continue
            return False

        def worker():
            cancel_token = stop_signal.set(cancelled)
            lock = app.state.engine_lock
            acquired = False
            answer_id = None
            parts = []
            info = {'sources': [], 'resolved_query': body.question, 'kind': 'answer'}
            info['topic'] = body.topic
            iterator = None
            try:
                emit('status', {'message': '正在查找食谱…', 'chat_id': chat_id})
                acquired = lock.acquire(timeout=settings.queue_timeout_seconds)
                if not acquired:
                    emit('error', {'code': 'busy', 'message': '正在回答其他问题，请稍后重试。'})
                    return
                if cancelled.is_set():
                    return
                # Reload after locking, so a completed previous turn is never lost.
                current = store.get(user, chat_id)
                if not current:
                    emit('error', {'code': 'chat_not_found', 'message': '这段对话已删除。'})
                    return
                memory = ConversationMemory(config.conversation_max_turns, config.conversation_max_chars).restore(
                    {} if body.reset else current['memory'],
                    source_history=() if body.reset else menu_source_history(current['messages']))
                user_id, answer_id = store.begin(user, chat_id, body.question)
                emit('start', {'chat_id': chat_id, 'user_message_id': user_id,
                               'message_id': answer_id, 'request_id': request_id})
                shortcuts = {'早餐吃什么': 'breakfast', '晚餐我想吃得清淡点，有什么推荐': 'light',
                             '推荐三道简单易做的主食': 'quick', '晚餐吃什么': 'dinner'}
                topic = body.topic or shortcuts.get(body.question)
                if not topic and is_recommendation(body.question) and re.search(r'饮品|饮料|喝什么', body.question): topic = 'drinks'
                key = (topic or body.question) if is_recommendation(body.question) else None
                seen = {} if body.reset else current['seen']
                exclusions = list(dict.fromkeys(seen.get(key, []) + body.exclude_ids)) if key else []
                kwargs = {'stream': True, 'strategy': body.strategy, 'memory': memory, 'result_info': info}
                if exclusions:
                    kwargs['exclude_ids'] = exclusions
                answer, analysis = app.state.system.ask_question_with_routing(body.question, **kwargs)
                info['topic'] = topic
                decorate(info)
                info['retrieval_strategy'] = analysis.recommended_strategy.value if analysis else None
                emit('metadata', {**info, 'chat_id': chat_id, 'message_id': answer_id})
                iterator = iter([answer]) if isinstance(answer, str) else iter(answer)
                checkpoint = time.monotonic()
                for part in iterator:
                    if cancelled.is_set():
                        break
                    parts.append(part)
                    if not emit('delta', {'text': part}):
                        break
                    if time.monotonic() - checkpoint >= 1:
                        store.finish(user, chat_id, answer_id, ''.join(parts), 'streaming', info)
                        checkpoint = time.monotonic()
                if cancelled.is_set():
                    store.finish(user, chat_id, answer_id, ''.join(parts), 'stopped', info)
                    return
                if key and info['kind'] == 'recommendation':
                    previous = [] if info.get('recommendation_reset') else exclusions
                    seen[key] = list(dict.fromkeys(previous + [s['document_id'] for s in info['sources']]))
                complete = ''.join(parts)
                store.finish(user, chat_id, answer_id, complete, 'complete', info, memory.snapshot(), seen)
                emit('done', {**info, 'answer': complete, 'chat_id': chat_id,
                              'message_id': answer_id, 'request_id': request_id})
            except Exception as exc:
                if not cancelled.is_set():
                    logger.error('Streaming generation failed', extra={'request_id': request_id,
                                                                      'error_type': type(exc).__name__})
                if answer_id:
                    store.finish(user, chat_id, answer_id, ''.join(parts), 'stopped' if cancelled.is_set() else 'error', info)
                emit('error', {'code': 'backend_error', 'message': '这次回答未能完成，可以重试。'})
            finally:
                if iterator is not None and hasattr(iterator, 'close'):
                    try:
                        iterator.close()
                    except Exception:
                        logger.warning('Stream iterator close failed', extra={'request_id': request_id})
                if acquired:
                    lock.release()
                with active_lock:
                    app.state.active_chats.discard(chat_id)
                    app.state.stream_cancellations.pop(chat_id, None)
                stop_signal.reset(cancel_token)
                emit('end', {})

        async def response_events():
            thread = threading.Thread(target=worker, name='recipe-stream', daemon=True)
            thread.start()
            try:
                while True:
                    if await request.is_disconnected():
                        cancelled.set()
                        break
                    try:
                        name, data = await asyncio.to_thread(output.get, True, .5)
                    except queue.Empty:
                        if not thread.is_alive():
                            break
                        yield ': keepalive\n\n'
                        continue
                    if name == 'end':
                        break
                    yield event(name, data)
            finally:
                cancelled.set()

        return StreamingResponse(response_events(), media_type='text/event-stream', headers={
            'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no',
            'X-Request-ID': request_id})

    from document_routes import register_document_routes
    register_document_routes(app, settings, authenticate, owner, Error)
