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
    data_path: str = str(ROOT / 'data/C8/cook')
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

    def __post_init__(self):
        if self.embedding_provider not in {'openai', 'ollama'} or self.llm_provider not in {'openai', 'ollama'}:
            raise ValueError('模型类型仅支持 openai（兼容接口）或 ollama')
        if self.embedding_batch_size < 1 or self.top_k < 1 or self.chunk_size <= self.chunk_overlap or self.chunk_overlap < 0:
            raise ValueError('批次、top_k 必须为正数，chunk_size 必须大于非负 chunk_overlap')
        if not 1 <= self.max_graph_depth <= 3:
            raise ValueError('max_graph_depth 必须在 1 到 3 之间')
        if self.conversation_max_turns < 1 or self.conversation_max_chars < 500:
            raise ValueError('会话记忆至少保留 1 轮，字符预算至少为 500')

    @classmethod
    def from_dict(cls, config_dict):
        return cls(**config_dict)

    def to_dict(self):
        result = asdict(self)
        for key in ('embedding_api_key', 'llm_api_key', 'qdrant_api_key', 'neo4j_password'):
            result[key] = '***' if result[key] else ''
        return result


GraphRAGConfig = RAGConfig
DEFAULT_CONFIG = RAGConfig()
