# AI News Agent

一个面向 AI 行业资讯的本地新闻采集、分析与检索工具。它从配置的新闻来源收集文章，进行标准化、去重、LLM 分类、事件聚类与重要性排序，并将结果保存在本地 SQLite 数据库中。用户可以通过命令行查询新闻、追问事件、执行主动研究，以及生成 Markdown、HTML 和 PDF 格式的日报与周报。

项目采用 **Python 负责数据管道、规则、状态与存储，LLM 负责语义理解、分类、归纳与分析** 的设计。LLM 通过本地运行的 OpenAI 兼容代理接入 DeepSeek、Gemini 和 ChatGPT Web 等服务，不要求本项目直接集成各服务的专有 API。

> **网络说明：** 本项目不是完全离线的新闻采集器。抓取新闻、使用 Web 驱动的 LLM 代理，以及主动研究中的实时抓取，都可能产生网络请求。本文档重写仅依据仓库代码与现有文档，没有执行联网测试或实时服务验证。

## 目录

- [功能概览](#功能概览)
- [工作流程与架构](#工作流程与架构)
- [环境要求](#环境要求)
- [安装与配置](#安装与配置)
- [快速开始](#快速开始)
- [命令行参考](#命令行参考)
- [LLM Provider 配置](#llm-provider-配置)
- [定时采集](#定时采集)
- [数据与输出](#数据与输出)
- [项目结构](#项目结构)
- [测试与排错](#测试与排错)
- [设计文档](#设计文档)

## 功能概览

| 模块 | 功能 |
| --- | --- |
| 新闻来源 | 通过 `doc/sites.md` 管理来源，区分来源类别和启用状态 |
| 数据采集 | RSS 与 HTML 抓取；对来源级错误进行隔离，避免单个来源失败中断整轮采集 |
| 文章处理 | 标准化文章字段，识别重复内容，记录到 SQLite |
| LLM 分类 | 对文章进行语义分类和摘要生成 |
| 事件聚类 | 将相关报道归并为事件；另外提供 LLM 语义聚类命令 |
| 重要性排序 | 对事件进行排序，供后续查询与简报使用 |
| 本地查询 | 按时间查看事件、关键词搜索、查看事件及关联文章 |
| Query Agent | 根据本地新闻库回答问题，并返回相关来源链接 |
| 主动研究 | 针对指定事件或重要事件开展研究；可选择启用实时抓取 fallback |
| 报告生成 | 生成 Markdown、HTML 或 PDF 格式的日报与周报，支持一次生成多种格式，保存到 `output/` |
| 语义检索 | 为文章和事件建立向量索引，并执行语义搜索 |
| 运行维护 | 日志、来源健康状态、LLM 健康检查、重试、fallback、并发限制和熔断机制 |

这些功能是否能完整运行，取决于本地数据库中的数据、来源可访问性，以及所需 LLM 代理是否已正确配置并可用。

## 工作流程与架构

```text
doc/sites.md
    │
    ▼
Source Registry / Scheduler
    │
    ▼
RSS / HTML Fetcher
    │
    ▼
Normalize ── Deduplicate
    │
    ▼
SQLite：文章、来源、处理状态
    │
    ▼
LLM Classification / Summarization
    │
    ▼
Rule-based Event Clustering
    │
    ├── Semantic Clustering（独立命令）
    │
    ▼
Event Ranking
    │
    ├── Local Search / Query Agent
    ├── Active Research
    ├── Markdown / HTML / PDF Briefing
    └── Vector Index / Semantic Search
```

### 设计要点

- **采集与分析分开：** `news fetch` 负责抓取、标准化、去重和入库；`news pipeline` 负责分类、规则聚类和排序。
- **本地持久化：** 默认使用 SQLite，数据存放在 `data/newsagent.db`。不同命令可以基于已积累的数据逐步处理。
- **Provider 路由：** 根据任务选择优先 Provider 和备用 Provider；失败时由 LLM 层按配置处理重试、fallback 与熔断。
- **可追溯查询：** Query Agent 使用本地事件数据构造上下文，并收集关联文章 URL，以便回答关联来源。
- **确定性报告渲染：** 报告由已存储的事件及摘要生成 Markdown，不需要在生成报告的每个步骤重新调用 LLM。

## 环境要求

- Python 3.10 或更高版本。
- pip 和虚拟环境支持。
- 可访问的新闻来源；运行采集时需要网络连接。
- 如果要执行 LLM 分类、语义分析、问答或主动研究，需要启动并配置相应的本地 LLM 兼容代理。

主要运行依赖由 `pyproject.toml` 声明，包括 `httpx`、`feedparser`、`selectolax`、`beautifulsoup4`、`trafilatura`、`openai`、`pyyaml` 和 `tenacity`。开发依赖包括 `pytest` 与 `pytest-asyncio`。

## 安装与配置

在项目根目录执行：

```bash
# 创建虚拟环境
python3 -m venv .venv

# 安装项目及开发依赖
.venv/bin/python -m pip install -e ".[dev]"

# 创建本地配置文件
cp .env.example .env
```

配置通过环境变量读取，默认值集中定义在 `src/newsagent/config.py`。请根据实际运行环境修改 `.env` 或设置相应环境变量。`.env.example` 是配置参考；使用前请确认当前运行方式确实会加载你所使用的环境变量文件。

### 关键配置项

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `NEWSAGENT_DB_PATH` | `data/newsagent.db` | SQLite 数据库路径 |
| `NEWSAGENT_SITES_MD` | `doc/sites.md` | 新闻来源清单路径 |
| `NEWSAGENT_FETCH_TIMEOUT` | `15` | 单次抓取超时配置 |
| `NEWSAGENT_FETCH_CONCURRENCY` | `6` | 抓取并发配置 |
| `NEWSAGENT_FETCH_RETRIES` | `2` | 抓取重试配置 |
| `NEWSAGENT_USER_AGENT` | `NewsAgent/0.1` | 抓取请求使用的 User-Agent |
| `JINA_API_KEY` | 空 | 如需使用相应的 Jina 回退服务，可配置 API Key |
| `LLM_PROVIDERS` | `deepseek-web,gemini-web,chatgpt-web` | 全局 Provider 顺序配置 |
| `LLM_TIMEOUT` | `120` | LLM 默认超时配置，单位为秒 |
| `LLM_MAX_RETRIES` | `1` | LLM 重试配置 |
| `LLM_CONCURRENCY` | `3` | LLM 并发配置 |
| `NEWSAGENT_INTERVAL_OFFICIAL` | `30` | 官方来源调度间隔，单位为分钟 |
| `NEWSAGENT_INTERVAL_MEDIA` | `60` | 媒体来源调度间隔，单位为分钟 |
| `NEWSAGENT_INTERVAL_COMMUNITY` | `60` | 社区来源调度间隔，单位为分钟 |
| `NEWSAGENT_INTERVAL_RESEARCH` | `180` | 研究类来源调度间隔，单位为分钟 |
| `NEWSAGENT_INTERVAL_NEWSLETTER` | `720` | Newsletter 来源调度间隔，单位为分钟 |
| `NEWSAGENT_LOG_LEVEL` | `INFO` | 日志级别 |

Provider 还支持各自的 `BASE_URL`、`MODEL` 和 `TIMEOUT` 覆盖项。完整配置示例见 `.env.example`。路径使用相对路径时，以项目根目录作为解析基准。

## 快速开始

以下是典型的初次运行流程。需要访问网络或 LLM 代理的操作请在自己的运行环境中执行；本次文档重写没有执行这些命令。

### 1. 查看来源清单

检查 `doc/sites.md`，确认来源条目、类别和启用设置符合预期。来源配置和维护说明见 [doc/sites.md](doc/sites.md)。

### 2. 采集文章

```bash
.venv/bin/news fetch --limit 10
```

该命令最多选择 10 个启用的来源进行抓取。省略 `--limit` 则不限制来源数量；可以通过 `--source` 限定来源 ID。抓取结果经过标准化和去重后写入数据库，个别来源失败会记录错误并继续处理其他来源。

### 3. 运行处理流水线

```bash
.venv/bin/news pipeline --limit 30 --hours 48
```

该命令依次执行文章分类、规则事件聚类和重要性排序。分类默认处理上限为 30；聚类使用最近 48 小时的范围。首次运行需要有待处理文章，并需要可用的 LLM Provider 来完成分类。

### 4. 查看最近事件

```bash
.venv/bin/news recent --hours 24
```

### 5. 对本地新闻提问

```bash
.venv/bin/news ask "过去24小时 AI 有哪些重要新闻？"
```

Query Agent 会优先使用本地数据库中的事件及关联文章来源，不是通用的联网搜索引擎。如果本地没有相关数据，先执行采集和处理步骤。该命令需要 LLM Provider 可用。

### 6. 生成日报

```bash
.venv/bin/news report --hours 24
```

报告会写入 `output/`。使用大于 24 的小时数可以覆盖更长的事件时间范围，例如：

```bash
.venv/bin/news report --hours 168
```

默认只生成 Markdown，以保持原有行为。也可以选择输出格式：

```bash
# 只生成 HTML
.venv/bin/news report --hours 24 --formats html

# 生成 Markdown、HTML 和 PDF
.venv/bin/news report --hours 24 --formats md,html,pdf
```

HTML 使用内置模板和 CSS，可离线打开。PDF 使用可选的 WeasyPrint 依赖；首次使用前可尝试安装：

```bash
.venv/bin/python -m pip install -e ".[pdf]"
```

WeasyPrint 在部分操作系统上还需要额外的系统库和可用字体，请根据目标平台的安装说明配置。自动化测试已验证 PDF 输出编排，但真实 PDF 的中文字体和分页效果仍需在目标环境中检查。

### 使用 `python -m` 的方式

如果虚拟环境中的可执行入口不可用，也可以使用模块方式调用 CLI：

```bash
.venv/bin/python -m newsagent fetch --limit 10
.venv/bin/python -m newsagent pipeline --limit 30 --hours 48
```

## 命令行参考

安装后，CLI 入口为 `news`。命令通常输出 JSON 格式结果；报告生成命令会输出生成文件路径。

| 命令 | 用途 | 关键参数 |
| --- | --- | --- |
| `news fetch` | 抓取、标准化、去重并保存文章 | `--source ID`（可重复）、`--limit N` |
| `news sources` | 列出已记录的来源与状态 | 无 |
| `news recent` | 查看最近发生或首次发现的事件 | `--hours H`，默认 24；`--limit N`，默认 50 |
| `news search "关键词"` | 在本地事件库中进行文本搜索 | `--limit N`，默认 50 |
| `news event EVENT_ID` | 查看指定事件及其关联信息 | 事件 ID |
| `news health` | 检查来源和 LLM Provider 健康状态 | 无 |
| `news pipeline` | 分类、规则聚类并排序 | `--limit N`，默认 30；`--hours H`，默认 48 |
| `news ask "问题"` | 基于本地事件回答问题 | 问题文本 |
| `news semantic-cluster` | 使用 LLM 对规则聚类结果进行语义合并 | `--hours H`，默认 48 |
| `news research` | 研究指定事件或筛选出的重要事件 | 可选 `EVENT_ID`；`--limit N`，默认 3；`--min-importance X`，默认 0.6；`--live` 启用实时抓取 fallback |
| `news report` | 生成 Markdown、HTML 或 PDF 日报/周报 | `--hours H`，默认 24；`--formats md,html,pdf`，默认 `md` |
| `news index` | 建立文章和事件的向量索引 | `--limit N`，默认 500 |
| `news semantic-search "查询"` | 对事件执行语义搜索 | `--limit N`，默认 20；`--articles` 改为搜索文章 |

### 常用示例

```bash
# 只采集指定来源
news fetch --source openai --source arxiv --limit 2

# 查看一周内的事件
news recent --hours 168 --limit 100

# 按关键词搜索本地事件
news search "多模态模型" --limit 20

# 查看指定事件
news event EVENT_ID

# 对最近事件执行语义聚类
news semantic-cluster --hours 48

# 研究某个事件，不显式开启 live fallback
news research EVENT_ID

# 研究重要事件，并允许实时抓取 fallback
news research --limit 5 --min-importance 0.6 --live

# 建立向量索引并检索事件
news index --limit 500
news semantic-search "开源推理模型" --limit 10

# 检索文章而不是事件
news semantic-search "模型训练数据" --articles --limit 10

# 检查运行状态
news health
```

上例中的 `openai`、`arxiv` 和 `EVENT_ID` 是示例参数；请替换成实际配置的来源 ID 或事件 ID。`news health` 会调用健康检查逻辑，检查配置的来源与 Provider；不要把它当作纯离线命令。

## LLM Provider 配置

默认 Provider 地址与模型如下。这些地址是运行在本机的兼容代理端点，不是托管 API 的公网地址。

| Provider | 默认 Base URL | 默认模型 | 主要任务倾向 |
| --- | --- | --- | --- |
| `deepseek-web` | `http://127.0.0.1:8000/v1` | `deepseek-chat` | 文章分类与摘要 |
| `gemini-web` | `http://127.0.0.1:8001/v1` | `gemini-chat` | 事件审查与备用处理 |
| `chatgpt-web` | `http://127.0.0.1:8002/v1` | `chatgpt-chat` | 复杂分析与用户问答 |

对应配置项为 `DEEPSEEK_WEB_*`、`GEMINI_WEB_*` 和 `CHATGPT_WEB_*`，支持分别设置 `BASE_URL`、`MODEL` 与 `TIMEOUT`。全局 `LLM_TIMEOUT` 为默认超时；`.env.example` 对 `CHATGPT_WEB_TIMEOUT` 设置了更长的示例值，以适应较慢的 Web 驱动会话。

任务路由与全局 Provider 顺序共同决定调用策略。代码中的任务路由默认优先使用 DeepSeek 处理文章分类和摘要；聚类审查、证据审查优先使用 Gemini；复杂分析和用户问答优先使用 ChatGPT。可用性不足时，具体 fallback 行为由 LLM 路由、重试和熔断逻辑处理。

使用前应确认：

1. 对应代理已在本机启动，地址和模型名称与实际服务配置一致。
2. 代理所驱动的 Web 服务已登录且可以正常工作。
3. 超时与并发配置适合当前任务和代理性能。

这些代理使用 OpenAI 兼容的文本对话接口。请遵循 [doc/local_llm.md](doc/local_llm.md) 中的代理约束；不要假设代理支持所有原生 OpenAI 参数。项目设计说明指出，不应发送代理不支持的 `developer` 角色或 `reasoning_effort` 参数。

## Provider 性能实测（原版记录）

> 以下为原版 README 中记录的真实联网测试结果，保留作为历史实测数据参考。本次 README 重写没有重新执行联网测试，因此这些数据不代表当前服务状态或最新性能。

所有 Provider 均为 Web 端驱动，单轮延迟远高于云端 API，原版记录的实测数据如下：

| Provider | 单轮延迟 | 健康检查 | 能否独立完成任务 |
| --- | --- | --- | --- |
| `deepseek-web` | 21–98s | ✅ 21s | ✅ 分类 10/10 成功（首选，`fallback_from=None`） |
| `gemini-web` | 24s | ✅ 25s | ✅ 分类、中文简报、问答与主动研究全部成功 |
| `chatgpt-web` | 67–120s+ | ✅ 68s | ⚠️ 短任务（分类 3/3）成功；长上下文 query 超过 120s 超时 |

### 原版测试结论

- 三个 Provider 的 Web 会话当时均已登录，`news health` 全部返回 `healthy`。原版记录指出，健康检查超时设置为 10 秒时曾误报 DeepSeek 为 down，随后修复为 `max(llm_timeout, 60s)`。
- 延迟差异明显：DeepSeek 和 Gemini 约 20–25 秒，ChatGPT Web 约 67 秒；长上下文任务可能超过 120 秒。
- ChatGPT Web 处理长上下文（例如包含多个事件的 query）时曾超时。原版记录提到已经支持按 Provider 配置超时（`CHATGPT_WEB_TIMEOUT=300`），并将 query 上下文裁剪至 10 个事件，以降低延迟。
- 故障转移链曾实测有效：DeepSeek 返回 502 后由 Gemini 接管；连续失败 3 次后熔断状态进入 `circuit -> OPEN`。

### 原版各任务实测耗时

| 任务 | Provider | 耗时 |
| --- | --- | --- |
| 分类单次 | `deepseek-web` | 21–98s |
| 分类单次 | `gemini-web` | 约 5s（首次） |
| 健康检查 | `deepseek-web` | 21.3s |
| 健康检查 | `gemini-web` | 24.5s |
| 健康检查 | `chatgpt-web` | 67.6s |
| query（24 个事件） | `chatgpt-web` | 超时（>120s） |
| 主动研究 | `gemini-web` | 数十秒，包含 4 个问题、报告与发现 |

**注意：** 上述耗时和成功率是原版 README 中的历史记录，而非本次验证结果。实际表现会受 Web 会话登录状态、上游服务、上下文长度、代理配置与网络情况影响。

## 定时采集

项目提供一个持续运行的简单调度循环：

```bash
.venv/bin/python -m newsagent.cli.schedule
```

该进程每隔 300 秒检查一次到期来源，并根据来源配置的 `crawl_interval` 执行调度。各类来源的默认调度间隔见配置表；来源具体间隔、上次运行状态和其他来源属性由来源注册与调度代码管理。进程可以通过 `SIGINT` 或 `SIGTERM` 正常停止。

调度器负责按来源间隔触发采集，不等同于定期执行完整的 `news pipeline`、语义聚类、向量索引或报告生成。若需要这些步骤自动运行，应另外配置相应的命令调度流程。

## 数据与输出

| 路径 | 用途 |
| --- | --- |
| `data/newsagent.db` | 默认 SQLite 数据库，保存来源、文章、事件以及流水线状态等数据 |
| `output/` | Markdown、HTML 和 PDF 报告输出目录 |
| `doc/sites.md` | 新闻来源清单 |
| `.env.example` | 环境变量配置示例 |

`news report --hours 24` 默认生成日报命名格式的 Markdown 文件；更长时间范围使用周报命名格式。通过 `--formats md,html,pdf` 可一次请求多种格式，文件共享同一份报告数据和日期，分别使用 `.md`、`.html`、`.pdf` 扩展名。格式生成失败时，命令会报告成功文件与失败原因，并以非零状态结束。报告按照事件评分选择内容，并输出头条及其他事件。实际内容取决于数据库中已有的事件、摘要与评分。

向量索引由 `news index` 显式建立，语义检索通过 `news semantic-search` 执行。常规采集或 `news pipeline` 并不会自动完成全部向量索引工作。

当前默认存储实现是 SQLite。仓库包含存储后端抽象，但不应据此推断 PostgreSQL 或 pgvector 已经作为可直接使用的完整生产后端交付。

## 项目结构

```text
.
├── doc/
│   ├── design_chatgpt.md       # ChatGPT / 混合架构设计
│   ├── design_deepseek.md      # DeepSeek 流水线设计
│   ├── design_gemini.md        # Gemini 相关设计
│   ├── local_llm.md            # 本地 LLM 代理说明
│   ├── sites.md                # 新闻来源清单
│   └── tasks_*.md              # 实施任务与阶段记录
├── output/                     # 生成的 Markdown、HTML 和 PDF 日报/周报
├── scripts/                    # 演示及单 Provider 探测脚本
├── src/newsagent/
│   ├── config.py               # 配置及 Provider 默认值
│   ├── logging_setup.py        # 日志和错误定义
│   ├── sources/                # 来源模型、注册表、调度器
│   ├── fetch/                  # RSS 与 HTML 抓取
│   ├── pipeline/               # 标准化、去重、分类、聚类、排序
│   ├── llm/                    # 客户端、路由、Schema、重试、熔断、健康检查
│   ├── storage/                # SQLite、Repository、搜索、向量及后端抽象
│   ├── agents/                 # Query、Research、Report
│   └── cli/                    # CLI 与持续调度循环
├── tests/                      # 自动化测试
├── .env.example                # 配置模板
└── pyproject.toml              # 包元数据、依赖及入口定义
```

## 测试与排错

### 运行自动化测试

```bash
.venv/bin/python -m pytest -q
```

该命令用于在本地运行仓库测试。本次 README 重写没有运行测试，也没有执行任何联网测试，因此本文不报告未经本次验证的测试结果或 Provider 在线状态。

### 常见问题

**1. 数据库中没有事件**

先运行 `news fetch` 获取文章，再运行 `news pipeline` 执行分类、聚类和排序。没有新文章、来源不可访问或分类失败，都可能导致事件数量不足。

**2. LLM 调用超时或失败**

确认代理进程、登录状态、Base URL 和模型名正确。Web 驱动的服务延迟可能明显高于直接调用云 API；可按 Provider 调整 `*_TIMEOUT`，并检查重试、fallback 与熔断日志。

**3. 部分来源失败**

查看日志和 `news sources` 输出。采集逻辑会隔离来源级异常，但单个来源失败仍可能导致该轮结果为部分成功。

**4. 问答缺少引用或回答不完整**

Query Agent 只能基于本地收集的事件及关联文章作答。先检查事件摘要、关联 URL 与数据库覆盖范围；没有充分证据时，模型应说明信息不足。

**5. 语义搜索没有结果**

先确认数据库里已有文章或事件，再运行 `news index` 建立向量索引。普通的文本搜索和向量语义搜索是不同的检索路径。

**6. 报告为空**

检查相应时间范围内是否已有事件、事件时间字段和排序数据。可以先采集并处理新闻，再运行 `news report`。

## 设计文档

- [ChatGPT / 混合架构设计](doc/design_chatgpt.md)
- [DeepSeek 两阶段流水线设计](doc/design_deepseek.md)
- [Gemini 相关设计](doc/design_gemini.md)
- [本地 LLM 代理部署与约束](doc/local_llm.md)
- [新闻来源清单](doc/sites.md)
- [ChatGPT 实施任务记录](doc/tasks_chatgpt.md)
- [DeepSeek 实施任务记录](doc/tasks_deepseek.md)

---

**开发原则：** 先采集并积累可追溯的数据，再用 LLM 增强理解和组织；尽量把确定性逻辑留在 Python 代码中，并明确区分本地检索、LLM 分析和实时网络操作。