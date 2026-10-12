"""SQLite conversations owned by an opaque browser cookie; no database/model secrets."""
import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager


def now():
    return datetime.now(timezone.utc).isoformat()


class ChatStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
                memory TEXT NOT NULL DEFAULT '{}', seen TEXT NOT NULL DEFAULT '{}')''')
            db.execute('''CREATE TABLE IF NOT EXISTS messages (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                chat_id TEXT NOT NULL REFERENCES chats(id), role TEXT NOT NULL,
                content TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                created_at TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS chat_owner ON chats(owner, updated_at)')
            db.execute('CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, sequence)')

    def recover_interrupted(self):
        with self.connect() as db:
            db.execute("UPDATE messages SET status='interrupted' WHERE status='streaming'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, owner):
        chat_id = str(uuid.uuid4())
        stamp = now()
        with self.connect() as db:
            db.execute('INSERT INTO chats(id,owner,title,created_at,updated_at) VALUES(?,?,?,?,?)',
                       (chat_id, owner, '新对话', stamp, stamp))
        return self.get(owner, chat_id)

    def list(self, owner, deleted=False):
        with self.connect() as db:
            rows = db.execute('SELECT id,title,created_at,updated_at,deleted_at FROM chats WHERE owner=? '
                              + ('AND deleted_at IS NOT NULL' if deleted else 'AND deleted_at IS NULL')
                              + ' ORDER BY updated_at DESC LIMIT 200', (owner,)).fetchall()
        return [dict(row) for row in rows]

    def get(self, owner, chat_id, deleted=False):
        with self.connect() as db:
            row = db.execute('SELECT * FROM chats WHERE owner=? AND id=?', (owner, chat_id)).fetchone()
            if not row or (row['deleted_at'] and not deleted):
                return None
            result = dict(row)
            result['memory'] = json.loads(result['memory'])
            result['seen'] = json.loads(result['seen'])
            rows = db.execute('SELECT id,role,content,status,payload,created_at FROM messages WHERE chat_id=? '
                              'ORDER BY sequence', (chat_id,)).fetchall()
            result['messages'] = [{**dict(r), 'payload': json.loads(r['payload'])} for r in rows]
            result.pop('owner')
            return result

    def update(self, owner, chat_id, title=None, deleted=None):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM chats WHERE owner=? AND id=?', (owner, chat_id)).fetchone():
                return False
            if title is not None:
                db.execute('UPDATE chats SET title=?,updated_at=? WHERE id=?', (title[:60], now(), chat_id))
            if deleted is not None:
                db.execute('UPDATE chats SET deleted_at=?,updated_at=? WHERE id=?',
                           (now() if deleted else None, now(), chat_id))
        return True

    def begin(self, owner, chat_id, question):
        user_id, answer_id = str(uuid.uuid4()), str(uuid.uuid4())
        with self.connect() as db:
            row = db.execute('SELECT title,deleted_at FROM chats WHERE id=? AND owner=?',
                             (chat_id, owner)).fetchone()
            if not row or row['deleted_at']:
                raise ValueError('Chat not found')
            stamp = now()
            title = question[:36] if row['title'] == '新对话' else row['title']
            db.execute('UPDATE chats SET title=?,updated_at=? WHERE id=?', (title, stamp, chat_id))
            db.execute('INSERT INTO messages(id,chat_id,role,content,status,payload,created_at) VALUES(?,?,?,?,?,?,?)',
                       (user_id, chat_id, 'user', question, 'complete', '{}', stamp))
            db.execute('INSERT INTO messages(id,chat_id,role,content,status,payload,created_at) VALUES(?,?,?,?,?,?,?)',
                       (answer_id, chat_id, 'assistant', '', 'streaming', '{}', stamp))
        return user_id, answer_id

    def finish(self, owner, chat_id, answer_id, answer, status, payload, memory=None, seen=None):
        with self.connect() as db:
            row = db.execute('SELECT deleted_at FROM chats WHERE id=? AND owner=?', (chat_id, owner)).fetchone()
            if not row:
                return
            db.execute('UPDATE messages SET content=?,status=?,payload=? WHERE id=? AND chat_id=?',
                       (answer, status, json.dumps(payload, ensure_ascii=False), answer_id, chat_id))
            db.execute('UPDATE chats SET updated_at=? WHERE id=?', (now(), chat_id))
            if memory is not None:
                db.execute('UPDATE chats SET memory=?,seen=? WHERE id=?',
                           (json.dumps(memory, ensure_ascii=False), json.dumps(seen, ensure_ascii=False), chat_id))
