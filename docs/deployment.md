# 运行与部署

## 环境

使用 Python 3.11+（配置使用标准库 tomllib）。安装 `requirements.txt`，确认 Neo4j 5 和 Qdrant 1.14 系列在运行。模型可用 Ollama 或 OpenAI 兼容接口。默认模型比较大，请先确认机器能够运行，不在服务启动时自动下载模型。

已有 Docker 数据库可以直接填写连接地址。新环境可自行创建：

```powershell
# 将 <your-password> 换成自己的密码，并在 .env 中设置相同值
docker run -d --name cook-neo4j -p 7474:7474 -p 7687:7687 `
    -e NEO4J_AUTH=neo4j/<your-password> -v cook-neo4j-data:/data neo4j:5
docker run -d --name cook-qdrant -p 6333:6333 `
    -v cook-qdrant-data:/qdrant/storage qdrant/qdrant:v1.14.1
```

已有相同端口的容器时直接复用或选其他端口。不要删除已有业务数据来腾出端口。Neo4j 导入会增加本项目标签和 `local-c9:` ID；Qdrant 构建新集合不会删除旧集合。定期备份 Neo4j 和 Qdrant 的持久化卷。

## 配置

优先顺序：进程环境变量 → 项目 `.env` → TOML / 代码默认值。`settings.toml` 相对数据和索引目录以项目根目录解析；自定义配置路径由 `RAG_CONFIG` 指定。直接构造 `RAGConfig(...)` 时参数用于该实例。

| 环境变量 | 用途 | 默认 / 示例 |
| --- | --- | --- |
| NEO4J_URI | Bolt 地址 | bolt://localhost:7687 |
| NEO4J_USERNAME | 用户名 | neo4j；也兼容 NEO4J_USER |
| NEO4J_PASSWORD | 数据库密码 | 必须自行设置 |
| NEO4J_DATABASE | 数据库 | neo4j |
| NEO4J_CONNECTION_TIMEOUT | 连接超时秒数 | 10 |
| NEO4J_MAX_CONNECTION_POOL_SIZE | 连接池大小 | 50 |
| NEO4J_MAX_CONNECTION_LIFETIME | 连接最大存活秒数 | 3600 |
| QDRANT_URL | HTTP 地址 | http://localhost:6333 |
| QDRANT_API_KEY | Qdrant 密钥 | 本地无鉴权时留空 |
| QDRANT_COLLECTION_NAME | 集合名前缀 | recipe_c9，实际名附数据指纹 |
| LLM_MODEL_ID / EMBED_MODEL_NAME | 生成 / 嵌入模型 | qwen3.5:9b / qwen3-embedding:8b |
| LLM_MODEL_TYPE / EMBED_MODEL_TYPE | 客户端类型 | ollama 或 openai |
| LLM_BASE_URL / EMBED_BASE_URL | 模型服务地址 | http://localhost:11434 |
| LLM_API_KEY / EMBED_API_KEY | 兼容接口密钥 | 本地 Ollama 可写 ollama |
| EMBED_BATCH_SIZE | 嵌入批次 | 8 |
| API_HOST / API_PORT | 服务监听地址 | 127.0.0.1 / 8000 |
| HISTORY_DB_PATH | 网页历史 SQLite 文件 | runtime/history.sqlite3 |
| APP_API_KEY | /ask 的 Bearer key | 空值表示关闭鉴权 |
| RETRIEVAL_MODE | 检索模式 | recipe 或 baseline |
| RAG_CONFIG | TOML 文件 | 项目根目录 settings.toml |

localhost / 127.0.0.1:11434 会自动使用 Ollama 原生接口，即使 URL 带 `/v1`。远程 Ollama 请设 provider=ollama；远程 OpenAI 兼容接口请设 provider=openai。嵌入模型更换后必须重新构建对应集合；生成模型可独立更换。

主要 TOML 字段：

| 分组 | 参数 |
| --- | --- |
| rag | data_path、index_save_path、top_k、retrieval_mode、chunk_size、chunk_overlap |
| rag | parent_doc_top_n、parent_doc_max_chars、context_max_chars、enable_parent_doc_retrieval |
| rag | conversation_max_turns、conversation_max_chars、temperature、max_tokens、max_graph_depth |
| rag | ollama_num_ctx、embedding_num_ctx、source_base_url |
| service | host、port、max_sessions、session_ttl_seconds、queue_timeout_seconds |
| logging | level、file、max_bytes、backup_count |

`source_base_url` 默认指向本 GitHub 仓库的 main 分支；部署自己的分支或私有仓库时改为自己的原文链接地址，建议使用固定 commit 以保持引用稳定。密钥字段不能写进 TOML。图原文不具备本地仓库路径时引用使用 `neo4j:` 标识。

## 启动、更新和重启

```powershell
.\.venv\Scripts\python.exe main.py --check
.\.venv\Scripts\python.exe main.py --import-local --build-only
.\.venv\Scripts\python.exe serve.py
```

看到 `Application startup complete` 后检查 `/health`。API 启动加载整份知识库和 BM25 索引，完成后才接受问题。若图库没有 Recipe，会自动导入本地食谱；若集合不存在或校验不通过，自动构建索引。已有外部 Recipe 时不会擅自替换全库，显式 `--import-local` 添加/更新本地食谱。

修改 Markdown 后，先停服务（Ctrl+C），执行 `--import-local --build-only`，再启动。仅修改检索或服务参数时重启即可。修改模型、分块参数或原文会改变索引指纹，旧集合留存供人工核查；确认不再使用后自行清理本项目的旧集合。不要在多个进程中同时构建同一集合。

服务器部署使用 `python serve.py`，由 systemd、Windows 服务工具或容器负责进程重启。使用 **单 worker**，不要直接加 `--workers 4`：旧 /ask 会话、模型锁与流式取消信号都在进程内，网页历史则保存到 SQLite。默认等待锁 1 秒；/ask 满载返回 429，SSE 已建立时用流内 busy 事件表示繁忙。适合单机模型服务，不承诺多用户高吞吐。

Windows 本地也可以使用后台脚本：`./scripts/start-api.ps1` 启动，`./scripts/stop-api.ps1` 停止，再次执行启动脚本完成重启。进程记录保存在 `logs/api-processes.json`，停止前会核对进程命令属于当前项目。后台停止会结束进程，应在没有进行中的请求时操作；日志在 `logs/` 中，不弹出终端窗口。

## API 容器

```powershell
docker build -t cook-rag:local .
# Windows Docker Desktop：数据库和 Ollama 仍在宿主机的已发布端口
docker run -d --name cook-rag-api -p 8000:8000 --env-file .env `
    -e API_HOST=0.0.0.0 `
    -e NEO4J_URI=bolt://host.docker.internal:7687 `
    -e QDRANT_URL=http://host.docker.internal:6333 `
    -e LLM_BASE_URL=http://host.docker.internal:11434 `
    -e EMBED_BASE_URL=http://host.docker.internal:11434 `
    -e LLM_MODEL_TYPE=ollama -e EMBED_MODEL_TYPE=ollama `
    -v cook-rag-index:/app/vector_index -v cook-rag-logs:/app/logs `
    -v cook-rag-history:/app/runtime cook-rag:local
```

容器中的 localhost 指向容器自身。Linux 可把服务接入同一 Docker 网络，用数据库服务名连接；访问宿主机时需配置 `host-gateway`。Ollama 也必须监听容器可访问的地址。不要把宿主机 `.env` 拷进镜像，`.dockerignore` 已排除秘密、虚拟环境、索引和日志。镜像以普通用户运行。

Dockerfile 尚未在本次 Windows 验证中构建，部署前应在目标平台构建并跑健康检查。已配置的 GitHub Actions 执行离线单元测试，不自动启动真实数据库，也不自动部署。

## 对外访问

本地默认没有鉴权并只监听回环地址。开放网络访问时设置 `APP_API_KEY`，在反向代理上启用 HTTPS、请求体大小限制、速率限制和合适的生成等待超时。API key 只用于共享服务访问，不能替代用户身份与租户隔离。不要直接开放数据库端口到公网。

网页历史、已完成轮次的菜单和已推荐候选会持久化；旧 /ask 会话与执行中的模型请求不持久化。备份聊天数据库时，停机后复制 runtime/history.sqlite3，运行中使用 SQLite backup 接口，不能只复制可能带 WAL 的主文件。runtime 目录包含问题与回答，不要提交到公开仓库。浏览器 Cookie 是记录归属标识，清除后不能自动找回该浏览器的历史。

反向代理需为 /ask/stream 关闭响应缓冲和缓存（例如 Nginx proxy_buffering off），设置足够的读取超时；否则浏览器可能等待整段内容才显示。停止生成会发送取消信号并关闭生成器，但底层模型或检索正在等待响应时不能保证立即释放。

日志轮转记录请求 ID、方法、路径、状态和耗时，应用错误仅记录异常类型；不记录请求正文和凭据。流式 HTTP 耗时是响应头建立耗时，不代表整段生成耗时。排错时使用 request_id 关联日志。

## 常见问题

| 现象 | 检查 |
| --- | --- |
| ModuleNotFoundError | 用运行服务的同一个 `.venv` 解释器安装 requirements；不要混用系统 pip |
| Neo4j 连接失败 | 容器是否运行、7687 是否发布、URI/密码/数据库是否正确 |
| Qdrant 连接失败 | 6333 是否可达、URL 是否容器误用 localhost、API key 是否匹配 |
| health 为 model_missing | `ollama list` 是否有与配置完全一致的模型名 |
| 启动需要等待 | 首次索引构建；看向量化进度，不要同时启动另一构建进程 |
| 502 backend_error | 根据 request_id 查日志；检查模型驻留、连接及显存/内存 |
| 429 busy | 另一个请求正在执行；退避重试或减少并发 |
| 编号追问没有上下文 | 是否带回相同 session_id；是否过期、重启或 reset |
| 修改食谱后未生效 | 重新导入本地食谱并重启服务 |

清淡过滤和图食材提取都是保守规则，不是营养评估或过敏安全保证。生成模型仍可能错误解释原文，重要用量应检查文档引用。
