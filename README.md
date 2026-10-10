# 本地 Ollama 食谱 RAG

嵌入和回答生成均通过本地 Ollama 运行，无需云端 API Key。

## 运行

在项目目录、Python 3.10 或更新版本的环境中安装依赖：

```powershell
python -m pip install -r requirements.txt
```

准备模型：

```powershell
ollama pull qwen3-embedding:8b
ollama pull qwen3.5:9b
ollama list
```

确保 Ollama 正在运行。Windows 桌面版通常会启动服务；如果未启动，另开终端执行 `ollama serve`，保持该终端运行。

```powershell
python main.py
```

第一次启动会向量化 `data/C8/cook` 下的食谱并保存到 `vector_index`，可能需要较长时间；之后启动复用索引。原 Hugging Face 索引或其他嵌入模型的索引会自动重建。

## 配置

修改 `config.py` 中的 `RAGConfig`：

- `embedding_model`：默认 `qwen3-embedding:8b`。
- `llm_model`：默认 `qwen3.5:9b`。
- `ollama_base_url`：默认 `http://localhost:11434`，使用 Ollama 原生接口地址，不加 `/v1`。
- `ollama_num_ctx`：默认 16384，控制生成模型上下文窗口。
- `embedding_num_ctx`：默认 4096，单独限制嵌入模型上下文，避免采用模型默认的超大窗口。
- `embedding_batch_size`：默认 8，每次只向量化一小批文本块，构建时显示进度；资源较紧张时可改为 1。
- `max_tokens`：默认 2048，对应 Ollama 的 `num_predict`。

生成模型关闭思考输出，以便查询分类、查询重写和流式回答直接返回结果。现有 `.env` 中的云端密钥不再读取。

连接失败时检查 Ollama 服务和地址；提示模型不存在时执行对应的 `ollama pull`。内存不足时可降低上下文窗口或换用较小的本地模型。

如果 `/api/embed` 返回内部随机端口（例如 `127.0.0.1:5742/tokenize`）连接被拒绝，说明 Ollama 内部模型进程不可用。该端口不是项目配置的服务地址。先重新运行；若仍失败，可执行 `ollama stop qwen3-embedding:8b` 后重试，或退出并重新打开 Ollama。项目已使用小批次和独立的嵌入上下文限制来降低资源占用。
