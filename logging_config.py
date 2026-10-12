"""Rotating JSON application logs; no prompts, answers or credentials are logged."""
import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record):
        value = {'time': datetime.now(timezone.utc).isoformat(), 'level': record.levelname,
                 'logger': record.name, 'message': record.getMessage()}
        for key in ('request_id', 'method', 'path', 'status', 'duration_ms', 'error_type'):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        return json.dumps(value, ensure_ascii=False)


def configure_logging(settings):
    path = Path(settings.log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=settings.log_max_bytes,
                                 backupCount=settings.log_backup_count, encoding='utf-8')
    console = logging.StreamHandler(sys.stdout)
    for item in (handler, console):
        item.setFormatter(JsonFormatter())
    logging.basicConfig(level=settings.log_level, handlers=[handler, console], force=True)
    for name in ('httpx', 'httpcore', 'neo4j', 'jieba'):
        logging.getLogger(name).setLevel(logging.WARNING)
