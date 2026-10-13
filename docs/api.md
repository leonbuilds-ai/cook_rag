# API

`POST /suggestions` 接收 `{ "exclude_ids": [] }`（最多 500 项），返回 `suggestions` 和 `reset_topics`。从早餐、原文符合清淡筛选的菜品、简单家常菜三类随机选取实际食谱；优先避开排除 ID，耗尽才重新开始。结果包含真实 document_id、title、topic 与可直接提问的 question。

`POST /ask/stream` 可携带 `exclude_ids`（最多 500 项），仅用于推荐请求；网页保存浏览器近期推荐，并在新对话中发送这些 ID。topic 新增 `drinks`。响应仍保留结构化 sources 和原始引用，网页默认隐藏正文标记并收起依据，不改变 API 数据。

启动：`python serve.py`。OpenAPI：`GET /openapi.json`，交互文档：`GET /docs`。

日常使用访问网站首页 `GET /`；`/docs` 保留为开发者调试页。`/assets` 仅提供 web 目录的静态文件。

## POST /ask

`Content-Type: application/json`。设置 `APP_API_KEY` 后，请求必须包含 `Authorization: Bearer <key>`。

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| question | 是 | 去掉首尾空格后非空；最多 2000 字符 |
| session_id | 否 | UUID；不传时生成新会话 |
| strategy | 否 | 默认 auto；或 hybrid_traditional / graph_rag / combined |
| reset | 否 | 默认 false；true 重置指定会话 |

成功响应：`answer`、`sources`、`session_id`、`resolved_query`、`retrieval_strategy`、`kind`、`request_id`、`elapsed_ms`。`kind` 包括 answer / recommendation / clarification / no_results，以及 greeting / thanks / acknowledgment / help / catalog。无检索的回应没有来源，策略字段为 null。

来源 ID 对应本轮文档，不能跨轮复用。`path` 是仓库相对路径，`url` 是配置的仓库链接；只有图数据库的食谱使用 `neo4j:` 标识，URL 为 null。生成提示要求引用 `[S1]` 等标记，服务另附确定的来源列表；引用用于追溯文档，不能自动证明模型生成的每句话都正确。推荐只返回已展示菜品的来源。

会话按 UUID 隔离，无持久化。默认最多 200 个、空闲 3600 秒后过期，满额时淘汰最久未使用的会话。过期、被淘汰或重启后传回旧 ID，将从空白会话开始。UUID 不是用户身份认证；单个全局 API key 也不是多租户权限系统。

## GET /health

就绪返回 200，启动失败或依赖异常返回 503：

```json
{
  "status": "ready",
  "dependencies": {"neo4j":"ok","qdrant":"ok","embedding":"ok","llm":"ok"},
  "recipes": 322,
  "retrieval_mode": "recipe"
}
```

检查图数据库查询、当前 Qdrant 集合和本地 Ollama 的模型列表，不执行生成。远程模型服务标记为 `configured`，调用能力以 `/ask` 的实际结果为准。启动异常时返回 `not_ready`，依赖异常返回 `degraded`。启动期间 Uvicorn 等待 lifespan 完成，健康检查可能尚不能连接。

另返回 `auth_required`、`instance_id` 和 `session_ttl_seconds`。网站用于显示连接状态与访问设置；session_ttl_seconds 仅适用于旧 `/ask` 会话，网页历史不随进程重启清除。instance_id 只是进程标识，不是密钥。

## GET /recipes/{document_id}

返回 `title`、完整 Markdown `content`、`source`、带本地图片地址的 `display_content` 与 `image` 描述。原文 content 保持不变。网站从 sources 获取 document_id，再调用此接口打开原文弹窗。不会读取任意文件路径。开启 APP_API_KEY 后使用相同 Bearer 鉴权。找不到返回 404 `recipe_not_found`，知识库未就绪返回 503。

## 网页聊天与 SSE

先 `POST /chats` 创建对话，将响应的 id 作为 chat_id；使用 HTTP Cookie jar 保持响应设置的 `cook_browser` Cookie。没有 Cookie 或 Cookie 与记录不匹配时返回 404。Cookie 用于浏览器归属隔离，不是账号登录。

| 方法与路径 | 用途 |
| --- | --- |
| GET /chats | 当前浏览器的最新 200 段对话；trash=true 查看最近删除 |
| POST /chats | 创建对话，201 返回记录 |
| GET /chats/{id} | 消息、状态、菜单记忆与 generating 标记 |
| PATCH /chats/{id} | JSON title 重命名，去空格后 1–60 字符 |
| DELETE /chats/{id} | 移到最近删除，可以恢复 |
| POST /chats/{id}/restore | 恢复已删除对话 |
| POST /chats/{id}/stop | 取消异步模型流，保存部分回答并释放锁；同步检索仍需等待返回 |
| POST /chats/import | 导入旧网页最多 30 轮 question / answer / document_ids；来源只接受当前知识库 ID |

`POST /ask/stream` 接收 JSON：question、chat_id（必填 UUID）、strategy、reset，以及可选 topic（breakfast / light / quick / dinner / drinks）与 exclude_ids。不使用旧 session_id。reset 重置对话上下文与该对话的候选记录，保留已有消息；浏览器近期推荐记录单独用于换批。成功响应为 `text/event-stream`，前端使用 Fetch 读取，因为 EventSource 不支持 POST。

事件顺序为 status → start → metadata → delta（多次）→ done。发生异常会发送 error 并结束；客户端需处理流内 error，即使 HTTP 已返回 200。

```text
event: delta
data: {"text":"将面条煮熟"}

event: done
data: {"answer":"完整答案……","sources":[],"chat_id":"…","message_id":"…","kind":"answer","request_id":"…"}
```

start 提供 user_message_id 与 message_id；metadata 提供 resolved_query、kind、sources、retrieval_strategy、topic。来源多一个 image 字段：url、kind（original / missing）、label。done 包含完整答案与最终来源。响应设置 Cache-Control: no-cache,no-transform 和 X-Accel-Buffering: no；代理也应关闭响应缓冲。

一段对话同时只能生成一次；生成期间重命名或删除返回 409 chat_busy。模型锁被其他请求占用时流内返回 busy，不追加用户消息。连接中断或 stop 请求让 worker 关闭生成器、保存部分回答并释放锁；底层请求未返回时可能需要等待。消息状态为 complete / streaming / stopped / error / interrupted。未完成回答不会进入后续模型记忆，服务启动会将残留 streaming 标记为 interrupted。

`GET /recipe-media/{relative}` 仅返回 data/cook 下有有效文件头的图片；路径越界、非图片和空文件返回 404。它与食谱数据一样是公开静态资源，不包含聊天内容。

## 错误与并发

### 食谱与异步任务

| 接口 | 用途 |
| --- | --- |
| GET /documents | 共享食谱目录，返回 ID、标题、来源、状态与创建时间 |
| POST /documents/upload | multipart/form-data，file 必填，category 可选；最多 50 MiB UTF-8 .md / .txt |
| GET /jobs | 当前浏览器最近 50 项任务 |
| GET /jobs/{id} | 已校验归属的任务状态、阶段、progress 与错误说明 |
| POST /jobs/{id}/retry | 重试失败任务，202 返回 queued 状态 |

上传需包含标题、食材或原料、步骤或操作章节。成功返回 202 与任务 id / document_id，后续查询 status：queued / running / complete / failed。相同浏览器相同内容的已完成文档返回 duplicate=true；不重复创建已排队任务。任务按浏览器 Cookie 隔离，文档入库后属于共享本机知识库。

413 file_too_large、422 unsupported_format / invalid_encoding / invalid_recipe / invalid_category、503 queue_unavailable 均给出中文提示。队列暂不可用时不把任务伪装为成功；已经提交到 PostgreSQL 但尚未投递的任务会在服务启动时恢复。

上传来源采用 upload: 文档标识，url 为 null，可用 /recipes/{document_id} 查看原文；不返回本机路径或不存在的 GitHub 链接。新答案的 payload 保存 evaluation_sample（实际问题与生成上下文）及 retrieval_trace（缓存 / 重排状态和集合），用于本机离线回放。/health 同时检查 PostgreSQL、Redis 与 Qdrant 实际点数据。

```json
{"error":{"code":"backend_error","message":"数据库或模型请求失败，请稍后重试。"},"request_id":"…"}
```

| HTTP | code | 原因 |
| --- | --- | --- |
| 401 | unauthorized | 缺失或错误的 API key |
| 422 | invalid_request | 问题为空、过长、UUID 或策略不合法 |
| 429 | busy | 其他请求占用模型执行锁，超过排队等待时间 |
| 502 | backend_error | 数据库、嵌入、追问或生成请求失败 |
| 503 | not_ready | 启动未完成或启动失败 |
| 500 | internal_error | 其他服务异常 |

429 带 `Retry-After: 2`；调用方应适度退避。Uvicorn 在连接数超过 64 时也可能直接返回自身的 503，这类响应不保证使用应用错误格式。模型执行串行；`/health` 不占用模型锁。`/ask` 返回完整 JSON，`/ask/stream` 与 CLI 支持流式生成。

应用错误不会回传原始异常、请求正文或凭据。`request_id` 同时出现在响应体、`X-Request-ID` 和日志中，用于关联排查。
