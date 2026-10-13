# 检索评估

标注文件：[queries.json](../evaluation/queries.json)。原始结果：[results.json](../evaluation/results.json)。运行时间、数据哈希、模型名称、集合名、逐条相关文档与排名都保存在报告中。

## 方法

24 条人工标注问题，12 条开发集、12 条测试集。覆盖菜名、常见别名、用量、步骤、食材描述和部分多相关文档问题。标注文件在实验前写入，相关文档由仓库实际路径标识；脚本检查每条标注是否在当前知识库中。开发集用于修正通用标题词的过度匹配，算法定稿后运行测试集，没有针对测试结果继续调参。

检索单位是食谱文档，不是文本块。相同来源去重后取前 5 个，重复命中同一菜谱不重复计分。两种检索使用同一 Neo4j 文档、Qdrant 集合和嵌入模型，不调用生成模型、会话解析或图路由。

- Recall@5 = 前 5 个文档中相关文档数 / 全部标注相关文档数。
- MRR@5 = 首个相关文档的排名倒数；前 5 个无相关文档时为 0。
- 最终指标对每条问题取平均。部分食谱有两份原文，例如陈皮排骨汤，两份均列为相关文档。

旧算法：按块计算向量与 BM25 的 RRF，再像旧应用一样取前 15 块、去重并保留前 5 份食谱。新算法：向量候选先按食谱去重，与完整食谱 BM25 进行 RRF；查询包含完整菜名时增加精确菜名排名。对番茄/西红柿、马铃薯/土豆、宫爆/宫保做固定归一化。多个候选块不再给同一道菜重复加分。

## 结果

| 范围 | Recall@5 baseline → recipe | MRR@5 baseline → recipe |
| --- | --- | --- |
| 全部 | 1.000000 → 1.000000 | 0.904167 → 0.958333 |
| 开发集 | 1.000000 → 1.000000 | 0.916667 → 1.000000 |
| 测试集 | 1.000000 → 1.000000 | 0.891667 → 0.916667 |

可检查的变化：番茄炒鸡蛋、宫爆鸡丁由第 2 名变为第 1 名；马铃薯炖排骨由第 5 名变为第 1 名。“吐司夹鸡蛋的早餐”由第 1 名退到第 2 名。糖醋里脊的目标仍排第 2 名。报告保留全部问题，不只展示提升案例。

数据量较小，包含菜名明确的问题，Recall 已饱和。只能说明本标注集上的排序改进，不能据此估计通用准确率。标签不保证穷尽全库每个可接受的相关菜谱；别名归一化也可能误改包含“番茄”的原菜名。延迟受缓存、机器和模型驻留影响，两种算法顺序固定，报告中的平均耗时不是严谨性能基准。

## 复现与回退

```powershell
.\.venv\Scripts\python.exe main.py --import-local --build-only
.\.venv\Scripts\python.exe evaluate.py --split dev --output evaluation/dev-results.json
.\.venv\Scripts\python.exe evaluate.py --split test --output evaluation/test-results.json
.\.venv\Scripts\python.exe evaluate.py --output evaluation/results.json
```

修改语料或标注后重新评估，报告会记录新的哈希。继续调参应使用开发集，再建立新的未参与调参的测试集。`settings.toml` 的 `retrieval_mode` 或环境变量 `RETRIEVAL_MODE` 可选 `recipe`（默认）或 `baseline`，切换不需要重建向量。

## Cross-Encoder 对照 · 2026-10-13

[原始三组结果](../evaluation/results-reranked.json) 在同一份 322 食谱 / 1761 块语料上运行，沿用 24 条标注。所用模型为 `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`，对 RRF 筛选后的候选执行真实 query / passage 推理。未生效的重排不能通过评估。

| 范围 | baseline Recall / MRR | recipe RRF Recall / MRR | Cross-Encoder Recall / MRR |
| --- | --- | --- | --- |
| 全部 24 条 | 1.000000 / 0.925000 | 1.000000 / 0.958333 | 0.979167 / 0.951389 |
| 开发 12 条 | 1.000000 / 0.958333 | 1.000000 / 1.000000 | 0.958333 / 1.000000 |
| 测试 12 条 | 1.000000 / 0.891667 | 1.000000 / 0.916667 | 1.000000 / 0.902778 |

整体没有提升。开发问题“剩米饭加鸡蛋炒一下”遗漏了一份相关食谱；测试集中有提升和退步。当前默认 `RERANK_ENABLED=false`，需要进一步评估模型或训练再决定是否启用。延迟含当时 localhost 的 IPv6 回退等待，之后已切换 IPv4；不要用该报告比较系统吞吐。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-rerank.txt
.\.venv\Scripts\python.exe evaluate.py --output evaluation/results-reranked.json
```

脚本会实际启用重排作对照，独立于网站默认关闭的设置。当前多了一份上传示例，重跑将生成新的语料指纹。

## Ragas 答案回放

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-eval.txt
# 首次为缺少答案或上下文的样本生成真实回答；已有完整样本保持冻结
.\.venv\Scripts\python.exe evaluate_answers.py --capture --limit 3
# 只评估已保存答案，不重新生成；同一样本与模型配置命中 SQL 分数缓存
.\.venv\Scripts\python.exe evaluate_answers.py --limit 3 --output evaluation/answer-replay-results.json
# 回放数据库中某段聊天的最后一条完整答案，必须提供人工参考答案
.\.venv\Scripts\python.exe evaluate_answers.py --chat-id <对话UUID> --reference '人工参考答案'
```

四项指标是 faithfulness、answer_relevancy、context_precision、context_recall。使用实际生成上下文，包含父文档回填和预算裁剪；参考答案需人工给定。更换问题、答案、上下文、参考答案或评估模型配置会产生新的缓存键。

本次 [样本](../evaluation/answer_samples.json) 为葱油拌面用量、蒜蓉空心菜食材、蒸花卷水量和时间。平均分依次为 1.000000、0.863335、约 1.000000、1.000000。[结果报告](../evaluation/answer-results.json) 保留逐条分数，[再次回放](../evaluation/answer-replay-results.json) 为 3/3 分数缓存命中，计分约 0.012 秒。这个时间不包含首次模型评估，不证明通用调用成本下降 30%。

Ragas 0.3.9 使用 LangChain wrapper 对接 Ollama；该版本已提示 wrapper 弃用，当前固定版本以确保复现，后续升级需重新验证。三条样本规模很小，自动 judge 的高分也不能替代人工核对。
