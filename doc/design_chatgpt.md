# AI 新闻采集 Agent 设计方案

## 1. 目标

建立一个适合本地单机环境运行的 AI News Agent，而不是让一个 LLM 自己浏览所有网站。

系统负责：

1. 从 `doc/sites.md` 获取新闻来源。
2. 定期抓取新文章、官方公告、论文和社区内容。
3. 做标准化、去重和事件聚类。
4. 对重要事件建立来源与证据链。
5. 使用本地 LLM 生成摘要、分类、重要性和影响分析。
6. 将结果保存到 SQLite，支持历史查询。
7. 用户查询时优先检索已有新闻库，必要时才实时验证。

核心原则：

> **Python 负责数据管道、规则、状态和存储；本地 LLM 负责理解、归纳、分类和有限推理。**

不要为了“Agent”而把所有步骤都交给 LLM，也不要第一版就使用复杂的 Multi-Agent Framework。

## 2. 本地 LLM 约束

`doc/local_llm.md` 定义了三个 OpenAI-compatible 本地 Provider：

| Provider | Base URL | Model ID | 建议职责 |
| --- | --- | --- | --- |
| `deepseek-web` | `http://127.0.0.1:8000/v1` | `deepseek-chat` | 批量分类、摘要、常规分析 |
| `gemini-web` | `http://127.0.0.1:8001/v1` | `gemini-chat` | 第二分析路径、交叉检查、fallback |
| `chatgpt-web` | `http://127.0.0.1:8002/v1` | `chatgpt-chat` | 复杂分析、重要事件总结、用户查询 |

共同限制：

- API 类型：`openai-completions`
- API Key：`none`
- 输入仅支持 `text`
- 不支持 `developer` role：`supportsDeveloperRole=false`
- 不支持 `reasoning_effort`：`supportsReasoningEffort=false`
- 配置声明上下文窗口为 1,000,000、最大输出为 65,535；实际能力以对应 Web 代理为准

代码必须使用 `system` / `user` / `assistant` 消息，不要使用 `developer` role，也不要发送 `reasoning_effort`。

## 3. 总体架构

```text
                  doc/sites.md
                       |
                       v
                  Source Registry
                       |
                       v
                    Scheduler
                       |
          +------------+------------+
          |            |            |
        RSS/API      HTML       Search/Feed
          |            |            |
          +------------+------------+
                       |
                       v
                  Normalizer
                       |
                       v
                 Deterministic
                     Dedup
                       |
                       v
                 Event Cluster
                       |
                       v
                Evidence Layer
                       |
                       v
                Local LLM Client
          +------------+------------+
          |            |            |
       DeepSeek     Gemini       ChatGPT
          |            |            |
          +------------+------------+
                       |
                       v
                 Ranking + SQLite
                       |
            +----------+----------+
            |                     |
      Background Pipeline     Query Agent
                                  |
                                  v
                             User Query
```

后台持续采集和前台问答必须分开。

## 4. Source Registry

当前 `doc/sites.md` 主要用于人工维护，不必立即改成 YAML。程序可以解析 Markdown 表格，在代码层转换成统一的 `Source` 对象。

建议字段：

```text
id
name
url
category
language
enabled
priority
crawl_interval
parser_type
last_success_at
last_error_at
failure_count
etag
last_modified
```

来源分类：

- `media`
- `official`
- `research`
- `community`
- `newsletter`

后续可以增加独立 YAML/JSON 配置，但不应破坏当前 `doc/sites.md`。

## 5. 采集层

优先级：

```text
RSS / Atom
    -> 官方 API
    -> 站点专用 parser
    -> 普通 HTML parser
    -> 浏览器渲染
```

第一版不要给所有站点使用 Playwright。

建议：

| 来源 | 首选 |
| --- | --- |
| 新闻媒体 | RSS / feed / HTML |
| 官方博客 | RSS / sitemap / HTML |
| arXiv | API / RSS |
| Hacker News | API |
| Reddit | API / RSS |
| GitHub Trending | HTML |
| Newsletter | RSS / 页面 |

采集失败必须局部隔离，不能因为一个站点失败而阻塞整个 Pipeline。

## 6. Article 标准模型

抓取后的内容统一为：

```json
{
  "source_id": "techcrunch_ai",
  "url": "https://example.com/article",
  "canonical_url": "https://example.com/article",
  "title": "...",
  "published_at": "2026-10-09T03:10:00Z",
  "author": "...",
  "content": "...",
  "language": "en",
  "content_hash": "...",
  "retrieved_at": "2026-10-09T03:15:00Z"
}
```

Normalizer 全部由 Python 完成：

- URL canonicalization
- 删除 tracking 参数
- 统一时间
- HTML 清理
- 正文提取
- 删除导航/广告文本
- 语言判断
- content hash

这些步骤不调用 LLM。

## 7. 去重与事件聚类

必须区分 `Article` 和 `Event`。

### 第一层：确定性去重

按以下顺序：

1. URL 完全相同
2. canonical URL 相同
3. content hash 相同

### 第二层：标题/文本相似

使用普通字符串相似度、token overlap 等低成本方法。

### 第三层：语义聚类

只有前两层无法处理时才使用 embedding 或 LLM 辅助。

最终形成：

```text
Article A ---+
Article B ---+--> Event #123
Article C ---+
```

例如一家公司发布模型后，会同时出现官方公告、Reuters、TechCrunch、Hacker News、Reddit、论文等内容，用户最终应看到一个事件，而不是多条重复新闻。

## 8. Event 模型

建议保存：

```text
event_id
title
first_seen_at
last_seen_at
published_at
importance_score
novelty_score
confidence_score
category
entities
summary
why_it_matters
status
```

用 `event_articles` 建立关联：

```text
event_id
article_id
relation_type
```

`relation_type`：

- `primary`
- `independent_report`
- `discussion`
- `research`

## 9. 证据与事实追踪

新闻 Agent 的关键安全机制是让每一个重要结论可追溯。

证据类型：

- `confirmed`：第一方明确确认
- `reported`：媒体报道
- `discussed`：社区讨论
- `inferred`：Agent 推断

例如：

```json
{
  "claim": "新模型于 10 月 9 日发布",
  "evidence_type": "confirmed",
  "source_urls": ["https://official.example.com/news"]
}
```

不要保存没有来源的关键事实。

Evidence Pipeline：

```text
Event
  -> 找到 primary source
  -> 找 independent reports
  -> 检查冲突
  -> 保存 claims
```

程序负责收集和保存证据，LLM 负责比较和解释证据。

## 10. 本地 LLM Client

所有业务模块都通过统一 Client 调用本地 LLM，不允许业务代码直接创建 OpenAI Client。

建议目录：

```text
src/llm/client.py
src/llm/providers.py
src/llm/router.py
src/llm/schemas.py
src/llm/prompts.py
src/llm/usage.py
```

统一接口：

```python
llm.generate(
    task=task_name,
    messages=messages,
    schema=schema,
    provider=optional_provider,
)
```

Provider 配置：

```python
PROVIDERS = {
    "deepseek-web": {
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "deepseek-chat",
    },
    "gemini-web": {
        "base_url": "http://127.0.0.1:8001/v1",
        "model": "gemini-chat",
    },
    "chatgpt-web": {
        "base_url": "http://127.0.0.1:8002/v1",
        "model": "chatgpt-chat",
    },
}
```

所有 Provider 使用：

```text
api = openai-completions
apiKey = none
```

消息格式只使用：

```python
[
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_prompt},
]
```

不要使用 `developer` role，不要发送 `reasoning_effort`。

## 11. Provider 路由

建议任务级路由：

| Task | 首选 | Fallback |
| --- | --- | --- |
| `classify_article` | DeepSeek | Gemini -> ChatGPT |
| `summarize_article` | DeepSeek | Gemini -> ChatGPT |
| `cluster_review` | Gemini | ChatGPT |
| `evidence_review` | Gemini | ChatGPT |
| `complex_analysis` | ChatGPT | Gemini -> DeepSeek |
| `user_query` | ChatGPT | Gemini -> DeepSeek |

这只是程序路由策略，不代表模型能力的绝对排名。

Fallback 只在以下情况触发：

- connection error
- timeout
- HTTP 5xx
- provider unavailable
- 重试后仍为空或无效

正常的低置信度结果不要自动触发重复调用。

## 12. Timeout、Retry、Concurrency、Circuit Breaker

### Timeout

每次调用必须有明确 timeout，禁止无限等待。

### Retry

```text
第一次失败
   -> 短暂延迟
   -> 同 Provider 重试
   -> 仍失败
   -> Fallback Provider
```

### Concurrency

三个本地 Web 代理应分别限制并发数，使用小型 worker pool，防止大量同时请求压垮本地代理。

### Circuit Breaker

```text
正常 -> 连续失败 -> OPEN -> 冷却 -> HALF-OPEN
                                  |
                           +------+------+
                           |             |
                         成功           失败
                           |             |
                         正常          OPEN
```

这样单个 Provider 故障不会阻塞整个新闻 Pipeline。

## 13. LLM 使用边界

### 不使用 LLM

- RSS/API 解析
- HTML 清理
- URL canonicalization
- hash 去重
- 时间过滤
- Source 状态
- SQLite
- 基础关键词过滤
- 简单文本相似度
- 基础排序

### 普通 LLM 调用

- AI 相关性
- 主题分类
- entity 提取
- 简短摘要
- 初步重要性

### 高质量 LLM 调用

- 多来源事件综合
- 来源冲突解释
- 复杂影响分析
- 用户主动研究

原则：**不要对每篇文章都调用高能力模型。**

## 14. 结构化输出

LLM 不直接输出最终 Markdown，而是输出 JSON。

例如：

```json
{
  "is_ai_related": true,
  "topics": ["LLM", "model-release"],
  "entities": ["OpenAI"],
  "summary": "...",
  "importance": 0.82,
  "confidence": 0.91
}
```

事件级输出：

```json
{
  "summary": "...",
  "why_it_matters": "...",
  "importance": 0.92,
  "novelty": 0.76,
  "confidence": 0.89,
  "claims": [
    {
      "text": "...",
      "evidence_type": "confirmed",
      "source_urls": ["..."]
    }
  ]
}
```

程序侧必须做 schema validation。

若 JSON 无效：

1. 同 Provider 重试一次
2. 仍失败则 fallback
3. 最终失败则保留原始文章，不阻塞整个 Pipeline

## 15. Prompt Version 与 LLM 使用统计

Prompt 必须带版本号，例如：

```text
article_classifier:v1
article_summarizer:v1
event_analyzer:v1
evidence_reviewer:v1
```

`llm_runs` 建议保存：

```text
run_id
task_type
provider
model
prompt_version
started_at
finished_at
input_tokens
output_tokens
latency_ms
success
error_type
fallback_from
```

即使当前代理不一定始终提供完整 token 统计，也应预留字段。

## 16. 排序

基础分数由程序计算：

```text
base_score =
    freshness
  * source_priority
  * evidence_score
  * novelty_score
```

LLM 只为少量候选事件补充 `importance_score`。

新颖性可保存：

```text
first_seen_at
last_seen_at
source_count
novelty_score
```

报道很多但已经传播广泛的事件，novelty 应下降；刚出现且来源较少的事件应优先验证。

## 17. SQLite 数据库

第一版只使用 SQLite。

建议表：

### `sources`

来源配置与抓取状态。

### `articles`

URL、正文、hash、发布时间等。

### `events`

聚合后的新闻事件。

### `event_articles`

事件与文章关联。

### `claims`

关键事实及 source URL。

### `llm_runs`

LLM 调用记录。

### `pipeline_runs`

采集任务状态、耗时、错误。

第一版不要同时引入 PostgreSQL、Redis、Kafka 和向量数据库。

## 18. 后台 Pipeline

```text
Scheduler
  -> Load Sources
  -> Fetch
  -> Normalize
  -> Deterministic Dedup
  -> AI relevance filter
  -> Article Analysis
  -> Event Clustering
  -> Evidence Collection
  -> Event Analysis
  -> Ranking
  -> SQLite
```

关键点：文章先被收集和过滤，只有值得分析的内容才进入更高成本的 LLM 阶段；事件级摘要应尽量在聚类之后执行，避免对同一事件重复摘要。

## 19. 前台 Query Agent

前台查询优先读取数据库。

例如：

> 过去 24 小时 AI 有什么重要新闻？

流程：

```text
User Question
  -> Query Parser
  -> SQLite recent events
  -> candidate ranking
  -> 必要时实时 fetch/verify
  -> ChatGPT Web / fallback
  -> answer
```

推荐工具：

```text
get_recent_events(window)
get_event(event_id)
search_local_events(query)
get_source_status(source_id)
fetch_url(url)
verify_event(event_id)
llm_analyze(task)
```

Query Agent 不应拥有任意 Shell 执行权限。

## 20. 调度

| 来源类型 | 示例 | 建议间隔 |
| --- | --- | --- |
| 重大官方来源 | OpenAI / Anthropic / DeepMind | 15–30 分钟 |
| 快速新闻 | Reuters / TechCrunch | 15–30 分钟 |
| 一般媒体 | WIRED / Ars / MIT TR | 1–3 小时 |
| 社区 | Hacker News / Reddit | 30–120 分钟 |
| 论文 | arXiv | 1–6 小时 |
| Newsletter | The Batch | 6–24 小时 |

需要：

- exponential backoff
- retry limit
- source rate limit
- provider concurrency limit
- 单来源连续失败暂停

## 21. 错误隔离

```text
单篇文章失败
    -> 跳过文章

单个站点失败
    -> 标记 source error

一个 LLM Provider 失败
    -> retry + fallback

SQLite 写入失败
    -> 当前 pipeline run 失败并记录

Scheduler 失败
    -> 下一周期继续
```

## 22. 推荐项目目录

```text
src/
├── sources/
│   ├── registry.py
│   ├── parsers/
│   └── scheduler.py
├── fetch/
│   ├── rss.py
│   ├── html.py
│   └── browser.py
├── pipeline/
│   ├── normalize.py
│   ├── dedup.py
│   ├── cluster.py
│   ├── evidence.py
│   └── ranking.py
├── llm/
│   ├── client.py
│   ├── providers.py
│   ├── router.py
│   ├── schemas.py
│   ├── prompts.py
│   └── usage.py
├── storage/
│   ├── database.py
│   ├── models.py
│   └── repository.py
├── agents/
│   └── query.py
└── cli/
    └── news.py

doc/
├── sites.md
├── local_llm.md
└── design_chatgpt.md

data/
└── news.db
```

## 23. MVP

第一版只选择约 8–10 个来源，不要一次覆盖全部网站：

```text
Reuters
TechCrunch
The Verge
OpenAI
Anthropic
Google DeepMind
Hacker News
arXiv cs.CL
机器之心
量子位
```

建议 MVP：

```text
每 30 分钟
  -> Fetch
  -> Normalize
  -> URL/hash dedup
  -> DeepSeek 分类
  -> SQLite
  -> 简单事件聚类
  -> 对重要事件使用 Gemini 或 ChatGPT 摘要
```

MVP 不需要：

- Multi-agent framework
- PostgreSQL
- pgvector
- Redis
- Kafka
- 全站 Playwright
- 自主研究 Loop
- 复杂用户画像

## 24. 第二阶段

在 MVP 稳定后增加：

1. 内容语义去重
2. 多来源 Event Clustering
3. Claims / Evidence
4. Gemini 交叉审核
5. ChatGPT 复杂事件分析
6. importance / novelty ranking
7. Query Agent
8. Provider health monitoring
9. LLM usage dashboard

## 25. 第三阶段

最后再考虑：

- 主动研究
- 事件时间线
- 实体关系图
- 用户兴趣模型
- 自动日报/周报
- 邮件或 IM 推送
- 向量检索
- PostgreSQL / pgvector

## 26. 最终推荐

本项目最适合的结构是：

```text
确定性新闻数据管道
          +
统一本地 LLM Client
          +
SQLite 新闻记忆
          +
一个简单 Query Agent
```

三个本地 LLM Provider 只是智能分析层：

```text
DeepSeek Web
   -> 批量、便宜、常规分析

Gemini Web
   -> 第二路径、交叉检查、fallback

ChatGPT Web
   -> 复杂分析和用户查询
```

真正稳定的系统边界应该是：

```text
网页采集、去重、状态、存储、调度、失败恢复
                    |
                    v
              Local LLM Client
                    |
                    v
         分类 / 摘要 / 事件分析
                    |
                    v
                 SQLite
                    |
                    v
               Query Agent
```

### 实现优先级

1. Source Registry
2. Fetchers
3. Normalizer
4. SQLite
5. URL/hash Dedup
6. LLM Client
7. Provider Router + Fallback
8. Article Classification
9. Event Clustering
10. Event Summary
11. Evidence / Claims
12. Ranking
13. Query Agent
14. 主动研究

第一版先做到 1–10，就已经可以形成一个实用的 AI 新闻 Agent。