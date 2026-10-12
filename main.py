"""C9-inspired recipe Graph RAG with Neo4j + Qdrant."""
import argparse
import json
import logging
from config import DEFAULT_CONFIG
from langchain_core.documents import Document
from rag_modules.conversation import ConversationMemory
from rag_modules import (GraphDataPreparationModule, QdrantIndexConstructionModule,
    RetrievalOptimizationModule, GenerationIntegrationModule, GraphRAGRetrieval, IntelligentQueryRouter)

logger = logging.getLogger(__name__)


class RecipeRAGSystem:
    def __init__(self, config=None):
        self.config = config or DEFAULT_CONFIG
        self.data_module = self.index_module = self.generation_module = None
        self.retrieval_module = self.query_router = None
        self.system_ready = False
        self.conversation = ConversationMemory(self.config.conversation_max_turns,
                                               self.config.conversation_max_chars)

    def initialize_system(self):
        self.data_module = GraphDataPreparationModule(self.config)
        self.data_module.driver.verify_connectivity()
        self.index_module = QdrantIndexConstructionModule(self.config)
        self.index_module.client.get_collections()
        self.generation_module = GenerationIntegrationModule(config=self.config)

    def build_knowledge_base(self, import_local=False, rebuild=False):
        self.system_ready = False
        if import_local:
            logger.info('导入本地食谱: %s', self.data_module.import_local_recipes())
        docs = self.data_module.load_documents()
        if not docs:
            logger.info('Neo4j 没有 Recipe，导入本地 Markdown 食谱')
            self.data_module.import_local_recipes()
            docs = self.data_module.load_documents()
        chunks = self.data_module.chunk_documents(self.config.chunk_size, self.config.chunk_overlap)
        if rebuild or self.index_module.load_index(chunks) is None:
            self.index_module.build_vector_index(chunks)
        self.retrieval_module = RetrievalOptimizationModule(self.index_module, chunks)
        self.graph_rag_retrieval = GraphRAGRetrieval(self.config, self.data_module)
        self.query_router = IntelligentQueryRouter(self.retrieval_module, self.graph_rag_retrieval, self.config)
        self.system_ready = True
        logger.info('知识库就绪: %s 个食谱，%s 个文本块', len(docs), len(chunks))

    def _extract_filters_from_query(self, query):
        filters = {}
        for category in self.data_module.get_supported_categories():
            if category in query:
                filters['category'] = category
                break
        for difficulty in sorted(self.data_module.get_supported_difficulties(), key=len, reverse=True):
            if difficulty in query:
                filters['difficulty'] = difficulty
                break
        return filters

    def retrieve(self, question, strategy='auto'):
        if not self.system_ready:
            raise ValueError('请先初始化系统并构建知识库')
        if not question.strip():
            raise ValueError('问题不能为空')
        candidate_k = self.config.top_k * 3 if self.config.enable_parent_doc_retrieval else self.config.top_k
        filters = self._extract_filters_from_query(question)
        names = self.graph_rag_retrieval.extract_entities(question)['recipes']
        if names and not any(word in question for word in ('相似', '类似', '搭配', '关系', '推荐')):
            filters['parent_id'] = [d.metadata['parent_id'] for d in self.data_module.documents
                                    if d.metadata['dish_name'] in names]
        docs, analysis = self.query_router.route_query(question, top_k=candidate_k, filters=filters,
                                                      strategy=strategy)
        if self.config.enable_parent_doc_retrieval:
            parents = {d.metadata['parent_id']: d for d in self.data_module.documents}
            result, seen = [], set()
            for i, doc in enumerate(docs):
                key = doc.metadata['parent_id']
                if key in seen:
                    continue
                seen.add(key)
                if len(result) < self.config.parent_doc_top_n and key in parents:
                    parent = parents[key]
                    doc = Document(page_content=parent.page_content[:self.config.parent_doc_max_chars],
                                   metadata={**parent.metadata, **doc.metadata, 'doc_type': 'parent'})
                result.append(doc)
                if len(result) >= self.config.top_k:
                    break
            docs = result
        return docs, analysis

    def ask_question_with_routing(self, question, stream=False, explain_routing=False, strategy='auto'):
        if not self.system_ready:
            raise ValueError('请先初始化系统并构建知识库')
        if not question.strip():
            raise ValueError('问题不能为空')
        resolution = self.conversation.resolve(question,
            [d.metadata['dish_name'] for d in self.data_module.documents], self.generation_module.llm)
        if resolution.clarification:
            return resolution.clarification, None
        history = self.conversation.messages()
        if explain_routing and resolution.query != question:
            print(f'追问解析: {resolution.query}')
        docs, analysis = self.retrieve(resolution.query, strategy)
        if explain_routing:
            print(f'检索策略: {analysis.recommended_strategy.value}；{analysis.reasoning}')
        if not docs:
            return '没有找到符合条件的食谱，请调整菜品、食材或过滤条件。', analysis
        if any(word in resolution.query for word in ('怎么做', '步骤', '制作方法', '做法')):
            generate = (self.generation_module.generate_step_by_step_answer_stream if stream
                        else self.generation_module.generate_step_by_step_answer)
        else:
            generate = (self.generation_module.generate_basic_answer_stream if stream
                        else self.generation_module.generate_basic_answer)
        generation_query = question if resolution.query == question else f'用户原始提问：{question}\n已解析的追问：{resolution.query}'
        answer = generate(generation_query, docs, history=history)
        if not stream:
            self.conversation.remember(question, answer, docs)
            return answer, analysis
        def remember_stream():
            parts = []
            # Commit only after normal exhaustion; interrupted/failed output is not a completed turn.
            for part in answer:
                parts.append(part)
                yield part
            self.conversation.remember(question, ''.join(parts), docs)
        return remember_stream(), analysis

    def clear_history(self):
        self.conversation.clear()

    def ask_question(self, question, stream=False):
        return self.ask_question_with_routing(question, stream)[0]

    def statistics(self):
        return {**self.data_module.get_statistics(), **self.index_module.get_collection_stats(),
                'routing': self.query_router.get_route_statistics() if self.query_router else {}}

    def run_interactive(self):
        print('Neo4j + Qdrant 食谱助手（stats 查看统计，clear 清空对话，quit 退出）')
        while True:
            try:
                question = input('\n您的问题: ').strip()
                if question.lower() in {'quit', 'exit', '退出'}:
                    break
                if not question:
                    continue
                if question.lower() == 'stats':
                    print(json.dumps(self.statistics(), ensure_ascii=False, indent=2))
                    continue
                if question.lower() in {'clear', '清空对话'}:
                    self.clear_history()
                    print('对话已清空，可以开始新的话题。')
                    continue
                answer, _ = self.ask_question_with_routing(question, stream=True, explain_routing=True)
                print_answer(answer)
            except KeyboardInterrupt:
                break
            except Exception:
                logger.exception('问答失败')

    def close(self):
        if self.index_module:
            self.index_module.close()
        if self.data_module:
            self.data_module.close()


AdvancedGraphRAGSystem = RecipeRAGSystem


def print_answer(answer):
    if isinstance(answer, str):
        print(answer)
    else:
        for part in answer:
            print(part, end='', flush=True)
        print()


def main():
    parser = argparse.ArgumentParser(description='Neo4j + Qdrant 食谱 Graph RAG')
    parser.add_argument('--check', action='store_true', help='只验证数据库和模型连接')
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--import-local', action='store_true', help='更新项目管理的本地食谱节点')
    parser.add_argument('--rebuild', action='store_true', help='重新向量化当前版本，不删除其他集合')
    parser.add_argument('--question')
    parser.add_argument('--retrieve-only', action='store_true', help='只显示检索内容，不调用生成模型')
    parser.add_argument('--no-stream', action='store_true')
    parser.add_argument('--strategy', default='auto', choices=['auto', 'hybrid_traditional', 'graph_rag', 'combined'])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    system = RecipeRAGSystem()
    try:
        system.initialize_system()
        if args.check:
            vector = system.index_module.embeddings.embed_query('食谱连接检查')
            reply = system.generation_module.llm.invoke('请只回复：连接正常')
            print(f'Neo4j、Qdrant、嵌入和生成模型连接正常；向量维度={len(vector)}；生成={reply.content}')
            print('Recipe 数量:', system.data_module.run('MATCH (r:Recipe) RETURN count(r) AS count')[0]['count'])
            return
        system.build_knowledge_base(args.import_local, args.rebuild)
        if args.build_only:
            print(json.dumps(system.statistics(), ensure_ascii=False, indent=2))
        elif args.question and args.retrieve_only:
            docs, analysis = system.retrieve(args.question, args.strategy)
            print('检索策略:', analysis.recommended_strategy.value)
            for doc in docs:
                print(json.dumps(doc.metadata, ensure_ascii=False))
                print(doc.page_content)
        elif args.question:
            answer, _ = system.ask_question_with_routing(args.question, not args.no_stream, True, args.strategy)
            print_answer(answer)
        else:
            system.run_interactive()
    finally:
        system.close()


if __name__ == '__main__':
    main()
