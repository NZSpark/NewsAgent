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
news semantic-cluster --hours 48	LLM 合并同一事件的规则簇
news research [event_id] [--live]	主动研究（提问 → 报告）
news report --hours 24	生成日报/周报到 output/
news index --limit 500	建立事件向量索引
news semantic-search "query"	向量语义检索
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
├── pipeline/            # normalize / dedup / classify / cluster / semantic_cluster / ranking
├── llm/                 # client / router / schemas / prompts / usage / circuit / health
├── storage/             # database / repository / search / vector / backends
├── agents/              # query / research / report
└── cli/                 # main / schedule
本地 LLM 约束

所有 Provider 走 OpenAI 兼容接口（api = openai-completions，apiKey = none，仅 text）：

Provider	Base URL	Model	职责
deepseek-web	http://127.0.0.1:8000/v1	deepseek-chat	分类、摘要
gemini-web	http://127.0.0.1:8001/v1	gemini-chat	事件分析、fallback
chatgpt-web	http://127.0.0.1:8002/v1	chatgpt-chat	复杂分析、用户查询

不要使用 developer 角色，不要发送 reasoning_effort——本地代理不支持。

Provider 性能实测（真实联网测试）

所有 Provider 均为 Web 端驱动，单轮延迟远高于云端 API，实测数据如下：

Provider	单轮延迟	健康检查	能否独立完成任务
 deepseek-web	21–98s	✅ 21s	✅ 分类 10/10 成功（首选，fallback_from=None）
 gemini-web	24s	✅ 25s	✅ 分类 + 中文简报 + 问答 + 主动研究全部成功
 chatgpt-web	67–120s+	✅ 68s	⚠️ 短任务（分类 3/3）成功；长上下文 query 超 120s 超时

关键结论：

- 三个 Provider 的 Web 会话均已登录，`news health` 全部返回 healthy（曾因健康检查 10s 超时过短而误报 deepseek 为 down，已修复为 `max(llm_timeout, 60s)`）。
- 延迟差异大：deepseek/gemini 约 20–25s，chatgpt-web 约 67s，长上下文可超 120s。
- chatgpt-web 对长上下文（如携带多个事件的 query）会超时；已支持**按 Provider 配置超时**（`CHATGPT_WEB_TIMEOUT=300`），并裁剪 query 上下文至 10 个事件以降低延迟。
- 故障转移链实测有效：deepseek 502 → gemini 接管；连续失败 3 次触发熔断 `circuit -> OPEN`。

各任务实测耗时：

任务	Provider	耗时
 分类单次	deepseek-web	21–98s
 分类单次	gemini-web	~5s（首次）
 健康检查	deepseek-web	21.3s
 健康检查	gemini-web	24.5s
 健康检查	chatgpt-web	67.6s
 query（24 事件）	chatgpt-web	超时（>120s）
 主动研究
gemini-web	数十秒，含 4 问 + 报告 + 发现

测试
bash
Copy
Download
.venv/bin/python -m pytest -q
状态

已实现 P0–P7 全部任务：配置、日志、Source Registry、RSS/HTML 抓取、SQLite、标准化、去重、统一 LLM Client（retry / fallback / 并发 / 熔断）、分类、规则 + 语义聚类、证据 / claims、排序、本地搜索、Query Agent、CLI、调度与退避、健康检查、主动研究、日报 / 周报、向量检索。

存储后端：SQLite（默认）。PostgreSQL/pgvector 提供后端抽象（storage/backends.py），按设计仅在 SQLite 成为瓶颈时实现。

测试：57 passed。

本地 LLM 代理需处于已登录状态；若上游 Web 端未登录，chat 调用会返回 502，Agent 会按配置顺序自动 fallback。