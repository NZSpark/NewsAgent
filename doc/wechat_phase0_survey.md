# 微信报告输出：阶段 0 调研结果与 iLink 协议参考

> 对应 `doc/wechat_tasks.md` 阶段 0（TASK-001 ~ TASK-018）。
> 更新日期：2026-10-10。
> 当前结论：**Hermes 参考源码已在本地找到并完成初步阅读；协议实现方案已具备参考依据，但尚未通过真实微信账号端到端验证。** 不将源码研究等同于线上协议验证。

## 1. NewsAgent 现有接口梳理（TASK-001 ~ TASK-007）

| 任务 | 结论 |
| --- | --- |
| TASK-001 CLI 框架 | `argparse`，入口 `src/newsagent/cli/main.py:build_parser()`，通过 `args.func(args)` 调度；项目脚本 `news = newsagent.cli.main:main`。 |
| TASK-002 报告命令 | `news report` 支持 `--hours`、`--formats`；可选附带 `--send-wechat` 和 `--wechat-mode`。 |
| TASK-003 报告输出 | `write_reports(...)` 返回格式到路径的映射；`write_report(...)` 是 Markdown 兼容封装。 |
| TASK-004 文件类型 | Markdown、HTML、PDF；PDF 依赖可选的 WeasyPrint。 |
| TASK-005 报告关联 | 已引入报告 manifest 的方案和相关代码，摘要、正文与 PDF 应通过 manifest/报告解析逻辑关联，不应只凭修改时间猜测。 |
| TASK-006 配置与测试 | 微信模块独立管理配置、凭据、报告输入、发送编排与状态；pytest 覆盖 mock 发送路径。 |
| TASK-007 Python 与基础依赖 | Python 要求 `>=3.10`；项目已有 `httpx`。需要加密和终端二维码显示时可通过可选依赖提供 `cryptography`、`qrcode`，避免强迫所有安装都引入微信功能依赖。 |

## 2. Hermes 参考实现来源与代码链接（TASK-008、TASK-016）

### 2.1 已检查的仓库

- 项目：NousResearch Hermes Agent。
- Git 远程仓库：https://github.com/NousResearch/hermes-agent
- 本地 checkout：`/tmp/hermes-weixin-survey`
- 本地检出的 HEAD：`dce1e9b37581dd62e480a9064dc04a709c2940d3`
- 微信协议适配器：https://github.com/NousResearch/hermes-agent/blob/dce1e9b37581dd62e480a9064dc04a709c2940d3/gateway/platforms/weixin.py
- Hermes 用户指南：https://github.com/NousResearch/hermes-agent/blob/dce1e9b37581dd62e480a9064dc04a709c2940d3/website/docs/user-guide/messaging/weixin.md
- 本地文件：`/tmp/hermes-weixin-survey/gateway/platforms/weixin.py`、`/tmp/hermes-weixin-survey/website/docs/user-guide/messaging/weixin.md`
- 仓库许可证：MIT；本地许可证文件 `/tmp/hermes-weixin-survey/LICENSE`，版权声明为 Nous Research，2025。

以上是**参考版本和源文件位置**，不意味着 NewsAgent 已完整移植 Hermes，也不意味着 Hermes 声明的所有行为都已经在 NewsAgent 的真实微信账号上复现。若后续复制或改编代码，应保留 MIT 许可证和原版权声明，并核对新增依赖自身的许可证。

## 3. 从 Hermes 源码梳理出的 iLink 协议流程（TASK-009 ~ TASK-015）

### 3.1 基础请求约定

- 默认 iLink API base URL：`https://ilinkai.weixin.qq.com`；登录确认结果可以返回账号实际使用的 `baseurl`。
- 常见接口通过 POST 发送 JSON；请求包含 `base_info: {"channel_version":"2.2.0"}`。
- 请求头包括 `Content-Type: application/json`、`AuthorizationType: ilink_bot_token`、`X-WECHAT-UIN`、`iLink-App-Id: bot`、`iLink-App-ClientVersion`；认证请求携带 `Authorization: Bearer <bot_token>`。
- 主要端点：`ilink/bot/get_bot_qrcode`、`ilink/bot/get_qrcode_status`、`ilink/bot/getupdates`、`ilink/bot/sendmessage`、`ilink/bot/getconfig`、`ilink/bot/getuploadurl`。

### 3.2 二维码登录

1. GET `ilink/bot/get_bot_qrcode?bot_type=3`，读取 `qrcode` 和 `qrcode_img_content`。
2. 微信应扫描 `qrcode_img_content` 所指向的二维码内容/URL，而不是只把原始 `qrcode` 字符串当作扫码内容。
3. GET `ilink/bot/get_qrcode_status?qrcode=...`，轮询状态。源码涉及 `wait`、`scaned`、`scaned_but_redirect`、`confirmed`、`expired`；收到重定向状态时需使用 `redirect_host` 切换轮询 host。
4. `confirmed` 后读取 `ilink_bot_id`、`bot_token`、`baseurl` 和可选的 `ilink_user_id`，安全写入本地凭据。
5. 登录过程需要超时、二维码过期刷新及敏感信息保护。终端 ASCII 二维码显示依赖可选的 `qrcode` 包；无法渲染时应至少给出可用 URL 和清晰提示。

### 3.3 入站轮询与收件人绑定

- POST `ilink/bot/getupdates`，请求携带 `get_updates_buf`，响应更新游标并可能返回 `msgs`。
- Hermes 使用约 35 秒的长轮询，并将 `get_updates_buf` 持久化；独立 CLI 进程不能每次都无条件丢弃游标。
- 从实际消息对象提取 `from_user_id` 与 `context_token`。需识别 `room_id` / `chat_room_id` 等群聊标记，NewsAgent 当前设计范围是单一私聊收件人，不应误绑群聊。
- 绑定时要求用户明确确认目标联系人，防止其他入站用户静默接管固定收件人。
- `context_token` 与账号和 peer 关联保存；发送时应使用最新已知上下文。会话上下文并不保证永久有效。

### 3.4 文本发送和错误处理

- POST `ilink/bot/sendmessage`，消息位于 `msg` 对象，常用字段包括 `from_user_id`、`to_user_id`、唯一 `client_id`、`message_type`、`message_state`、`item_list`，并在可用时附带 peer 的 `context_token`。
- 文本项形如 `{"type":1,"text_item":{"text":"..."}}`。应验证 HTTP 状态、JSON 响应和 `ret` / `errcode`，不能将未知响应当作成功。
- Hermes 源码将 `-14` 视为会话过期；`-2` 常用于限流，但 `errmsg` 为 `unknown error` 或 `prepare failed` 时也可能是旧会话问题。上下文过期时，参考实现会在适用场景下尝试一次不带 `context_token` 的发送。应区分真正限流、明确失败与网络超时导致的结果不确定。
- 网络超时可能发生在服务端已经收下消息之后；若不能确认消息是否送达，不应简单自动重试并制造重复消息。应保留部分成功和不确定状态。
- NewsAgent 当前使用 `MAX_TEXT_LEN = 2000` 并在发送器层进行分段。这是项目现有的保守切分设置，**不能据此宣称它就是 iLink 服务端的确切限制**；真实长度边界仍需账号实测。

### 3.5 PDF / 文件附件

Hermes 的文件链路大体为：

1. 生成随机 `filekey` 和 16 字节 AES key，计算明文大小及 MD5。
2. POST `ilink/bot/getuploadurl`，提交 `filekey`、`media_type`、`to_user_id`、`rawsize`、`rawfilemd5`、密文 `filesize`、`no_need_thumb` 和 AES key 的 hex 值。
3. 使用 AES-128-ECB + PKCS#7 padding 加密文件，向服务端返回的上传 URL 通过 POST 上传密文。
4. 从上传响应的 `x-encrypted-param` 读取加密查询参数；随后将它与媒体信息、AES key 和文件信息放到 `sendmessage` 的媒体 item 中。
5. Hermes 特别将 `aes_key` 编为 **AES key 的 hex 字符串再做 Base64**，不是对原始 16 字节直接做 Base64。媒体上传和消息发送应分别检查失败情况。

这属于从参考源码获得的实现线索，并非对真实 iLink 服务端当前行为的独立实测。AES-ECB 的使用是该参考协议实现的现状，不应被推广成通用文件加密建议。

## 4. NewsAgent 当前本地进度与差距

### 已有且有测试覆盖的部分

- 微信配置、凭据与收件人 JSON 存储、POSIX 权限和原子写入逻辑。
- 报告输入解析、摘要 / 正文 / PDF 模式编排。
- 发送重试分类、状态记录、部分成功处理和重复执行保护。
- CLI 的 `news wechat status`、`logout`、`send` 命令骨架，以及调度模板和相关文档。
- 当前开发过程已有的测试记录：此前一轮完整测试为 **119 passed in 7.45s**。这是当时的测试记录，不代表后续未提交改动已经重新测试，也不代表真实微信端到端通过。

### 仍需实现或接线的部分

- 当前 `src/newsagent/cli/main.py` 的 `_wechat_client()` 仍返回 `UnavailableClient`；`login` 和 `bind` 命令仍是未实现提示，parser help 也仍标记为 protocol blocked。
- 当前 `src/newsagent/wechat/client.py` 保留客户端协议和异常类型，但实际 iLink 请求、QR 登录与长轮询绑定尚需真正实现并连接到 CLI。
- 媒体加密上传需要对应的可选依赖、协议级 mock 测试和真实文件接收验证。
- 需要检查 `pyproject.toml` 可选依赖、当前修改中的 `client.py` / `sender.py` / `tests/test_wechat.py`，实现后重新跑完整测试并检查最终 diff。
- 真实账号登录、个人微信绑定、独立进程发消息、长文本顺序、PDF 接收与打开、会话过期恢复、macOS `launchd` 和 Linux `cron` 验证仍未完成。

## 5. 建议实施顺序

1. 将 Hermes 研究所得的协议流程落到独立 `src/newsagent/wechat/ilink.py` 适配层；复用项目现有凭据存储和发送器接口，不引入 Hermes/OpenClaw Gateway 作为运行时依赖。
2. 用可替换的 `httpx` transport / mock response 覆盖二维码成功与过期、重定向、长轮询游标持久化、私聊绑定、请求头与请求体、上下文过期、限流、不确定发送结果和 AES 媒体上传。
3. 将 CLI 的 `login`、`bind`、`send` 真正接入适配层；增加微信相关可选依赖和安装说明。
4. 更新任务清单，只把已有实现并通过相应测试的任务标为完成；源代码研究可解除“找不到 Hermes 源码”的阻塞，但不能解除真实账号 E2E 阻塞。
5. 使用专用微信测试账号执行端到端验证，并记录具体测试日期、环境、收发结果和未解决限制。

## 6. 仍然存在的明确阻塞

- **真实 iLink Bot / 微信账号测试条件**：当前尚无已记录的真实账号 E2E 结果，无法保证主动发送权限、会话生命周期、附件兼容性或协议未公开行为。
- **第三方依赖审查**：Hermes 仓库声明 MIT；新增运行依赖与最终移植代码仍需在实现后检查，并在复制/改编源代码时保留所需声明。
- **目标系统定时验证**：`launchd` / `cron` 模板存在不等于已经在目标 macOS/Linux 环境安装并通过真实运行验证。

> 结论：Hermes 参考实现已解决“没有源代码和协议实现线索”的调研缺口；NewsAgent 当前仍不能据此宣称真实 iLink 发送已经可用。完成实现、mock 协议测试和真实微信端到端验证后，应再次更新本文件与 `doc/wechat_tasks.md` 的任务状态。

## 3. NewsAgent 适配层实现进展（2026-10-10 晚）

在 Hermes 参考源码可用后，NewsAgent 已实现真实 iLink 适配层：

| 模块 | 内容 |
| --- | --- |
| `src/newsagent/wechat/ilink.py` | 同步 httpx 版 iLink 客户端：QR 申请/轮询、`getupdates` 长轮询、`sendmessage` 文本、AES-128-ECB 媒体上传（TASK-066~070） |
| `src/newsagent/wechat/login.py` | QR 登录（终端渲染 + 轮询 + 过期刷新 + 重定向）与入站轮询绑定收件人（TASK-033~044） |
| CLI | `news wechat login/bind` 已接入真实实现，`send` 使用真实客户端 |
| `pyproject.toml` | 新增可选组 `wechat = [cryptography, certifi, qrcode]` |
| `tests/test_wechat_ilink.py` | 15 个协议级测试（`httpx.MockTransport`）：请求构造、错误分类、附件上传、AES |

### 已验证的事实

- **QR 端点已对真实腾讯服务器验证**：`GET https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3` 返回 `HTTP 200`，包含 `qrcode`（32 字符）与 `qrcode_img_content`（`https://liteapp.weixin.qq.com/q/...`）。这确认了登录协议入口正确。
- 协议常量、端点、请求体结构、`context_token` 处理、错误码（`-14` 会话过期、`-2` 限流/陈旧会话）均按 Hermes `dce1e9b3` 移植。
- 媒体上传：`getuploadurl` → AES-128-ECB 加密 → CDN POST（`x-encrypted-param`）→ `file_item`，`aes_key` 用 base64(hex)。

### 仍未经真实账号验证（不声称已确认）

- 扫码后的 `confirmed` 流程与凭据保存（需要人扫真实二维码）。
- 入站消息的实际结构与 `context_token` 取得（需要目标微信主动发消息）。
- 主动私聊是否对目标账号可用、`-2` 陈旧会话的实际触发条件。
- 真实文本 / 长文本 / PDF 的接收效果与服务端长度限制。
- 定时模板在目标 macOS / Linux 的真机行为。

> 这些属于 TASK-163~172 的真实端到端验证，仍需专用微信测试账号与目标机器，不能仅凭 mock 测试声称已完成。
