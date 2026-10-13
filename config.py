"""Configuration loaded from the project .env, independent of the working directory."""
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env', override=False)


def env(name, default=''):
    return os.getenv(name, default)


@dataclass
class RAGConfig:
    data_path: str = str(ROOT / 'data/cook')
    index_save_path: str = str(ROOT / 'vector_index')
    embedding_model: str = field(default_factory=lambda: env('EMBED_MODEL_NAME', 'qwen3-embedding:8b'))
    embedding_provider: str = field(default_factory=lambda: env('EMBED_MODEL_TYPE', 'ollama').lower())
    embedding_base_url: str = field(default_factory=lambda: env('EMBED_BASE_URL', 'http://localhost:11434'))
    embedding_api_key: str = field(default_factory=lambda: env('EMBED_API_KEY', 'ollama'), repr=False)
    llm_model: str = field(default_factory=lambda: env('LLM_MODEL_ID', 'qwen3.5:9b'))
    llm_base_url: str = field(default_factory=lambda: env('LLM_BASE_URL', 'http://localhost:11434'))
    llm_api_key: str = field(default_factory=lambda: env('LLM_API_KEY', 'ollama'), repr=False)
    llm_provider: str = field(default_factory=lambda: env('LLM_MODEL_TYPE', 'ollama').lower())
    ollama_num_ctx: int = 16384
    embedding_num_ctx: int = 4096
    embedding_batch_size: int = field(default_factory=lambda: int(env('EMBED_BATCH_SIZE', '8')))
    qdrant_url: str = field(default_factory=lambda: env('QDRANT_URL', 'http://localhost:6333'))
    qdrant_api_key: str = field(default_factory=lambda: env('QDRANT_API_KEY'), repr=False)
    qdrant_collection_name: str = field(default_factory=lambda: env('QDRANT_COLLECTION_NAME', 'recipe_c9'))
    neo4j_uri: str = field(default_factory=lambda: env('NEO4J_URI', 'bolt://localhost:7687'))
    neo4j_user: str = field(default_factory=lambda: env('NEO4J_USERNAME', env('NEO4J_USER', 'neo4j')))
    neo4j_password: str = field(default_factory=lambda: env('NEO4J_PASSWORD'), repr=False)
    neo4j_database: str = field(default_factory=lambda: env('NEO4J_DATABASE', 'neo4j'))
    neo4j_max_connection_lifetime: int = field(default_factory=lambda: int(env('NEO4J_MAX_CONNECTION_LIFETIME', '3600')))
    neo4j_max_connection_pool_size: int = field(default_factory=lambda: int(env('NEO4J_MAX_CONNECTION_POOL_SIZE', '50')))
    neo4j_connection_timeout: float = field(default_factory=lambda: float(env('NEO4J_CONNECTION_TIMEOUT', '10')))
    top_k: int = 5
    temperature: float = 0.1
    max_tokens: int = 2048
    chunk_size: int = 1000
    chunk_overlap: int = 100
    max_graph_depth: int = 2
    enable_parent_doc_retrieval: bool = True
    parent_doc_top_n: int = 3
    parent_doc_max_chars: int = 6000
    context_max_chars: int = 18000
    conversation_max_turns: int = 6
    conversation_max_chars: int = 6000
    retrieval_mode: str = 'recipe'
    source_base_url: str = 'https://github.com/leonbuilds-ai/cook_rag/blob/main/'
    rerank_enabled: bool = False
    rerank_model: str = 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'
    rerank_threads: int = 4
    redis_url: str = field(default='', repr=False)

    def __post_init__(self):
        if self.embedding_provider not in {'openai', 'ollama'} or self.llm_provider not in {'openai', 'ollama'}:
            raise ValueError('模型类型仅支持 openai（兼容接口）或 ollama')
        if self.embedding_batch_size < 1 or self.top_k < 1 or self.chunk_size <= self.chunk_overlap or self.chunk_overlap < 0:
            raise ValueError('批次、top_k 必须为正数，chunk_size 必须大于非负 chunk_overlap')
        if not 1 <= self.max_graph_depth <= 3:
            raise ValueError('max_graph_depth 必须在 1 到 3 之间')
        if self.conversation_max_turns < 1 or self.conversation_max_chars < 500:
            raise ValueError('会话记忆至少保留 1 轮，字符预算至少为 500')
        if self.retrieval_mode not in {'recipe', 'baseline'}:
            raise ValueError('retrieval_mode 必须为 recipe 或 baseline')

    @classmethod
    def from_dict(cls, config_dict):
        return cls(**config_dict)

    def to_dict(self):
        result = asdict(self)
        for key in ('embedding_api_key', 'llm_api_key', 'qdrant_api_key', 'neo4j_password', 'redis_url'):
            result[key] = '***' if result[key] else ''
        return result


GraphRAGConfig = RAGConfig
def load_config(path=None):
    """TOML holds non-secret tuning values; existing model/database env vars win."""
    import tomllib
    from dataclasses import fields
    path = Path(path or env('RAG_CONFIG', str(ROOT / 'settings.toml')))
    values = tomllib.loads(path.read_text(encoding='utf-8')).get('rag', {}) if path.exists() else {}
    allowed = {f.name for f in fields(RAGConfig)}
    if set(values) - allowed:
        raise ValueError(f'未知配置字段: {sorted(set(values) - allowed)}')
    secret_names = {'embedding_api_key', 'llm_api_key', 'qdrant_api_key', 'neo4j_password', 'redis_url'}
    if set(values) & secret_names:
        raise ValueError('密钥请放在 .env 或环境变量中')
    defaults = RAGConfig()
    if 'RERANK_ENABLED' in os.environ: values['rerank_enabled'] = env('RERANK_ENABLED').lower() in {'true','1','yes'}
    if 'RERANK_MODEL' in os.environ: values['rerank_model'] = env('RERANK_MODEL')
    values['redis_url'] = env('REDIS_URL')
    # Fields backed by env factories use those values when explicitly set in the environment.
    env_fields = {'embedding_model': 'EMBED_MODEL_NAME', 'embedding_provider': 'EMBED_MODEL_TYPE',
        'embedding_base_url': 'EMBED_BASE_URL', 'llm_model': 'LLM_MODEL_ID',
        'llm_provider': 'LLM_MODEL_TYPE', 'llm_base_url': 'LLM_BASE_URL',
        'qdrant_url': 'QDRANT_URL', 'qdrant_collection_name': 'QDRANT_COLLECTION_NAME',
        'neo4j_uri': 'NEO4J_URI', 'neo4j_user': 'NEO4J_USERNAME', 'neo4j_database': 'NEO4J_DATABASE',
        'embedding_batch_size': 'EMBED_BATCH_SIZE', 'neo4j_connection_timeout': 'NEO4J_CONNECTION_TIMEOUT',
        'neo4j_max_connection_pool_size': 'NEO4J_MAX_CONNECTION_POOL_SIZE',
        'neo4j_max_connection_lifetime': 'NEO4J_MAX_CONNECTION_LIFETIME'}
    for name, variable in env_fields.items():
        if variable in os.environ:
            values.pop(name, None)
    if 'NEO4J_USER' in os.environ:
        values.pop('neo4j_user', None)
    for name in ('data_path', 'index_save_path'):
        if name in values:
            values[name] = str((ROOT / values[name]).resolve())
    if env('RETRIEVAL_MODE'):
        values['retrieval_mode'] = env('RETRIEVAL_MODE')
    return RAGConfig(**{**asdict(defaults), **values})


DEFAULT_CONFIG = load_config()
