# API

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

另返回 `auth_required`、`instance_id` 和 `session_ttl_seconds`。网站用于显示访问设置、识别服务重启及清理已过期的对话；instance_id 只是进程标识，不是密钥。

## GET /recipes/{document_id}

返回当前知识库中该食谱的 `title`、完整 Markdown `content` 和 `source`。网站从 `/ask` 的 sources 获取 document_id，再调用此接口打开原文弹窗。不会接收或读取任意文件路径。开启 APP_API_KEY 后与 `/ask` 使用相同 Bearer 鉴权。找不到返回 404 `recipe_not_found`，知识库未就绪返回 503。

## 错误与并发

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

429 带 `Retry-After: 2`；调用方应适度退避。Uvicorn 在连接数超过 64 时也可能直接返回自身的 503，这类响应不保证使用应用错误格式。模型执行串行；`/health` 不占用模型锁。API 不提供 SSE，CLI 可流式生成。

应用错误不会回传原始异常、请求正文或凭据。`request_id` 同时出现在响应体、`X-Request-ID` 和日志中，用于关联排查。
