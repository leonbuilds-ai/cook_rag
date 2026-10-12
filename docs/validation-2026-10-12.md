# 2026-10-12 验收记录

环境：Windows 虚拟环境、现有 Docker Neo4j 5 / Qdrant 1.14.1、Ollama。生成模型 qwen3.5:9b，嵌入模型 qwen3-embedding:8b。

- 目录从 `data/C8/cook` 迁移到 `data/cook`；322 份有效食谱，另有 1 份模板被跳过，图片随目录迁移。
- 图库同步来源路径，Qdrant 集合 `recipe_c9_8da7122318236cc4e170`，1761 个文本块。旧集合与其他应用的图数据保留。
- 65 项 unittest 回归通过，pip check 无依赖冲突，compileall 通过。
- 真实 `/health` 返回 200，neo4j / qdrant / embedding / llm 均为 ok。
- `/openapi.json` 返回 200，包含 `/ask` 和 `/health`。
- 问候返回 200，无食谱来源；早餐推荐返回 200，有结构化来源和原文链接。
- 同一 session_id 的“第一个怎么做”解析为“燕麦鸡蛋饼：第一个怎么做”，模型生成做法并引用 [S1]。
- 重启后清淡推荐返回西红柿豆腐汤羹、清蒸鲈鱼、玉米排骨汤；纯编号 `2` 解析为“清蒸鲈鱼怎么做？”，来源对应清蒸鲈鱼。
- 无效输入真实返回 422。离线测试覆盖 401、429、502、503、会话隔离、重置、TTL、容量与启动失败。
- `scripts/start-api.ps1` 和 `scripts/stop-api.ps1` 已完成一次实际后台停止和重新启动。
- 24 条评估：Recall@5 1.0000 → 1.0000，MRR@5 0.9042 → 0.9583；独立测试集 MRR@5 0.8917 → 0.9167。包含 1 条首位排序退步，保留逐条结果。

报告链接：[评估结果](../evaluation/results.json)、[评估方法](evaluation.md)、[API 说明](api.md)。历史验收见 [VALIDATION.md](../VALIDATION.md)。

本次没有构建 API Dockerfile，也没有在 Linux 上执行 CI，相关文件是部署和持续检查入口。没有进行负载测试、回答正确率评估或外网部署。
