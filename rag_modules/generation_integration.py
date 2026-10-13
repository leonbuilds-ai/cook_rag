"""
生成集成模块
"""

import logging
import re
from typing import List

from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_ollama import ChatOllama
from .model_clients import create_llm
from .cancellation import stop_signal, stream as cancellable_stream
from langchain_core.documents import Document
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

logger = logging.getLogger(__name__)

class GenerationIntegrationModule:
    """生成集成模块 - 负责LLM集成和回答生成"""
    
    def __init__(self, model_name: str = "qwen3.5:9b", temperature: float = 0.1, max_tokens: int = 2048,
                 base_url: str = "http://localhost:11434", num_ctx: int = 16384, config=None):
        """
        初始化生成集成模块
        
        Args:
            model_name: 模型名称
            temperature: 生成温度
            max_tokens: 最大token数
            base_url: Ollama服务地址（不包含 /v1）
            num_ctx: 模型上下文窗口大小
        """
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = base_url
        self.num_ctx = num_ctx
        self.config = config
        self.llm = None
        self.setup_llm()
    
    def setup_llm(self):
        """初始化大语言模型"""
        logger.info(f"正在初始化LLM: {self.model_name}")

        self.llm = create_llm(self.config) if self.config else ChatOllama(
            model=self.model_name,
            base_url=self.base_url,
            temperature=self.temperature,
            num_predict=self.max_tokens,
            num_ctx=self.num_ctx,
            reasoning=False,
        )
        
        logger.info("LLM初始化完成")

    def request_llm(self):
        if stop_signal.get() is None: return self.llm
        return create_llm(self.config) if self.config else ChatOllama(
            model=self.model_name, base_url=self.base_url, temperature=self.temperature,
            num_predict=self.max_tokens, num_ctx=self.num_ctx, reasoning=False)
    
    def generate_basic_answer(self, query: str, context_docs: List[Document], history=None) -> str:
        """
        生成基础回答

        Args:
            query: 用户查询
            context_docs: 上下文文档列表

        Returns:
            生成的回答
        """
        context = self._build_context(context_docs)
        history_text = self._format_history(history)

        prompt = ChatPromptTemplate.from_template("""
涉及食谱事实的句子请引用对应的 [S1]、[S2] 等来源标记。只使用本轮给出的标记。
你是一位专业的烹饪助手。请根据以下食谱信息回答用户的问题。

以本轮解析的问题和食谱信息为准。用户引用旧推荐中的另一项时，切换到该项；
历史讨论的菜谱不代表整个知识库的范围，不应声称上一轮的菜是唯一有资料的菜。

之前的对话（仅用于理解追问，不作为食谱事实来源）:
{history}

用户问题: {question}

相关食谱信息:
{context}

请根据原文提供实用的回答，引用菜名或来源。不应把未记录的食材搭配或因果关系当成事实；信息不足请说明。

回答:""")

        # 使用LCEL构建链
        chain = (
            {"question": RunnablePassthrough(), "context": lambda _: context, "history": lambda _: history_text}
            | prompt
            | self.llm
            | StrOutputParser()
        )

        response = chain.invoke(query)
        return response
    
    def generate_step_by_step_answer(self, query: str, context_docs: List[Document], history=None) -> str:
        """
        生成分步骤回答

        Args:
            query: 用户查询
            context_docs: 上下文文档列表

        Returns:
            分步骤的详细回答
        """
        context = self._build_context(context_docs)
        history_text = self._format_history(history)

        prompt = ChatPromptTemplate.from_template("""
涉及食谱事实的句子请引用对应的 [S1]、[S2] 等来源标记。只使用本轮给出的标记。
你是一位专业的烹饪导师。请根据食谱信息，为用户提供详细的分步骤指导。

以本轮解析的问题和食谱信息为准。用户引用旧推荐中的另一项时，切换到该项；
历史讨论的菜谱不代表整个知识库的范围，不应声称上一轮的菜是唯一有资料的菜。

之前的对话（仅用于理解追问，不作为食谱事实来源）:
{history}

用户问题: {question}

相关食谱信息:
{context}

请灵活组织回答，建议包含以下部分（可根据实际内容调整）：

## 🥘 菜品介绍
[简要介绍菜品特点和难度]

## 🛒 所需食材
[列出主要食材和用量]

## 👨‍🍳 制作步骤
[详细的分步骤说明，每步包含具体操作和大概所需时间]

## 💡 制作技巧
[仅在有实用技巧时包含。优先使用原文中的实用技巧，如果原文的"附加内容"与烹饪无关或为空，可以基于制作步骤总结关键要点，或者完全省略此部分]

注意：
- 优先遵守用户要求的回答长度和格式，并引用使用的菜名或来源
- 只根据原文和明确的图关系证据回答；缺失的时间、用量、营养或因果信息请说明，不要编造
- 根据实际内容灵活调整结构
- 不要强行填充无关内容或重复制作步骤中的信息
- 重点突出实用性和可操作性
- 如果没有额外的技巧要分享，可以省略制作技巧部分

回答:""")

        chain = (
            {"question": RunnablePassthrough(), "context": lambda _: context, "history": lambda _: history_text}
            | prompt
            | self.llm
            | StrOutputParser()
        )

        response = chain.invoke(query)
        return response
    
    def query_rewrite(self, query: str) -> str:
        """
        智能查询重写 - 让大模型判断是否需要重写查询

        Args:
            query: 原始查询

        Returns:
            重写后的查询或原查询
        """
        prompt = PromptTemplate(
            template="""
你是一个智能查询分析助手。请分析用户的查询，判断是否需要重写以提高食谱搜索效果。

原始查询: {query}

分析规则：
1. **具体明确的查询**（直接返回原查询）：
   - 包含具体菜品名称：如"宫保鸡丁怎么做"、"红烧肉的制作方法"
   - 明确的制作询问：如"蛋炒饭需要什么食材"、"糖醋排骨的步骤"
   - 具体的烹饪技巧：如"如何炒菜不粘锅"、"怎样调制糖醋汁"

2. **模糊不清的查询**（需要重写）：
   - 过于宽泛：如"做菜"、"有什么好吃的"、"推荐个菜"
   - 缺乏具体信息：如"川菜"、"素菜"、"简单的"
   - 口语化表达：如"想吃点什么"、"有饮品推荐吗"

重写原则：
- 保持原意不变
- 增加相关烹饪术语
- 优先推荐简单易做的
- 保持简洁性

示例：
- "做菜" → "简单易做的家常菜谱"
- "有饮品推荐吗" → "简单饮品制作方法"
- "推荐个菜" → "简单家常菜推荐"
- "川菜" → "经典川菜菜谱"
- "宫保鸡丁怎么做" → "宫保鸡丁怎么做"（保持原查询）
- "红烧肉需要什么食材" → "红烧肉需要什么食材"（保持原查询）

请输出最终查询（如果不需要重写就返回原查询）:""",
            input_variables=["query"]
        )

        chain = (
            {"query": RunnablePassthrough()}
            | prompt
            | self.llm
            | StrOutputParser()
        )

        response = chain.invoke(query).strip()

        # 记录重写结果
        if response != query:
            logger.info('查询已重写')
        else:
            logger.info('查询无需重写')

        return response



    def query_router(self, query: str) -> str:
        """
        查询路由 - 根据查询类型选择不同的处理方式

        Args:
            query: 用户查询

        Returns:
            路由类型 ('list', 'detail', 'general')
        """
        prompt = ChatPromptTemplate.from_template("""
根据用户的问题，将其分类为以下三种类型之一：

1. 'list' - 用户想要获取菜品列表或推荐，只需要菜名
   例如：推荐几个素菜、有什么川菜、给我3个简单的菜

2. 'detail' - 用户想要具体的制作方法或详细信息
   例如：宫保鸡丁怎么做、制作步骤、需要什么食材

3. 'general' - 其他一般性问题
   例如：什么是川菜、制作技巧、营养价值

请只返回分类结果：list、detail 或 general

用户问题: {query}

分类结果:""")

        chain = (
            {"query": RunnablePassthrough()}
            | prompt
            | self.llm
            | StrOutputParser()
        )

        result = chain.invoke(query).strip().lower()

        # 确保返回有效的路由类型
        if result in ['list', 'detail', 'general']:
            return result
        else:
            return 'general'  # 默认类型

    def generate_list_answer(self, query: str, context_docs: List[Document]) -> str:
        """
        生成列表式回答 - 适用于推荐类查询

        Args:
            query: 用户查询
            context_docs: 上下文文档列表

        Returns:
            列表式回答
        """
        if not context_docs:
            return "抱歉，没有找到相关的菜品信息。"

        dishes = []
        seen = set()
        for doc in context_docs:
            dish_name = doc.metadata.get('dish_name', '未知菜品')
            if dish_name not in seen:
                seen.add(dish_name)
                dishes.append(doc)
        requested = re.search(r'([1-9]|[一二两三四五六七八九十])\s*(?:个|道|种|款)', query)
        numbers = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
                   '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
        limit = (int(requested[1]) if requested[1].isdigit() else numbers[requested[1]]) if requested else 3
        rows = []
        for i, doc in enumerate(dishes[:limit], 1):
            details = [doc.metadata[key] for key in ('category', 'difficulty')
                       if doc.metadata.get(key) and doc.metadata[key] != '未知']
            if doc.metadata.get('preference_evidence'):
                details.append(doc.metadata['preference_evidence'])
            suffix = f"（{'，'.join(details)}）" if details else ''
            rows.append(f"{i}. {doc.metadata['dish_name']}{suffix}")
        return '为你推荐：\n' + '\n'.join(rows) + '\n\n可以回复编号或菜名，我再给你做法。'

    def generate_basic_answer_stream(self, query: str, context_docs: List[Document], history=None):
        """
        生成基础回答 - 流式输出

        Args:
            query: 用户查询
            context_docs: 上下文文档列表

        Yields:
            生成的回答片段
        """
        context = self._build_context(context_docs)
        history_text = self._format_history(history)

        prompt = ChatPromptTemplate.from_template("""
涉及食谱事实的句子请引用对应的 [S1]、[S2] 等来源标记。只使用本轮给出的标记。
你是一位专业的烹饪助手。请根据以下食谱信息回答用户的问题。

以本轮解析的问题和食谱信息为准。用户引用旧推荐中的另一项时，切换到该项；
历史讨论的菜谱不代表整个知识库的范围，不应声称上一轮的菜是唯一有资料的菜。

之前的对话（仅用于理解追问，不作为食谱事实来源）:
{history}

用户问题: {question}

相关食谱信息:
{context}

请根据原文提供实用的回答，引用菜名或来源。不应把未记录的食材搭配或因果关系当成事实；信息不足请说明。

回答:""")

        model = self.request_llm()
        chain = (
            {"question": RunnablePassthrough(), "context": lambda _: context, "history": lambda _: history_text}
            | prompt
            | model
            | StrOutputParser()
        )

        yield from cancellable_stream(chain, query, model)

    def generate_step_by_step_answer_stream(self, query: str, context_docs: List[Document], history=None):
        """
        生成详细步骤回答 - 流式输出

        Args:
            query: 用户查询
            context_docs: 上下文文档列表

        Yields:
            详细步骤回答片段
        """
        context = self._build_context(context_docs)
        history_text = self._format_history(history)

        prompt = ChatPromptTemplate.from_template("""
涉及食谱事实的句子请引用对应的 [S1]、[S2] 等来源标记。只使用本轮给出的标记。
你是一位专业的烹饪导师。请根据食谱信息，为用户提供详细的分步骤指导。

以本轮解析的问题和食谱信息为准。用户引用旧推荐中的另一项时，切换到该项；
历史讨论的菜谱不代表整个知识库的范围，不应声称上一轮的菜是唯一有资料的菜。

之前的对话（仅用于理解追问，不作为食谱事实来源）:
{history}

用户问题: {question}

相关食谱信息:
{context}

请灵活组织回答，建议包含以下部分（可根据实际内容调整）：

## 🥘 菜品介绍
[简要介绍菜品特点和难度]

## 🛒 所需食材
[列出主要食材和用量]

## 👨‍🍳 制作步骤
[详细的分步骤说明，每步包含具体操作和大概所需时间]

## 💡 制作技巧
[仅在有实用技巧时包含。优先使用原文中的实用技巧，如果原文的"附加内容"与烹饪无关或为空，可以基于制作步骤总结关键要点，或者完全省略此部分]

注意：
- 优先遵守用户要求的回答长度和格式，并引用使用的菜名或来源
- 只根据原文和明确的图关系证据回答；缺失的时间、用量、营养或因果信息请说明，不要编造
- 根据实际内容灵活调整结构
- 不要强行填充无关内容或重复制作步骤中的信息
- 重点突出实用性和可操作性
- 如果没有额外的技巧要分享，可以省略制作技巧部分

回答:""")

        model = self.request_llm()
        chain = (
            {"question": RunnablePassthrough(), "context": lambda _: context, "history": lambda _: history_text}
            | prompt
            | model
            | StrOutputParser()
        )

        yield from cancellable_stream(chain, query, model)

    @staticmethod
    def _format_history(history):
        if not history:
            return '无'
        return '\n'.join(('用户' if message.type == 'human' else '助手') + ': ' + message.content
                         for message in history)

    def _build_context(self, docs: List[Document], max_length: int = None) -> str:
        """
        构建上下文字符串
        
        Args:
            docs: 文档列表
            max_length: 最大长度
            
        Returns:
            格式化的上下文字符串
        """
        if not docs:
            return "暂无相关食谱信息。"
        max_length = max_length or (self.config.context_max_chars if self.config else 18000)
        
        context_parts = []
        current_length = 0
        
        for i, doc in enumerate(docs, 1):
            # 添加元数据信息
            metadata_info = f"【食谱 {i}】[S{i}]"
            if 'dish_name' in doc.metadata:
                metadata_info += f" {doc.metadata['dish_name']}"
            if 'category' in doc.metadata:
                metadata_info += f" | 分类: {doc.metadata['category']}"
            if 'difficulty' in doc.metadata:
                metadata_info += f" | 难度: {doc.metadata['difficulty']}"
            metadata_info += f" | 来源: {doc.metadata.get('source', '未知')}"
            
            # 构建文档文本
            doc_text = f"{metadata_info}\n{doc.page_content}\n"
            evidence = doc.metadata.get('graph_evidence', [])
            if evidence:
                doc_text += '\n图关系证据:\n' + '\n'.join(evidence) + '\n'
            
            # 检查长度限制
            if current_length + len(doc_text) > max_length:
                remaining = max_length - current_length
                if remaining > len(metadata_info) + 50:
                    context_parts.append(doc_text[:remaining] + '\n[原文因上下文预算截断]')
                break
            
            context_parts.append(doc_text)
            current_length += len(doc_text)
        divider = "\n" + "="*50 + "\n"
        return divider + divider.join(context_parts)
