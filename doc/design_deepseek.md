# AI News Agent 设计方案（基于本地 LLM）

> 目标：自动抓取 `doc/sites.md` 中列出的各站点最新信息，用**本地部署的 Web 版大模型**（DeepSeek / Gemini / ChatGPT）完成筛选、摘要、聚合与中文输出。
>
> 本方案与 `design_gemini.md` 互补：Gemini 方案偏重“一次性大批量长上下文总结”，本方案采用 **“先便宜粗筛、再按需精读”的两阶段流水线**，并通过本地 OpenAI 兼容代理调用 LLM，控制 token 成本与幻觉。
>
> LLM Provider 的详细配置见 [`doc/local_llm.md`](./local_llm.md)。

---

## 1. 设计原则

1. **配置即文档**：`doc/sites.md` 是唯一信息源（Single Source of Truth），Agent 不硬编码站点。
2. **两阶段 LLM**：先用轻量模型（`deepseek-chat`）做廉价的批量粗筛/分类，再用推理更强的模型对少量重点文章做深度推理与聚合。
3. **抓取与推理解耦**：抓取层只负责产出结构化 `Article`，LLM 层只吃结构化输入，便于单独测试与替换。
4. **链接不可由 LLM 生成**：原文 URL 全程由代码携带，模型只写摘要文字。
5. **增量与幂等**：同一 URL 只处理一次，重复运行结果稳定。
6. **Provider 抽象**：LLM 调用统一走 `openai` SDK + 本地代理，Provider 可配置切换，业务代码不感知具体模型。

---

## 2. 为什么使用本地 LLM Provider

本地通过三个 OpenAI 兼容代理（端口 8000/8001/8002）分别暴露 DeepSeek / Gemini / ChatGPT 的 Web 端能力：

| 能力 | 说明 | 在本项目中的用法 |
| --- | --- | --- |
| 中文表达 | 母语级中文 | 直接产出中文简报，无需二次翻译 |
| 推理能力 | 强逻辑/归纳 | 多篇同事件合并、趋势判断 |
| 零 API 费用 | 复用 Web 端额度 | 高频粗筛与打标无成本压力 |
| 长上下文 | 声明 1M tokens | 每批 5–12 篇归并 |
| OpenAI 兼容 | `openai` SDK 直连 | 无需额外适配层 |

**能力分工**：

- **粗筛/打标**：`deepseek-chat`（`http://127.0.0.1:8000/v1`）— 快、量大。
- **精读/聚合**：优先 `deepseek-chat`，长文或多篇归并可切换 `gemini-chat` / `chatgpt-chat`。
- **兜底**：某 Provider 不可用时，按配置顺序自动降级到其他 Provider。

---

## 3. 系统架构

```
doc/sites.md
     │  (1) Source Parser
     ▼
sources.yaml / sources.json  ── 结构化站点清单（类别、URL、抓取方式、权重）
     │
     ▼  (2) Fetcher  (async, RSS优先 → Jina Reader 兜底)
raw/*.jsonl  ── 原始 Article 列表
     │
     ▼  (3) Dedup & Normalize (SQLite)
new_articles.jsonl  ── 去重后的新增文章
     │
     ▼  (4) Stage-1 粗筛 & 打标  (deepseek-web)
scored.jsonl  ── 每条含 分类 / 重要性 0-10 / 是否值得精读
     │
     ▼  (5) Stage-2 精读聚合  (本地 LLM, 可切换)
briefs/*.md  ── 按主题聚合的深度摘要
     │
     ▼  (6) Publisher
output/daily_YYYY-MM-DD.md  +  推送(Telegram/飞书/邮件)
```

---

## 4. 模块设计

### 4.1 Source Parser（解析 sites.md）

- 读入 `doc/sites.md`，解析所有 Markdown 表格行，抽出 `名称 / URL / 特点 / 所属类别`。
- 输出 `sources.yaml`，并为每个站点补上人工可维护的元数据：

```yaml
- name: OpenAI Blog
  url: https://openai.com/blog
  category: official
  fetch: rss            # rss | html | jina
  rss: https://openai.com/blog/rss.xml
  weight: 10            # 一手信息权重高
- name: Hacker News
  url: https://news.ycombinator.com/
  category: community
  fetch: api            # 官方 Firebase API
  weight: 4
```

- 首次运行可让 LLM 辅助生成该 YAML（把 `sites.md` 表格喂给本地 `deepseek-chat`），之后人工校对并纳入版本控制。

### 4.2 Fetcher（采集）

**抓取策略优先级**：

1. **RSS/Atom**：`feedparser`，最稳、最快，覆盖大多数官方博客、arXiv、Substack。
2. **官方 API**：Hacker News(Firebase)、Reddit(.json)、GitHub Trending(可解析)。
3. **静态 HTML**：`httpx` + `selectolax`/`BeautifulSoup`，配合 `trafilatura` 抽正文。
4. **兜底**：`https://r.jina.ai/<url>` 返回 LLM 友好 Markdown，穿透部分反爬站点。

**工程要点**：
- `asyncio` + `semaphore` 控制并发（建议 5–8），每个域名限速。
- 统一 UA、超时(15s)、重试(指数退避 2 次)。
- 每站点失败不影响整体；记录 `fetch_errors.log`。
- 产出统一结构：

```json
{
  "id": "sha1(url)",
  "source": "OpenAI Blog",
  "category": "official",
  "title": "...",
  "url": "https://...",
  "published": "2026-10-09T03:12:00Z",
  "raw_text": "...(正文或摘要)",
  "fetched_at": "..."
}
```

### 4.3 Dedup & Normalize（去重）

- SQLite 表 `seen(id TEXT PRIMARY KEY, url, first_seen, title_hash)`。
- 去重键：`sha1(canonical_url)`；标题归一化后做二次判重（防同一文多链接）。
- 时间窗口：默认只保留最近 **48h** 发布或首次发现的内容。
- 增量：`INSERT OR IGNORE`，`id` 已存在则跳过，保证幂等。

### 4.4 Stage-1：粗筛与打标（deepseek-web）

**目的**：用本地 `deepseek-chat` 把上百条压到 15–25 条重点，零 API 成本。

- 批量输入：每批 8–12 条，只给 `title + source + category + 前 500 字`。
- 输出 JSON（要求模型严格 JSON 模式）：

```json
{
  "id": "...",
  "topic": "模型发布|融资|政策|论文|产品|开源|硬件|观点",
  "importance": 8,
  "deep_read": true,
  "reason": "一句话理由"
}
```

- **重要性打分参考**：来源权重 × 事件级别（新模型/大额融资/监管 > 常规更新 > 观点周边）。
- 代码侧对 `importance` 排序并截断，进入 Stage-2。

Prompt 要点（粗筛）：

```
你是 AI 新闻编辑。对每条新闻输出 JSON：topic、importance(0-10)、deep_read(bool)、reason。
判断标准：一手发布/重大事件=高分，转载/旧闻/营销=低分。只输出 JSON 数组，不要解释。
```

### 4.5 Stage-2：精读与聚合（本地 LLM，可切换 Provider）

**目的**：对重点内容做深度理解，并**把同一事件的多篇报道合并成一条**。

- 先按 `topic + 实体`（公司/模型名）聚类，再逐簇调用精读模型。
- 默认用 `deepseek-chat`；长文/复杂归并可切换 `gemini-chat` 或 `chatgpt-chat`（见 §9 Provider 配置）。
- **兼容性约束**：本地 Provider 不支持 `developer` 角色与 `reasoning_effort` 参数，Prompt 一律使用 `system` + `user`。
- 输入：该簇的 `title + raw_text`（必要时用 4.2 的 jina 抓全文）。
- 输出结构化：

```json
{
  "headline": "中文标题（≤30字）",
  "summary": "3–5 句中文摘要",
  "key_points": ["...", "..."],
  "entities": ["OpenAI", "GPT-5"],
  "why_it_matters": "对行业的影响",
  "sources": ["url1", "url2"]   // 由代码回填，模型不得生成
}
```

Prompt 要点（精读）：

```
你是资深 AI 科技记者。基于给定多篇同事件材料，用中文输出：
1) 标题 2) 摘要(3-5句) 3) 要点列表 4) 关键实体 5) 影响判断。
只依据材料，不得编造事实或链接。
```

> **幻觉控制**：`sources` 字段由程序写入原始 URL；若模型输出中出现 http 链接一律丢弃。

### 4.6 Publisher（产出与分发）

输出 `output/daily_YYYY-MM-DD.md`：

```markdown
# AI 日报 · 2026-10-09

## 🔥 头条
### <headline>
<summary>
- 要点...
- 影响：...
来源: [OpenAI](url) · [TechCrunch](url)

## 🧪 模型与研究
...
## 💰 商业与融资
...
## 🛠 开源与工具
...
## 📜 政策与监管
...
```

分发渠道（任选）：Telegram Bot / 飞书或钉钉 Webhook / 邮件 SMTP / 写入静态站点。

---

## 5. 工作流（一次运行）

1. Cron 触发（建议每日 08:00，另加 20:00 增量）。
2. 解析 `sites.md` → 加载 `sources.yaml`。
3. 并发抓取 → 落 `raw/*.jsonl`。
4. SQLite 去重 → `new_articles.jsonl`。
5. Stage-1（deepseek-web）粗筛打标 → `scored.jsonl`。
6. 聚类 + Stage-2（本地 LLM）精读 → `briefs/*.json`。
7. 渲染 Markdown → `output/daily_*.md`。
8. 推送并记录本次统计（抓取数/新增数/入选数/失败站点）。

---

## 6. 成本与性能控制

| 手段 | 效果 |
| --- | --- |
| 粗筛只喂标题+前500字 | 大幅省 token |
| 批量 8–12 条/请求 | 摊薄 system prompt 成本 |
| 仅对 `deep_read=true` 精读 | 精读量降到 ~10% |
| 聚类后同事件只精读一次 | 避免重复推理 |
| 缓存 `sha1(raw_text)` → 摘要 | 同文再跑直接命中 |
| `deepseek-chat` 走 JSON 模式 | 免解析重试 |
| 本地 Web 代理零 API 费用 | 高频调用无成本压力 |
| Provider 自动降级 | 单点故障不阻塞流水线 |

---

## 7. 关键挑战与对策

- **反爬 / 无 RSS**：优先找 RSS；无则 Jina Reader 兜底；仍失败则降级为“只留标题+链接”。
- **内容陈旧**：严格按 `published` 与 `first_seen` 双时间过滤。
- **LLM 幻觉**：链接与实体由代码校验；摘要要求“仅依据材料”。
- **摘要同质化**：Stage-2 明确要求 `why_it_matters`，给出增量判断。
- **限流**：域名级令牌桶 + 全局并发上限；失败重试不超过 2 次。
- **本地代理不可用**：调用前需确保 8000/8001/8002 端口服务已启动；Agent 启动时做健康检查。
- **Provider 兼容性**：本地代理不支持 `developer` 角色与 `reasoning_effort`，统一用 `system`/`user`/`assistant`。
- **可观测性**：每步产出中间文件，便于回溯是哪一层出错。

---

## 8. 目录结构建议

```
NewsAgent/
├── doc/
│   ├── sites.md            # 站点清单（信息源）
│   ├── design_gemini.md
│   └── design_deepseek.md  # 本文档
├── config/
│   └── sources.yaml        # 由 sites.md 生成 + 人工维护
├── src/
│   ├── parser.py           # Source Parser
│   ├── fetcher.py          # RSS/API/HTML/Jina
│   ├── store.py            # SQLite 去重
│   ├── llm_client.py       # 本地 Provider 统一客户端（openai SDK）
│   ├── stage1_screen.py    # 粗筛（deepseek-web）
│   ├── stage2_deepread.py  # 精读（本地 LLM，可切换）
│   ├── publish.py
│   └── main.py
├── data/
│   ├── raw/
│   ├── briefs/
│   └── news.db
└── output/
    └── daily_YYYY-MM-DD.md
```

---

## 9. 环境与配置

### 9.1 本地 Provider 配置

Provider 定义（与 `doc/local_llm.md` 一致）：

| Provider | Base URL | 模型 ID | 用途 |
| --- | --- | --- | --- |
| `deepseek-web` | `http://127.0.0.1:8000/v1` | `deepseek-chat` | 粗筛（默认）+ 精读（默认） |
| `gemini-web` | `http://127.0.0.1:8001/v1` | `gemini-chat` | 精读备用 |
| `chatgpt-web` | `http://127.0.0.1:8002/v1` | `chatgpt-chat` | 精读备用 |

```bash
# .env
LLM_PROVIDERS=deepseek-web,gemini-web,chatgpt-web   # 降级顺序
DEEPSEEK_WEB_BASE_URL=http://127.0.0.1:8000/v1
GEMINI_WEB_BASE_URL=http://127.0.0.1:8001/v1
CHATGPT_WEB_BASE_URL=http://127.0.0.1:8002/v1
STAGE1_PROVIDER=deepseek-web
STAGE2_PROVIDER=deepseek-web
JINA_API_KEY=          # 可选
```

### 9.2 统一 LLM 客户端

所有本地 Provider 均兼容 OpenAI SDK，`apiKey` 固定为 `none`：

```python
from openai import OpenAI

PROVIDERS = {
    "deepseek-web": ("http://127.0.0.1:8000/v1", "deepseek-chat"),
    "gemini-web":   ("http://127.0.0.1:8001/v1", "gemini-chat"),
    "chatgpt-web":  ("http://127.0.0.1:8002/v1", "chatgpt-chat"),
}

def get_client(provider: str):
    base_url, model = PROVIDERS[provider]
    return OpenAI(base_url=base_url, api_key="none"), model
```

> **注意**：本地代理不支持 `developer` 角色和 `reasoning_effort`，调用时不要传这两个参数。

依赖：`httpx feedparser selectolax trafilatura beautifulsoup4 openai sqlite-utils pyyaml tenacity`

---

## 10. MVP 落地顺序

1. **P0**：启动本地代理（8000/8001/8002）+ 健康检查；Source Parser + RSS Fetcher + SQLite 去重。
2. **P1**：统一 LLM 客户端 + Stage-1 粗筛（deepseek-web）+ 简单 Markdown 日报。
3. **P2**：Stage-2 聚类精读（本地 LLM 可切换）+ 分类排版。
4. **P3**：Provider 自动降级 + Jina 兜底抓全文 + 推送渠道 + Cron。
5. **P4**：缓存、失败重试、统计报表。

> 建议先用 3–5 个稳定 RSS 源（OpenAI、Anthropic、Google DeepMind、Hugging Face Papers、arXiv cs.CL）验证全链路，再逐步接入 `sites.md` 中的其余站点。
