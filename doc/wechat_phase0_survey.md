# 微信报告输出：阶段 0 调研结果

> 对应 `doc/wechat_tasks.md` 阶段 0（TASK-001 ~ TASK-018）。
> 记录日期：2026-10-10。
> **重要：** 协议部分（TASK-008 ~ TASK-018）**未验证**。本仓库无法访问 Hermes 私有源码，也没有可用的真实微信 iLink Bot 测试账号，因此协议相关任务标记为阻塞，不能声称已验证。

## 4.1 现有项目接口梳理（TASK-001 ~ TASK-007）

| 任务 | 结论 |
| --- | --- |
| TASK-001 CLI 框架 | `argparse`，入口 `src/newsagent/cli/main.py:build_parser()`，`main(argv)` 调度 `args.func(args)`。项目脚本入口 `news = newsagent.cli.main:main`（pyproject）。 |
| TASK-002 `news report` | 参数 `--hours`（默认 24）、`--formats`（默认 `md`，逗号分隔 `md,html,pdf`）。退出码：0 成功；1 部分/全部格式生成失败；2 参数非法（`ValueError`）。 |
| TASK-003 报告写入返回值 | `write_reports(hours, formats, out_dir) -> dict[str, Path]`（format -> 路径）。`write_report(...) -> Path` 为向后兼容的 md-only 封装。**可据此拿到本次生成的确切路径**。 |
| TASK-004 输出格式/命名 | 输出目录默认 `PROJECT_ROOT/output`。文件名 `{prefix}_{YYYY-MM-DD}.{ext}`，`prefix = daily`（hours<=24）或 `weekly`。三种格式：`md` / `html` / `pdf`。 |
| TASK-005 报告 ID / 元数据 / 文件清单 | **当前没有显式的报告 ID 或清单文件。** 同一批报告仅通过 `{prefix}_{date}` 前缀关联三种格式。这意味着 summary/full/pdf 的配对必须依赖**命名约定**，而非权威元数据。已在阶段 3 用严格规则处理（配对不唯一即失败）。 |
| TASK-006 配置/日志/异常/测试组织 | 配置：`src/newsagent/config.py`（dataclass + env 覆盖，`get_config()` / `reset_config()`）。日志：`logging_setup.py`（`get_logger`，统一格式）。异常：`NewsAgentError` 基类及子类。测试：`tests/`，pytest，80 passed。 |
| TASK-007 Python 版本 / 依赖 | `requires-python = ">=3.10"`。已有依赖：httpx、feedparser、selectolax、beautifulsoup4、trafilatura、openai、pyyaml、tenacity。PDF 走可选组 `pdf = ["weasyprint>=62"]`。 |

### 关键结论

1. **摘要是 Markdown 报告的一部分**，不是独立文件。当前 `output/` 只产出 `daily_*.md` / `.html` / `.pdf`，没有单独的「摘要文件」。
   - 因此 `summary` 模式的「摘要来源」需明确：从报告的 Markdown 中提取「头条」段落，或在报告生成阶段额外落一份摘要文件。设计文档假定存在「已生成的摘要」，但**当前实现并未单独输出摘要**。
   - 本实现选择：新增一个轻量的**报告清单（manifest）**，在 `write_reports` 成功后落盘，记录本次生成的三种格式路径 + 提取好的摘要文本。这样 summary/full/pdf 的配对有权威依据（回应 TASK-005 的缺口）。
2. `--formats` 已支持 `pdf`，但 PDF 依赖 `weasyprint` 为可选安装。
3. 报告没有日期以外的唯一标识；同一天多次生成会互相覆盖。manifest 需要带时间戳以避免歧义。

## 4.2 协议调研（TASK-008 ~ TASK-018）—— 阻塞

以下任务**本环境无法完成**，标记为 `[!]`：

- TASK-008 选定 Hermes 参考版本：无法访问其源码仓库/版本信息。
- TASK-009 ~ TASK-015 二维码登录、入站轮询、收件人标识、主动发送、附件协议、错误码：**均需真实 iLink Bot 账号与协议文档验证**，本环境不具备。
- TASK-016 / TASK-017 许可证检查：未取得要移植的源代码，无法执行。
- TASK-018 协议适配层接口：在协议未验证前无法定稿。

**阻塞条件（与任务文件一致）：** 如果 iLink 接口不允许预期的主动私聊，或后续发送依赖的上下文无法合理持久化，必须先记录限制，不得在代码中假装该能力可用。

### 为解除阻塞所需

1. 可访问的 Hermes Python 微信实现（具体 commit/tag）。
2. 一个专用的测试微信号 / iLink Bot 账号。
3. iLink Bot 接口文档（登录、轮询、发送文本、发送附件、上下文有效期）。

在此之前，本仓库只实现**与协议无关**的部分：配置、凭据存储、报告解析与配对、发送模式编排、重试/状态、CLI 骨架，并用**可替换的模拟客户端**做单元/集成测试。真实 iLink 客户端留出清晰的适配层接口。
