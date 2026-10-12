# 食谱 Graph RAG：Neo4j + Qdrant

基于 [Datawhale all-in-rag C9](https://github.com/datawhalechina/all-in-rag/tree/main/code/C9) 的图检索、查询路由和融合检索架构，将本地 C8 项目升级为 Neo4j + Qdrant 双引擎。参考版本：`cec956e80ae28d6c0ca84c9d39dddcf61ad633fd`。

本实现使用实际图关系证据，替代参考代码中的空路径查询和占位推理；向量数据库使用 **Qdrant**。不再依赖 Milvus 或 FAISS。原 C8 的 Markdown 分块、父文档回填和流式回答保留并完善。

## 安装和运行

使用 Python 3.10+，从项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 已有 .env 时无需覆盖；新环境可复制 .env.example 后填写连接参数。
.\.venv\Scripts\python.exe main.py --check
.\.venv\Scripts\python.exe main.py --build-only
.\.venv\Scripts\python.exe main.py
```

现有 Docker 的 Neo4j（7687）和 Qdrant（6333）可直接使用。Ollama 需安装配置的模型；默认是 `qwen3-embedding:8b` 和 `qwen3.5:9b`。本机原先的 `python` 命令指向不可用的 Windows 应用别名，已准备项目 `.venv`，可直接执行上面的 `.venv\Scripts\python.exe` 命令。

```powershell
# 制作步骤：中文 BM25 + 向量检索
.\.venv\Scripts\python.exe main.py --question "西红柿炒鸡蛋怎么做"
# 食材交集：图检索
.\.venv\Scripts\python.exe main.py --question "鸡蛋和西红柿能做哪些菜" --retrieve-only
# 排除食材、分类过滤
.\.venv\Scripts\python.exe main.py --question "不要鸡蛋的素菜" --retrieve-only
# 共享食材的两跳关系 + 原文融合
.\.venv\Scripts\python.exe main.py --question "与西红柿炒鸡蛋相似的菜" --strategy combined
```

交互模式支持 `stats`、`clear`（清空对话）、`quit`。`--no-stream` 返回完整回答；`--retrieve-only` 显示检索文档、来源和图关系证据；`--strategy` 支持 `auto`、`hybrid_traditional`、`graph_rag`、`combined`。

## 多轮对话

同一次交互会话会记住近期问题和实际回答。例如先问“推荐几个素菜”，再问“第二个怎么做”或“它需要多少盐”。编号按上一条实际回答解析，不使用检索排序代替；普通回答和完整流式回答都会写入历史，中断或失败的流式输出不作为完整的一轮。

一般追问通过模型改写成独立检索问题，回答模型也能看到近期对话；明确的新菜名可以切换话题。没有历史、编号无效或指代无法唯一确定时，会请求提供菜名或编号。输入 `clear` 开始新话题。

记忆保存在当前 `RecipeRAGSystem` 实例中，默认最多 6 轮、6000 字符，可在 `config.py` 调整 `conversation_max_turns` / `conversation_max_chars`。退出程序或每次单独运行 `--question` 会开始新会话；暂不跨进程保存。直接调用 `retrieve()` 仍是无会话状态的检索接口；多轮 API 使用同一实例的 `ask_question()` 或 `ask_question_with_routing()`。

## 环境配置

`.env` 总是从项目根目录加载，已有进程环境变量优先。实际密钥和密码只保存在 `.env`，不会进入 Git。

| 环境变量 | 用途 |
| --- | --- |
| `LLM_MODEL_ID`、`LLM_BASE_URL`、`LLM_API_KEY` | 生成模型及接口 |
| `LLM_MODEL_TYPE` | `ollama`（默认）或 `openai`（兼容接口） |
| `EMBED_MODEL_TYPE`、`EMBED_MODEL_NAME`、`EMBED_BASE_URL`、`EMBED_API_KEY` | 嵌入模型及接口 |
| `EMBED_BATCH_SIZE` | 向量化批次，默认 8 |
| `QDRANT_URL`、`QDRANT_API_KEY` | Qdrant 连接；无认证时 key 留空 |
| `QDRANT_COLLECTION_NAME` | 项目集合前缀，默认 `recipe_c9` |
| `NEO4J_URI`、`NEO4J_USERNAME`、`NEO4J_PASSWORD`、`NEO4J_DATABASE` | 图数据库连接，也兼容 `NEO4J_USER` |
| `NEO4J_MAX_CONNECTION_LIFETIME`、`NEO4J_MAX_CONNECTION_POOL_SIZE`、`NEO4J_CONNECTION_TIMEOUT` | Neo4j 连接池和超时 |

本机 Ollama `11434/v1` 地址自动适配原生接口，保留独立的嵌入上下文限制和关闭思考输出的设置。远程 OpenAI 兼容服务按配置调用。`config.py` 可调整 `top_k`、分块长度、上下文预算、父文档回填数量等；`to_dict()` 会隐藏密钥。

## 数据和索引

1. Neo4j 已有 `Recipe` 时直接读取全部菜谱，不采用参考代码的 `nodeId >= '200000000'` 限制。支持 `REQUIRES` → `Ingredient`、`CONTAINS_STEP` → `CookingStep`、`BELONGS_TO_CATEGORY` → `Category`。已有完整 `content` 优先用于回答，缺失时由节点属性、食材和有序步骤构建文档。
2. Neo4j 没有 `Recipe` 时，从 `data/C8/cook` 自动导入，跳过模板。导入器为项目节点增加 `LocalRecipeC9` 等标签和 `local-c9:` ID，保留其他应用的数据。`--import-local` 幂等更新本地菜谱节点；修改本地 Markdown 后使用此选项同步，再构建向量。
3. Markdown 原料清单以规则抽取食材名称，保留原清单文字作为用量证据；步骤按原文顺序导入。它不执行 LLM 本体抽取或营养/因果推断。可选食材也会记录在图中；过敏与排除结果以已记录的原料关系为依据，不能覆盖原文未列出的成分。
4. Qdrant 向量维度由实际嵌入输出确定。集合名包含模型、上下文和分块数据的指纹，模型或数据改变会构建新版本，其他已有集合保留。`vector_index/` 中仅保存完成标记；复用时验证实际点数，未完成的索引可用确定性 ID 重新写入。`--rebuild` 重新写入当前版本，不清空数据库。
5. 自动路由按图中已知实体和问题词选择检索策略。食材交集和排除条件使用参数化 Cypher；关系查询支持菜谱—食材—菜谱两跳扩展。中文 BM25 使用 jieba，关键词和向量在过滤后以 RRF 融合，组合检索按菜谱融合。不存在匹配的食材约束时返回空结果，避免回退到不满足条件的推荐。
6. 父文档默认启用，避免仅凭步骤片段回答。超过上下文预算时截断并标记，保留来源；图检索附带真实关系证据。深度为 1 时关闭两跳扩展，默认深度为 2。尚未实现任意本体、多跳因果推理或参考目录中的离线 AI agent 工作流。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --check
```

离线测试使用内存 Qdrant 和固定嵌入，覆盖索引复用、重复写入、数据/模型版本变化、不完整索引、元数据过滤、稳定分块、图查询路由、排除条件、长文档上下文和配置脱敏。实际 Docker/Ollama 验证记录见 [VALIDATION.md](VALIDATION.md)。
