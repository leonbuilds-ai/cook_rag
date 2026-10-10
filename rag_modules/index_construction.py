"""
索引构建模块
"""

import logging
import json
from typing import List
from pathlib import Path

from langchain_ollama import OllamaEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

logger = logging.getLogger(__name__)

class IndexConstructionModule:
    """索引构建模块 - 负责向量化和索引构建"""

    def __init__(self, model_name: str = "qwen3-embedding:8b", index_save_path: str = "./vector_index",
                 base_url: str = "http://localhost:11434", num_ctx: int = 4096, batch_size: int = 8):
        """
        初始化索引构建模块

        Args:
            model_name: 嵌入模型名称
            index_save_path: 索引保存路径
            base_url: Ollama服务地址（不包含 /v1）
            num_ctx: 嵌入模型上下文大小，独立于生成模型
            batch_size: 每次嵌入的文本块数量
        """
        self.model_name = model_name
        self.index_save_path = index_save_path
        self.base_url = base_url
        if batch_size < 1 or num_ctx < 1:
            raise ValueError("embedding_batch_size 和 embedding_num_ctx 必须大于0")
        self.num_ctx = num_ctx
        self.batch_size = batch_size
        self.embeddings = None
        self.vectorstore = None
        self.setup_embeddings()
    
    def setup_embeddings(self):
        """初始化嵌入模型"""
        logger.info(f"正在初始化嵌入模型: {self.model_name}")
        
        self.embeddings = OllamaEmbeddings(
            model=self.model_name,
            base_url=self.base_url,
            num_ctx=self.num_ctx,
        )
        
        logger.info("嵌入模型初始化完成")
    
    def build_vector_index(self, chunks: List[Document]) -> FAISS:
        """
        构建向量索引
        
        Args:
            chunks: 文档块列表
            
        Returns:
            FAISS向量存储对象
        """
        logger.info("正在构建FAISS向量索引...")
        
        if not chunks:
            raise ValueError("文档块列表不能为空")
        
        # 逐批向量化，避免一次将整个知识库提交给Ollama模型进程。
        vectorstore = None
        for start in range(0, len(chunks), self.batch_size):     #按 0、8、16……这样的起点遍历文本块
            batch = chunks[start:start + self.batch_size]        #切出当前批次，最多 8 个文本块
            if vectorstore is None:
                vectorstore = FAISS.from_documents(documents=batch, embedding=self.embeddings)     #第一批用于创建索引
            else:
                vectorstore.add_documents(batch)                 #后续批次计算向量后追加到同一个索引
            if start == 0 or (start // self.batch_size + 1) % 10 == 0 or start + len(batch) == len(chunks):
                logger.info(f"向量化进度: {start + len(batch)}/{len(chunks)}")
        self.vectorstore = vectorstore
        
        logger.info(f"向量索引构建完成，包含 {len(chunks)} 个向量")
        return self.vectorstore
    
    def add_documents(self, new_chunks: List[Document]):
        """
        向现有索引添加新文档
        
        Args:
            new_chunks: 新的文档块列表
        """
        if not self.vectorstore:
            raise ValueError("请先构建向量索引")
        
        logger.info(f"正在添加 {len(new_chunks)} 个新文档到索引...")
        for start in range(0, len(new_chunks), self.batch_size):
            self.vectorstore.add_documents(new_chunks[start:start + self.batch_size])
        logger.info("新文档添加完成")

    def save_index(self):
        """
        保存向量索引到配置的路径
        """
        if not self.vectorstore:
            raise ValueError("请先构建向量索引")

        # 确保保存目录存在
        Path(self.index_save_path).mkdir(parents=True, exist_ok=True)

        self.vectorstore.save_local(self.index_save_path)
        metadata_path = Path(self.index_save_path) / "embedding_config.json"
        metadata_path.write_text(
            json.dumps(self._embedding_config(), ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"向量索引已保存到: {self.index_save_path}")
    
    def load_index(self):
        """
        从配置的路径加载向量索引

        Returns:
            加载的向量存储对象，如果加载失败返回None
        """
        if not self.embeddings:
            self.setup_embeddings()

        if not Path(self.index_save_path).exists():
            logger.info(f"索引路径不存在: {self.index_save_path}，将构建新索引")
            return None

        try:
            metadata_path = Path(self.index_save_path) / "embedding_config.json"
            if not metadata_path.exists():
                logger.info("旧索引缺少嵌入模型信息，将使用Ollama重建索引")
                return None
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata != self._embedding_config():
                logger.info("索引的嵌入模型或上下文配置与当前配置不一致，将重建索引")
                return None
            self.vectorstore = FAISS.load_local(
                self.index_save_path,
                self.embeddings,
                allow_dangerous_deserialization=True
            )
            logger.info(f"向量索引已从 {self.index_save_path} 加载")
            return self.vectorstore
        except Exception as e:
            logger.warning(f"加载向量索引失败: {e}，将构建新索引")
            return None
    
    def _embedding_config(self):
        return {"provider": "ollama", "model": self.model_name, "num_ctx": self.num_ctx}

    def similarity_search(self, query: str, k: int = 5) -> List[Document]:
        """
        相似度搜索
        
        Args:
            query: 查询文本
            k: 返回结果数量
            
        Returns:
            相似文档列表
        """
        if not self.vectorstore:
            raise ValueError("请先构建或加载向量索引")
        
        return self.vectorstore.similarity_search(query, k=k)
