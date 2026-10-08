# AI News Agent

AI News Agent 抓取、去重、聚类并总结最新 AI 资讯，用本地部署的大语言模型（DeepSeek / Gemini / ChatGPT Web 代理）生成中文简报。

设计原则：Python 负责数据管道、规则、状态与存储；本地 LLM 负责理解、归纳、分类与有限推理。

文档

设计方案（ChatGPT / 本地 LLM 混合架构）

设计方案（DeepSeek 两阶段流水线）

本地 LLM 部署参考

数据源站点列表

任务拆解

架构
text
Copy
Download
doc/sites.md → Source Registry → Fetcher (RSS / HTML / API)
    → Normalizer → Dedup (SQLite)
    → LLM classify (DeepSeek) → Event cluster → Evidence / Claims
    → Ranking → SQLite
    → Query Agent (ChatGPT / fallback)
快速开始
bash
Copy
Download
# 1. 依赖
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

# 2. 配置（可选，默认值见 .env.example）
cp .env.example .env

# 3. 确保本地 LLM 代理已启动（见 doc/local_llm.md）
#    deepseek-web :8000 / gemini-web :8001 / chatgpt-web :8002
curl -s http://127.0.0.1:8000/v1/models

# 4. 抓取
.venv/bin/python -m newsagent fetch --limit 10

# 5. 处理（分类 → 聚类 → 排序），需要可用的本地 LLM
.venv/bin/python -m newsagent pipeline --limit 30 --hours 48

# 6. 查询
.venv/bin/python -m newsagent recent --hours 24
.venv/bin/python -m newsagent ask "过去24小时AI有什么重要新闻？"
CLI
命令	说明
news fetch [--source ID] [--limit N]	抓取来源写入 SQLite
news sources	列出来源与状态
news pipeline [--limit N] [--hours H]	分类 + 聚类 + 排序
news recent [--hours H]	最近事件
news search "query"	搜索事件
news event <id>	查看单个事件（含文章与 claims）
news ask "question"	Query Agent 问答（带来源）
news health	来源 + LLM Provider 健康状态

调度循环：python -m newsagent.cli.schedule

目录结构
text
Copy
Download
src/newsagent/
├── config.py            # 集中配置（env 覆盖）
├── logging_setup.py     # 日志 + 错误模型
├── sources/             # registry / models / scheduler
├── fetch/               # rss / html / jina 兜底
├── pipeline/            # normalize / dedup / classify / cluster / ranking
├── llm/                 # client / router / schemas / prompts / usage / circuit / health
├── storage/             # database / repository / search
├── agents/              # query agent
└── cli/                 # main / schedule
本地 LLM 约束

所有 Provider 走 OpenAI 兼容接口（api = openai-completions，apiKey = none，仅 text）：

Provider	Base URL	Model	职责
deepseek-web	http://127.0.0.1:8000/v1	deepseek-chat	分类、摘要
gemini-web	http://127.0.0.1:8001/v1	gemini-chat	事件分析、fallback
chatgpt-web	http://127.0.0.1:8002/v1	chatgpt-chat	复杂分析、用户查询

不要使用 developer 角色，不要发送 reasoning_effort——本地代理不支持。

测试
bash
Copy
Download
.venv/bin/python -m pytest -q
状态

已实现 P0–P6 核心链路：配置、日志、Source Registry、RSS/HTML 抓取、SQLite、标准化、去重、统一 LLM Client（retry / fallback / 并发 / 熔断）、分类、聚类、证据 / claims、排序、本地搜索、Query Agent、CLI、调度与退避、健康检查。

后置（未实现）：向量检索、主动研究 Agent、自动日报 / 周报、PostgreSQL/pgvector。

本地 LLM 代理需处于已登录状态；若上游 Web 端未登录，chat 调用会返回 502，Agent 会按配置顺序自动 fallback。