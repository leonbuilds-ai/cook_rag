"""SQLAlchemy persistence shared by PostgreSQL production and SQLite offline tests."""
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import (create_engine, MetaData, Table, Column, String, Text, Integer, JSON,
                        ForeignKey, Index, select, insert, update, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.pool import NullPool

metadata = MetaData()
json_type = JSON().with_variant(JSONB(), 'postgresql')
users = Table('users', metadata, Column('id', String(64), primary_key=True), Column('created_at', String(40), nullable=False))
chats = Table('chats', metadata, Column('id', String(36), primary_key=True), Column('owner', String(64), nullable=False),
              Column('title', Text, nullable=False), Column('created_at', String(40), nullable=False),
              Column('updated_at', String(40), nullable=False), Column('deleted_at', String(40)),
              Column('memory', json_type, nullable=False), Column('seen', json_type, nullable=False))
messages = Table('messages', metadata, Column('sequence', Integer, primary_key=True, autoincrement=True),
                 Column('id', String(36), unique=True, nullable=False), Column('chat_id', String(36), ForeignKey('chats.id'), nullable=False),
                 Column('role', String(16), nullable=False), Column('content', Text, nullable=False),
                 Column('status', String(20), nullable=False), Column('payload', json_type, nullable=False),
                 Column('created_at', String(40), nullable=False))
documents = Table('documents', metadata, Column('id', String(100), primary_key=True), Column('owner', String(64)),
                  Column('title', Text, nullable=False), Column('source', Text, nullable=False), Column('content', Text, nullable=False),
                  Column('checksum', String(64), nullable=False), Column('meta', json_type, nullable=False),
                  Column('status', String(20), nullable=False), Column('created_at', String(40), nullable=False))
jobs = Table('jobs', metadata, Column('id', String(36), primary_key=True), Column('owner', String(64), nullable=False),
             Column('document_id', String(100), ForeignKey('documents.id'), nullable=False),
             Column('status', String(20), nullable=False), Column('stage', Text, nullable=False), Column('progress', Integer, nullable=False),
             Column('error', Text), Column('created_at', String(40), nullable=False), Column('updated_at', String(40), nullable=False))
evaluations = Table('evaluations', metadata, Column('id', String(64), primary_key=True), Column('sample', json_type, nullable=False),
                    Column('profile', json_type, nullable=False), Column('scores', json_type, nullable=False), Column('created_at', String(40), nullable=False))
Index('chat_owner', chats.c.owner, chats.c.updated_at)
Index('messages_chat', messages.c.chat_id, messages.c.sequence)
Index('jobs_owner', jobs.c.owner, jobs.c.created_at)

def now(): return datetime.now(timezone.utc).isoformat()

def decoded(value): return json.loads(value) if isinstance(value, str) else value

class SQLStore:
    def __init__(self, path, url=''):
        if not url:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            url = 'sqlite+pysqlite:///' + str(Path(path).resolve())
        options = {'poolclass':NullPool} if url.startswith('sqlite') else {}
        self.engine = create_engine(url, pool_pre_ping=True, hide_parameters=True, **options,
            connect_args={'check_same_thread': False, 'timeout': 10} if url.startswith('sqlite') else {'connect_timeout': 5})
        self.backend = self.engine.dialect.name
        self.path = str(path)

    def initialize(self): metadata.create_all(self.engine)

    def ping(self):
        with self.engine.connect() as db: db.execute(select(1))
        return 'ok'

    def recover_interrupted(self):
        self.initialize()
        with self.engine.begin() as db:
            db.execute(update(messages).where(messages.c.status == 'streaming').values(status='interrupted'))

    def ensure_user(self, db, owner):
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        statement = pg_insert if self.backend == 'postgresql' else sqlite_insert
        db.execute(statement(users).values(id=owner, created_at=now()).on_conflict_do_nothing(index_elements=['id']))

    def create(self, owner):
        key, stamp = str(uuid.uuid4()), now()
        with self.engine.begin() as db:
            self.ensure_user(db, owner)
            db.execute(insert(chats).values(id=key, owner=owner, title='新对话', created_at=stamp, updated_at=stamp, memory={}, seen={}))
        return self.get(owner, key)

    def list(self, owner, deleted=False):
        with self.engine.connect() as db:
            query = select(chats.c.id, chats.c.title, chats.c.created_at, chats.c.updated_at, chats.c.deleted_at).where(chats.c.owner == owner)
            query = query.where(chats.c.deleted_at.is_not(None) if deleted else chats.c.deleted_at.is_(None))
            return [dict(row) for row in db.execute(query.order_by(chats.c.updated_at.desc()).limit(200)).mappings()]

    def get(self, owner, chat_id, deleted=False):
        with self.engine.connect() as db:
            row = db.execute(select(chats).where(chats.c.owner == owner, chats.c.id == chat_id)).mappings().first()
            if not row or (row['deleted_at'] and not deleted): return None
            result = dict(row); result.pop('owner')
            result['memory'], result['seen'] = decoded(result['memory']), decoded(result['seen'])
            result['messages'] = [{**dict(r), 'payload': decoded(r['payload'])} for r in db.execute(
                select(messages.c.id, messages.c.role, messages.c.content, messages.c.status, messages.c.payload, messages.c.created_at)
                .where(messages.c.chat_id == chat_id).order_by(messages.c.sequence)).mappings()]
            return result

    def update(self, owner, chat_id, title=None, deleted=None):
        values = {'updated_at': now()}
        if title is not None: values['title'] = title[:60]
        if deleted is not None: values['deleted_at'] = now() if deleted else None
        with self.engine.begin() as db:
            return bool(db.execute(update(chats).where(chats.c.owner == owner, chats.c.id == chat_id).values(**values)).rowcount)

    def begin(self, owner, chat_id, question):
        uid, aid, stamp = str(uuid.uuid4()), str(uuid.uuid4()), now()
        with self.engine.begin() as db:
            row = db.execute(select(chats.c.title).where(chats.c.owner == owner, chats.c.id == chat_id,
                                                        chats.c.deleted_at.is_(None))).first()
            if not row: raise ValueError('Chat not found')
            db.execute(update(chats).where(chats.c.id == chat_id).values(updated_at=stamp, title=question[:36] if row.title == '新对话' else row.title))
            db.execute(insert(messages), [dict(id=uid, chat_id=chat_id, role='user', content=question, status='complete', payload={}, created_at=stamp),
                                           dict(id=aid, chat_id=chat_id, role='assistant', content='', status='streaming', payload={}, created_at=stamp)])
        return uid, aid

    def finish(self, owner, chat_id, answer_id, answer, status, payload, memory=None, seen=None):
        with self.engine.begin() as db:
            if not db.execute(select(chats.c.id).where(chats.c.owner == owner, chats.c.id == chat_id)).first(): return
            db.execute(update(messages).where(messages.c.id == answer_id, messages.c.chat_id == chat_id)
                       .values(content=answer, status=status, payload=payload))
            values = {'updated_at': now()}
            if memory is not None: values.update(memory=memory, seen=seen)
            db.execute(update(chats).where(chats.c.id == chat_id).values(**values))

    def sync_documents(self, docs):
        with self.engine.begin() as db:
            for doc in docs:
                key = str(doc.metadata['parent_id'])
                values = dict(title=doc.metadata['dish_name'], source=doc.metadata['source'], content=doc.page_content,
                              checksum=hashlib.sha256(doc.page_content.encode()).hexdigest(), meta=doc.metadata, status='ready')
                if db.execute(select(documents.c.id).where(documents.c.id == key)).first():
                    db.execute(update(documents).where(documents.c.id == key).values(**values))
                else: db.execute(insert(documents).values(id=key, created_at=now(), **values))

    def catalog(self):
        with self.engine.connect() as db:
            return [dict(row) for row in db.execute(select(documents.c.id, documents.c.title, documents.c.source,
                 documents.c.status, documents.c.created_at).order_by(documents.c.created_at.desc())).mappings()]

    def submit(self, owner, title, content, category):
        checksum = hashlib.sha256(content.encode()).hexdigest()
        key = 'upload:' + hashlib.sha256((owner + checksum).encode()).hexdigest()[:32]
        jid, stamp = str(uuid.uuid4()), now()
        with self.engine.begin() as db:
            self.ensure_user(db, owner)
            old = db.execute(select(documents).where(documents.c.id == key)).mappings().first()
            if old and old['status'] == 'ready': return {'document_id': key, 'duplicate': True, 'status': 'ready'}
            pending = db.execute(select(jobs).where(jobs.c.document_id == key, jobs.c.status.in_(['queued','running']))).mappings().first()
            if pending: return {k:v for k,v in pending.items() if k != 'owner'}
            if not old: db.execute(insert(documents).values(id=key, owner=owner, title=title, source=key, content=content,
                checksum=checksum, meta={'parent_id':key, 'dish_name':title, 'source':key, 'category':category, 'difficulty':'未知', 'doc_type':'parent'},
                status='queued', created_at=stamp))
            else: db.execute(update(documents).where(documents.c.id == key).values(status='queued'))
            record = dict(id=jid, owner=owner, document_id=key, status='queued', stage='等待入库', progress=0, created_at=stamp, updated_at=stamp)
            db.execute(insert(jobs).values(**record))
        record.pop('owner'); return record

    def job(self, owner, key):
        with self.engine.connect() as db:
            query = select(jobs).where(jobs.c.id == key)
            if owner is not None: query = query.where(jobs.c.owner == owner)
            row = db.execute(query).mappings().first()
            if not row: return None
            result=dict(row); result.pop('owner'); return result

    def job_list(self, owner):
        with self.engine.connect() as db:
            return [dict(r) for r in db.execute(select(jobs.c.id, jobs.c.document_id, jobs.c.status, jobs.c.stage,
                jobs.c.progress, jobs.c.error, jobs.c.created_at).where(jobs.c.owner==owner).order_by(jobs.c.created_at.desc()).limit(50)).mappings()]

    def document(self, key):
        with self.engine.connect() as db:
            row = db.execute(select(documents).where(documents.c.id==key)).mappings().first()
            return dict(row) if row else None

    def job_progress(self, key, stage, progress, status='running', error=None):
        with self.engine.begin() as db:
            db.execute(update(jobs).where(jobs.c.id==key).values(stage=stage, progress=progress, status=status, error=error, updated_at=now()))
            if status in {'complete','failed'}:
                doc_id = db.execute(select(jobs.c.document_id).where(jobs.c.id==key)).scalar_one()
                db.execute(update(documents).where(documents.c.id==doc_id).values(status='ready' if status=='complete' else 'failed'))

    def unfinished_jobs(self):
        with self.engine.connect() as db:
            return list(db.execute(select(jobs.c.id).where(jobs.c.status.in_(['queued','running']))).scalars())

    def evaluation(self, key):
        with self.engine.connect() as db:
            row=db.execute(select(evaluations).where(evaluations.c.id==key)).mappings().first()
            return dict(row) if row else None

    def save_evaluation(self, key, sample, profile, scores):
        with self.engine.begin() as db:
            if not db.execute(select(evaluations.c.id).where(evaluations.c.id==key)).first():
                db.execute(insert(evaluations).values(id=key, sample=sample, profile=profile, scores=scores, created_at=now()))

    def migrate_sqlite(self, path):
        """Idempotent import; originals and owner hashes remain untouched."""
        if self.backend != 'postgresql' or not Path(path).exists(): return {'chats':0, 'messages':0}
        count={'chats':0,'messages':0}
        with sqlite3.connect(path) as old, self.engine.begin() as db:
            old.row_factory=sqlite3.Row
            if not old.execute("SELECT 1 FROM sqlite_master WHERE name='chats'").fetchone(): return count
            for row in old.execute('SELECT * FROM chats'):
                values=dict(row); self.ensure_user(db, values['owner'])
                if not db.execute(select(chats.c.id).where(chats.c.id==values['id'])).first():
                    values['memory'],values['seen']=decoded(values['memory']),decoded(values['seen'])
                    db.execute(insert(chats).values(**values)); count['chats']+=1
            for row in old.execute('SELECT * FROM messages ORDER BY sequence'):
                values=dict(row); values.pop('sequence'); values['payload']=decoded(values['payload'])
                if not db.execute(select(messages.c.id).where(messages.c.id==values['id'])).first():
                    db.execute(insert(messages).values(**values)); count['messages']+=1
        return count

    def close(self): self.engine.dispose()
