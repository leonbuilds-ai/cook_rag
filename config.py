"""
RAG系统配置文件
"""

from dataclasses import asdict, dataclass
from typing import Dict, Any

@dataclass
class RAGConfig:
    """RAG系统配置类"""

    # 路径配置
    data_path: str = "data/C8/cook"
    index_save_path: str = "./vector_index"

    # 模型配置
    embedding_model: str = "qwen3-embedding:8b"
    llm_model: str = "qwen3.5:9b"
    ollama_base_url: str = "http://localhost:11434"
    ollama_num_ctx: int = 16384       #生成模型的上下文窗口
    embedding_num_ctx: int = 4096     #嵌入模型的上下文窗口
    embedding_batch_size: int = 8     #每次向 Ollama 提交 8 个文本块

    # 检索配置
    top_k: int = 3

    # 生成配置
    temperature: float = 0.1
    max_tokens: int = 2048

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> 'RAGConfig':
        """从字典创建配置对象"""
        return cls(**config_dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)

# 默认配置实例
DEFAULT_CONFIG = RAGConfig()
