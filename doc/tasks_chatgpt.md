# AI 新闻 Agent 任务拆解

> 依据 `doc/design_chatgpt.md` 拆解。按依赖顺序实施，先完成单机 MVP，再扩展高级能力。

## 1. 阶段总览

| Phase | 范围 | 结果 |
| --- | --- | --- |
| P0 | 工程基础 | 项目骨架、配置、日志、测试 |
| P1 | 来源与采集 | Source Registry、RSS/API/HTML |
| P2 | 数据层 | SQLite、Article、Normalizer、Dedup |
| P3 | 本地 LLM | Client、Router、Retry/Fallback、Schema |
| P4 | 智能处理 | 分类、摘要、Event、Evidence |
| P5 | 查询与排序 | Ranking、Local Search、Query Agent |
| P6 | 可靠性 | Scheduler、Health、Usage |
| P7 | 高级能力 | 主动研究、日报、向量检索 |

推荐第一版先完成 P0–P5。

---

# P0：工程基础

## TASK-001 项目骨架

建立：

```text
src/
├── sources/
├── fetch/
├── pipeline/
├── llm/
├── storage/
├── agents/
└── cli/
data/
tests/
```

验收：Python package 可导入、测试框架可运行、不破坏 `doc/`。

依赖：无。

## TASK-002 配置系统

统一管理 DB 路径、fetch timeout、scheduler、Provider、concurrency、retry、logging。

验收：业务代码无散落硬编码；环境变量可覆盖关键配置。

依赖：001。

## TASK-003 日志与错误模型

统一记录 source/fetch/parse/LLM/storage/pipeline error。

验收：日志包含 run、source、task、provider 等上下文。

依赖：001。

---

# P1：Source Registry 与采集

## TASK-004 解析 `doc/sites.md`

把 Markdown 表格转换成 `Source`：

```text
id, name, url, category, language,
enabled, priority, crawl_interval, parser_type
```

验收：能读取现有 `doc/sites.md`；单个来源解析失败不影响其他来源；可按 id 查询。

依赖：001。

## TASK-005 Source 状态

保存：`last_success_at`、`last_error_at`、`last_seen_published_at`、`failure_count`、`etag`、`last_modified`。

验收：成功/失败后状态可更新。

依赖：004、008。

## TASK-006 RSS/Atom Fetcher

支持 HTTP、timeout、ETag、Last-Modified、XML 解析、发布时间。

验收：至少 2 个 feed 工作；HTTP 错误不会退出进程。

依赖：001、003。

## TASK-007 HTML Fetcher

支持 timeout、redirect、title、canonical URL、正文提取。

验收：至少 2 个 HTML 来源可采集；结构异常有明确错误。

依赖：001、003。

## TASK-008 统一 Fetch 接口

```python
fetch(source: Source) -> list[ArticleCandidate]
```

验收：RSS/HTML 都通过统一接口；新增 parser 不改主 Pipeline。

依赖：006、007。

---

# P2：SQLite、标准化、去重

## TASK-009 SQLite Schema

表：

```text
sources
articles
events
event_articles
claims
llm_runs
pipeline_runs
```

验收：可自动初始化、有索引、重复初始化安全。

依赖：001。

## TASK-010 Article Repository

提供：

```text
insert_article()
get_article()
find_article_by_url()
find_article_by_hash()
list_recent_articles()
```

验收：CRUD 与重复插入测试通过。

依赖：009。

## TASK-011 Normalizer

处理 URL canonicalization、tracking 参数、时间格式、HTML 清理、正文清理、language、content hash。

验收：同文不同 tracking URL 可归一；hash 稳定。

依赖：008、009。

## TASK-012 Deterministic Dedup

顺序：

```text
exact URL
→ canonical URL
→ content hash
→ 基础标题相似度
```

验收：重复文章不会重复进入业务流。

依赖：010、011。

## TASK-013 Pipeline Run

记录：

```text
run_id
started_at
finished_at
status
sources_total
sources_success
articles_seen
articles_new
errors
```

验收：可以查看历史采集运行记录。

依赖：009。

---

# P3：本地 LLM

## TASK-014 Provider 配置

严格按 `doc/local_llm.md`：

| Provider | Base URL | Model |
| --- | --- | --- |
| deepseek-web | `http://127.0.0.1:8000/v1` | `deepseek-chat` |
| gemini-web | `http://127.0.0.1:8001/v1` | `gemini-chat` |
| chatgpt-web | `http://127.0.0.1:8002/v1` | `chatgpt-chat` |

共同约束：`openai-completions`、API key=`none`、仅 text、不使用 `developer`、不发送 `reasoning_effort`。

验收：三个 Provider 都可由同一配置机制加载。

依赖：002。

## TASK-015 统一 LLM Client

接口：

```python
llm.generate(task, messages, schema=None, provider=None)
```

功能：OpenAI-compatible chat completion、timeout、retry、response parsing、provider metadata、usage metadata。

验收：上层不关心 base URL；至少一个本地 Provider 可完成 chat。

依赖：014。

## TASK-016 Provider Router

建议：

| Task | 首选 | Fallback |
| --- | --- | --- |
| classify_article | DeepSeek | Gemini → ChatGPT |
| summarize_article | DeepSeek | Gemini → ChatGPT |
| cluster_review | Gemini | ChatGPT |
| evidence_review | Gemini | ChatGPT |
| complex_analysis | ChatGPT | Gemini → DeepSeek |
| user_query | ChatGPT | Gemini → DeepSeek |

验收：业务层只传 task，Router 决定 Provider。

依赖：015。

## TASK-017 Retry + Fallback

失败类型：connection error、timeout、HTTP 5xx、空响应、schema 无效。

策略：同 Provider 重试一次，再 fallback。

验收：关闭某 Provider 时任务仍可完成；正常低置信度不触发无意义重复调用。

依赖：016。

## TASK-018 Provider Concurrency

为三个本地 Web Provider 分别设置并发上限。

验收：不会超过配置并发；一个 Provider 过载不拖死其他 Provider。

依赖：015。

## TASK-019 Circuit Breaker

```text
CLOSED → OPEN → HALF-OPEN → CLOSED
                         └→ OPEN
```

验收：连续失败后暂时停止请求；冷却后探测；恢复后重新启用。

依赖：017、018。

## TASK-020 JSON Schema

至少定义 ArticleAnalysis、EventAnalysis、Claim。

验收：合法结果通过；非法结果可 retry/fallback。

依赖：015、017。

## TASK-021 Prompt Registry

版本示例：

```text
article_classifier:v1
article_summarizer:v1
event_analyzer:v1
evidence_reviewer:v1
```

验收：每次调用都有 prompt_version。

依赖：015。

## TASK-022 LLM Usage Logging

记录：

```text
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

验收：每次调用有成功/失败记录。

依赖：015、021。

---

# P4：新闻智能处理

## TASK-023 AI Relevance Classifier

输入：title + 必要正文片段。

输出：

```json
{
  "is_ai_related": true,
  "topics": [],
  "entities": [],
  "confidence": 0.9
}
```

默认 DeepSeek。

验收：能过滤明显非 AI 新闻；JSON 通过 schema。

依赖：012、020、021、022。

## TASK-024 Article Summary

输出 summary、importance、confidence，并保留 source URL。默认 DeepSeek。

验收：摘要不能明显添加原文不存在的事实；失败不阻塞文章入库。

依赖：023。

## TASK-025 基础 Event Clustering

第一版只用时间窗口、entity overlap、标题 token overlap、source/category。

验收：同一新闻事件可以合并；无关内容不能轻易合并。

依赖：023、010。

## TASK-026 语义聚类

增加 embedding 或 LLM 辅助，解决规则无法判断的事件。状态：第二阶段，不阻塞 MVP。

依赖：025。

## TASK-027 Evidence Collector

为 Event 建立 primary source、independent report、discussion、research。

验收：重要事件有可追溯文章列表。

依赖：025。

## TASK-028 Claims

字段：`event_id`、`claim`、`evidence_type`、`source_urls`。

类型：`confirmed`、`reported`、`discussed`、`inferred`。

验收：关键结论可以反向找到 source URL。

依赖：027、020。

## TASK-029 Event Analysis

输出 summary、why_it_matters、importance、novelty、confidence、claims。

默认 Gemini；复杂事件升级 ChatGPT。

验收：多篇报道生成一个事件摘要；不会把 reported 自动写成 confirmed。

依赖：027、028。

## TASK-030 Source Conflict Review

检查时间、数字、功能描述以及官方与媒体的差异。默认 Gemini；复杂冲突可 ChatGPT。

验收：存在冲突时 Event 标记 conflict，而不是强行生成单一事实。

依赖：029。

---

# P5：排序、搜索、Query Agent

## TASK-031 Freshness Score

根据发布时间计算新鲜度。

验收：新事件分数更高，旧事件自然下降。

依赖：009。

## TASK-032 Novelty Score

依据 `first_seen_at`、`last_seen_at`、`source_count`。

验收：广泛重复报道的事件分数下降；新事件优先。

依赖：025。

## TASK-033 Source/Evidence Score

根据 source priority、source category、evidence type 计算可信度。

验收：官方 primary source 权重高于普通社区讨论；分数可解释。

依赖：028。

## TASK-034 Event Ranking

建议：

```text
base_score =
    freshness
  × source_priority
  × evidence_score
  × novelty_score
```

LLM 只补充 importance score。

验收：排序稳定、可解释、可测试。

依赖：029、031、032、033。

## TASK-035 Local Event Search

提供：

```text
get_recent_events(window)
get_event(event_id)
search_local_events(query)
```

支持时间、关键词、entity、topic。

验收：能查询最近新闻和指定事件。

依赖：034。

## TASK-036 Query Agent

流程：

```text
User Query
→ Query Parser
→ SQLite Search
→ Candidate Ranking
→ 必要时实时 Fetch/Verify
→ ChatGPT Web
→ Fallback
→ 带来源的答案
```

验收：默认先查本地数据库；只有证据不足或用户要求实时数据时才验证；最终答案包含 source URL；没有任意 Shell 权限。

依赖：015、017、028、034、035。

## TASK-037 CLI

建议：

```text
news fetch
news sources
news recent
news search "OpenAI"
news event <id>
news health
```

验收：无需 Web UI 即可验证 Pipeline。

依赖：035、036。

---

# P6：调度与可靠性

## TASK-038 Scheduler

按 source crawl interval 调度。

建议：官方/快讯 15–30 分钟；一般媒体 1–3 小时；社区 30–120 分钟；论文 1–6 小时；Newsletter 6–24 小时。

验收：不同 source 可独立运行；一个 source 失败不影响其他 source。

依赖：005、008、013。

## TASK-039 Fetch Retry/Backoff

支持 429、5xx、临时网络错误、最大 retry、exponential backoff。

验收：重试有限且有日志；长期失败进入 source error。

依赖：006、007。

## TASK-040 Source Health

状态：`healthy`、`warning`、`failed`、`paused`。

验收：CLI 可以看到 source 健康状况。

依赖：005、038。

## TASK-041 LLM Health

记录每 Provider 的 success rate、latency、failure count、circuit state。

验收：CLI 可查看三个 Provider 健康状态。

依赖：019、022。

---

# P7：后置能力

## TASK-042 主动研究 Agent

对高重要性 Event 自动提出研究问题，再抓取额外来源并生成 Research Report。

状态：后置。

依赖：036。

## TASK-043 自动日报/周报

内容：Top Events、Major Releases、Research Highlights、Trend Changes。

状态：第二阶段以后。

依赖：036。

## TASK-044 向量检索

为 article/event 增加 embedding，支持语义历史搜索。

状态：第三阶段以后，不阻塞 SQLite MVP。

依赖：035。

## TASK-045 PostgreSQL/pgvector

只有 SQLite 成为明确瓶颈才实施；业务层继续使用 Repository API。

依赖：044。

---

# 3. 最终执行顺序

```text
001 项目骨架
002 配置
003 日志
004 Source Registry
006 RSS Fetcher
007 HTML Fetcher
008 Fetch 接口
009 SQLite
010 Article Repository
011 Normalizer
012 Dedup
013 Pipeline Run
014 Provider 配置
015 LLM Client
016 Router
017 Retry/Fallback
018 Concurrency
020 JSON Schema
021 Prompt Registry
022 Usage Logging
023 AI Classifier
024 Article Summary
025 Event Clustering
027 Evidence
028 Claims
029 Event Analysis
031 Freshness
032 Novelty
033 Evidence Score
034 Ranking
035 Local Search
036 Query Agent
037 CLI
038 Scheduler
039 Backoff
040 Source Health
041 LLM Health
```

TASK-019 Circuit Breaker 可与 P6 一起完成，不应阻塞最小 MVP。

---

# 4. MVP 完成定义

- [ ] 可以读取 `doc/sites.md`
- [ ] 至少接入约 8–10 个核心来源
- [ ] RSS 与 HTML 都有可用实现
- [ ] Article 可以进入 SQLite
- [ ] URL/hash 去重可用
- [ ] DeepSeek 可以完成 AI 分类
- [ ] LLM Client 支持 retry/fallback
- [ ] Gemini/ChatGPT 可以处理重要事件
- [ ] Event 可以关联多个 Article
- [ ] Claim 有 source URL
- [ ] 可以查询最近 24 小时事件
- [ ] Query Agent 能生成带来源的中文摘要
- [ ] 单个网站失败不阻塞 Pipeline
- [ ] 单个 LLM Provider 失败不阻塞 Pipeline
- [ ] CLI 可以查看 sources/events/health

# 5. MVP 明确不做

以下后置：

- Multi-Agent Framework
- 全站 Playwright
- PostgreSQL
- Redis
- Kafka
- pgvector
- 复杂用户画像
- 自主研究 Loop
- 复杂 Web UI
- 一次性接入全部网站

第一阶段只需要把：

**采集 → 标准化 → 去重 → LLM 分类 → Event → Evidence → SQLite → Query**

跑通。
