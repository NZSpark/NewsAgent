# AI News Agent 任务分解（基于 doc/design_deepseek.md）

> 本文档把 [`doc/design_deepseek.md`](./design_deepseek.md) 的设计拆解为可执行任务。
> 任务按阶段（P0–P4）组织，每个任务含 **ID / 目标 / 产出 / 依赖 / 验收标准**。
> 勾选状态：`[ ]` 未开始，`[~]` 进行中，`[x]` 完成。

---

## 0. 基础设施准备

### T-000 项目脚手架与依赖
- [ ] **目标**：建立目录结构与依赖管理。
- **产出**：
  - 目录：`config/ src/ data/raw data/briefs output/ tests/`
  - `pyproject.toml` 或 `requirements.txt`，依赖：
    `httpx feedparser selectolax trafilatura beautifulsoup4 openai sqlite-utils pyyaml tenacity`
  - `.env.example`（含 §9 全部变量）
  - `.gitignore`（忽略 `data/ output/ .env`）
- **依赖**：无
- **验收**：`pip install -e .`（或 `-r requirements.txt`）成功；`python -c "import httpx, feedparser, openai"` 无报错。

### T-001 本地 Provider 健康检查
- [ ] **目标**：确保三个本地代理可用，并在 Agent 启动时校验。
- **产出**：`src/llm_client.py` 中的 `health_check(provider)`；`src/main.py` 启动即调用。
- **依赖**：T-000
- **验收**：
  - 对 `8000/8001/8002` 各发一次最小 chat 请求，返回 200 即健康。
  - 任一不可用时打印告警，并按 `LLM_PROVIDERS` 顺序降级。
  - 不传 `developer` 角色、不传 `reasoning_effort`。

---

## 阶段 P0：抓取与去重

### T-100 Source Parser（解析 sites.md）
- [ ] **目标**：把 `doc/sites.md` 解析为结构化站点清单。
- **产出**：
  - `src/parser.py`：解析所有 Markdown 表格行，抽出 `名称/URL/特点/类别`。
  - `config/sources.yaml`：含 `name,url,category,fetch,rss?,weight`。
- **依赖**：T-000
- **验收**：
  - 解析 `sites.md` 得到 ≥ 文档中列出的全部条目。
  - 生成 `sources.yaml`，字段完整；可人工编辑后再次被读取。
  - 可选：用本地 `deepseek-chat` 辅助推断 `fetch/rss/weight`，结果人工校对。

### T-101 Fetcher — RSS/Atom
- [ ] **目标**：优先走 RSS 抓取。
- **产出**：`src/fetcher.py` 中 `fetch_rss(source)`，用 `feedparser`。
- **依赖**：T-100
- **验收**：
  - 对 OpenAI / Anthropic / Google DeepMind / Hugging Face Papers / arXiv cs.CL 五个源抓取成功。
  - 产出统一 `Article` 结构（见设计 §4.2）。

### T-102 Fetcher — 官方 API 与静态 HTML
- [ ] **目标**：覆盖无 RSS 的站点。
- **产出**：
  - `fetch_api`：Hacker News(Firebase)、Reddit(`.json`)、GitHub Trending。
  - `fetch_html`：`httpx` + `selectolax`/`BeautifulSoup`，`trafilatura` 抽正文。
- **依赖**：T-101
- **验收**：HN 与 Reddit 各成功抓取 ≥ 10 条；正文非空率 > 80%。

### T-103 Fetcher — 并发、限速、重试、兜底
- [ ] **目标**：工程健壮性。
- **产出**：
  - `asyncio` + `Semaphore(5–8)`；域名级限速。
  - 统一 UA、超时 15s、指数退避重试 ≤ 2 次。
  - Jina Reader 兜底：`https://r.jina.ai/<url>`。
  - 失败写入 `data/fetch_errors.log`，不阻塞整体。
- **依赖**：T-101, T-102
- **验收**：单站点故意失败时，其余站点仍完成；错误被记录。

### T-104 落盘 raw/*.jsonl
- [ ] **目标**：抓取结果持久化。
- **产出**：`data/raw/YYYY-MM-DD.jsonl`，每行一个 `Article`。
- **依赖**：T-103
- **验收**：文件可被逐行 `json.loads`；字段齐全；`id = sha1(url)`。

### T-105 Dedup & Normalize（SQLite）
- [ ] **目标**：增量去重，保证幂等。
- **产出**：`src/store.py`
  - 表 `seen(id TEXT PRIMARY KEY, url, first_seen, title_hash)`
  - `INSERT OR IGNORE`；标题归一化二次判重。
  - 时间窗口过滤：保留最近 48h（`published` 或 `first_seen`）。
  - 产出 `data/new_articles.jsonl`。
- **依赖**：T-104
- **验收**：
  - 连续跑两次，第二次 `new_articles.jsonl` 为空。
  - 同一文章不同 URL（带 utm 等）只保留一条。

---

## 阶段 P1：粗筛与日报

### T-200 统一 LLM 客户端
- [ ] **目标**：Provider 抽象层。
- **产出**：`src/llm_client.py`
  - `PROVIDERS` 映射（见设计 §9.2）
  - `get_client(provider) -> (OpenAI, model)`
  - `chat(provider, system, user, json_mode=False)` 封装
  - 统一处理：不传 `developer`/`reasoning_effort`。
- **依赖**：T-001
- **验收**：对 `deepseek-web` 发一次请求返回内容；切换 `gemini-web`/`chatgpt-web` 同样成功。

### T-201 Stage-1 粗筛打标
- [ ] **目标**：把上百条压到 15–25 条。
- **产出**：`src/stage1_screen.py`
  - 每批 8–12 条，输入 `title + source + category + 前 500 字`。
  - JSON 模式输出：`id, topic, importance(0-10), deep_read, reason`。
  - 按 `importance` 排序截断，产出 `data/scored.jsonl`。
  - Prompt 见设计 §4.4。
- **依赖**：T-105, T-200
- **验收**：
  - 输出为合法 JSON 数组，解析成功率 ≥ 95%（失败重试 ≤ 1 次）。
  - 输入 100 条时，`deep_read=true` 控制在 15–25 条。

### T-202 简单 Markdown 日报
- [ ] **目标**：先跑通端到端。
- **产出**：`src/publish.py` 最简版 → `output/daily_YYYY-MM-DD.md`。
- **依赖**：T-201
- **验收**：生成日报，每条含标题、要点、来源链接。

---

## 阶段 P2：精读与排版

### T-300 事件聚类
- [ ] **目标**：把同一事件的多篇报道分组。
- **产出**：`src/stage2_deepread.py` 中 `cluster(articles)`，按 `topic + 实体`（公司/模型名）聚合。
- **依赖**：T-201
- **验收**：同一事件（如某模型发布）的多篇报道落入同一簇。

### T-301 Stage-2 精读聚合
- [ ] **目标**：逐簇生成中文深度摘要。
- **产出**：
  - 输入：簇内 `title + raw_text`（必要时 Jina 抓全文）。
  - 输出 JSON：`headline, summary, key_points, entities, why_it_matters, sources`。
  - `sources` 由代码回填；模型输出中的 http 链接一律丢弃。
  - 默认 `deepseek-chat`，可切换 Provider（`STAGE2_PROVIDER`）。
  - 产出 `data/briefs/*.json`。
- **依赖**：T-300, T-200
- **验收**：
  - 输出合法 JSON，含全部字段。
  - 每条 `sources` 均为输入中的真实 URL。
  - Prompt 使用 `system + user`，无 `developer` 角色。

### T-302 分类排版
- [ ] **目标**：按 🔥头条 / 🧪模型与研究 / 💰商业融资 / 🛠开源工具 / 📜政策监管 分区渲染。
- **产出**：`src/publish.py` 完整版，模板见设计 §4.6。
- **依赖**：T-301, T-202
- **验收**：日报分区正确；头条置顶；每段含来源链接。

---

## 阶段 P3：兜底、降级与分发

### T-400 Provider 自动降级
- [ ] **目标**：单 Provider 故障不阻塞。
- **产出**：`llm_client.chat` 支持按 `LLM_PROVIDERS` 顺序重试下一个 Provider。
- **依赖**：T-200
- **验收**：停掉 8000 后，Stage-1/2 仍能通过 8001 或 8002 完成。

### T-401 Jina 兜底抓全文
- [ ] **目标**：精读时正文不足时补全。
- **产出**：`fetcher.fetch_jina(url)`，支持 `JINA_API_KEY`（可选）。
- **依赖**：T-103
- **验收**：对无 RSS 的站点返回 Markdown 正文，非空。

### T-402 推送渠道
- [ ] **目标**：日报分发。
- **产出**：Telegram Bot / 飞书或钉钉 Webhook / 邮件 SMTP（任选，至少 1 个）。
- **依赖**：T-302
- **验收**：触发后目标渠道收到当日日报。

### T-403 定时调度（Cron）
- [ ] **目标**：自动化运行。
- **产出**：crontab 或调度脚本，每日 08:00 + 20:00 增量。
- **依赖**：T-402
- **验收**：到点自动生成并推送日报。

---

## 阶段 P4：优化与可观测性

### T-500 摘要缓存
- [ ] **目标**：同文再跑直接命中。
- **产出**：`sha1(raw_text)` → 摘要 的缓存表/文件。
- **依赖**：T-301
- **验收**：二次运行命中缓存，不重复调用 LLM。

### T-501 失败重试与统计报表
- [ ] **目标**：可观测。
- **产出**：
  - 抓取/LLM 失败重试策略统一封装。
  - 每次运行统计：抓取数 / 新增数 / 入选数 / 失败站点，写入日志或汇总文件。
- **依赖**：T-403
- **验收**：运行结束输出统计；失败项可定位到具体站点/步骤。

### T-502 中间产物可回溯
- [ ] **目标**：任一环节可排查。
- **产出**：每步产出中间文件（`raw/ new_articles scored briefs/`）并被文档化。
- **依赖**：T-500
- **验收**：给定一个最终条目，能回溯到原始抓取记录。

---

## 依赖关系图

```
T-000 ─┬─ T-100 ─ T-101 ─ T-102 ─ T-103 ─ T-104 ─ T-105 ─┐
       │                                                  │
T-001 ─┴─ T-200 ─────────────────────────────┬── T-201 ───┴─ T-202 ─┐
                                             │                       │
                                             └── T-300 ─ T-301 ─ T-302┴─ T-402 ─ T-403
                                                          │
T-103 ─ T-401 ────────────────────────────────────────────┘

T-301 ─ T-500 ─ T-502
T-403 ─ T-501
```

---

## 里程碑

| 里程碑 | 包含任务 | 产出 |
| --- | --- | --- |
| **M0 环境就绪** | T-000, T-001 | 依赖装好，三个本地 Provider 健康 |
| **M1 能抓到** | T-100 ~ T-105 | `new_articles.jsonl` 稳定产出 |
| **M2 能筛选** | T-200 ~ T-202 | 端到端生成最简日报 |
| **M3 能精读** | T-300 ~ T-302 | 分区深度日报 |
| **M4 能自动** | T-400 ~ T-403 | 定时 + 推送 + 降级 |
| **M5 可优化** | T-500 ~ T-502 | 缓存 + 统计 + 可回溯 |

---

## 风险与对策（对应设计 §7）

| 风险 | 相关任务 | 对策 |
| --- | --- | --- |
| 反爬 / 无 RSS | T-102, T-401 | Jina 兜底；失败降级为标题+链接 |
| 内容陈旧 | T-105 | `published` + `first_seen` 双时间过滤 |
| LLM 幻觉 | T-301 | `sources` 由代码回填，丢弃模型生成的链接 |
| 摘要同质化 | T-301 | 强制输出 `why_it_matters` |
| 限流 | T-103 | 域名级令牌桶 + 全局并发上限 |
| 本地代理不可用 | T-001, T-400 | 启动健康检查 + 自动降级 |
| Provider 兼容性 | T-200 | 统一 `system`/`user`，禁用 `developer`/`reasoning_effort` |

---

## 建议起步顺序

先用 3–5 个稳定 RSS 源验证全链路，再扩展：

1. T-000 → T-001
2. T-100 → T-101 → T-104 → T-105（先只跑 RSS）
3. T-200 → T-201 → T-202（端到端最小闭环）
4. T-300 → T-301 → T-302
5. T-102 / T-103 / T-400 / T-401 / T-402 / T-403
6. T-500 → T-501 → T-502
