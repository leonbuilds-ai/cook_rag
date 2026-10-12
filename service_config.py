"""HTTP and logging settings. Credentials are deliberately absent from TOML."""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from config import ROOT


@dataclass
class ServiceConfig:
    host: str = '127.0.0.1'
    port: int = 8000
    max_sessions: int = 200
    session_ttl_seconds: int = 3600
    queue_timeout_seconds: float = 1
    api_key: str = field(default='', repr=False)
    log_level: str = 'INFO'
    log_file: str = str(ROOT / 'logs/cook_rag.jsonl')
    log_max_bytes: int = 10485760
    log_backup_count: int = 3
    history_db_path: str = str(ROOT / 'runtime/history.sqlite3')

    def __post_init__(self):
        if not 1 <= self.port <= 65535 or self.max_sessions < 1 or self.session_ttl_seconds < 1:
            raise ValueError('端口、会话数量或 TTL 无效')
        if not 0 <= self.queue_timeout_seconds <= 30:
            raise ValueError('queue_timeout_seconds 必须在 0 到 30 秒之间')
        if self.log_level not in {'DEBUG', 'INFO', 'WARNING', 'ERROR'}:
            raise ValueError('log_level 无效')
        if self.log_max_bytes < 1024 or self.log_backup_count < 1:
            raise ValueError('日志轮转配置无效')


def load_service_config(path=None):
    path = Path(path or os.getenv('RAG_CONFIG', ROOT / 'settings.toml'))
    settings = tomllib.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    values = dict(settings.get('service', {}))
    for key, value in settings.get('logging', {}).items():
        name = {'level': 'log_level', 'file': 'log_file', 'max_bytes': 'log_max_bytes',
                'backup_count': 'log_backup_count'}.get(key)
        if not name:
            raise ValueError('未知日志配置字段: ' + key)
        values[name] = value
    if 'api_key' in values:
        raise ValueError('APP_API_KEY 必须放在环境变量中')
    values['api_key'] = os.getenv('APP_API_KEY', '')
    for name, variable, cast in [('host', 'API_HOST', str), ('port', 'API_PORT', int),
                                 ('history_db_path', 'HISTORY_DB_PATH', str)]:
        if variable in os.environ:
            values[name] = cast(os.environ[variable])
    if 'log_file' in values:
        values['log_file'] = str((ROOT / values['log_file']).resolve())
    if 'history_db_path' in values:
        values['history_db_path'] = str((ROOT / values['history_db_path']).resolve())
    return ServiceConfig(**values)
