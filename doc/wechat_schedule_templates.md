# 微信定时推送模板（macOS launchd / Linux cron）

> 对应 `doc/wechat_tasks.md` 阶段 7（TASK-124 ~ TASK-142）。
> **本仓库不自动安装、修改或卸载系统定时任务**（TASK-142）。用户自行复制、修改、安装。
> 真实 iLink 发送仍未实现（协议阻塞），模板只演示调用方式。

## 1. 两种运行方式（TASK-124 / TASK-125）

| 方式 | 命令 | 说明 |
| --- | --- | --- |
| 生成后发送 | `news report --hours 24 --formats md,pdf --send-wechat` | 先生成报告，成功后再发送 |
| 发送已有报告 | `news wechat send` | 只发送，不生成报告/PDF（TASK-127） |

报告生成失败时**不发送、不回退旧报告**（TASK-126）。发送已有报告时**不隐式生成报告或 PDF**（TASK-127）。

模式覆盖：默认继承全局 `default_mode`（`summary_pdf`），可用 `--wechat-mode` 临时覆盖（TASK-128）。

## 2. 公共要求（TASK-137 ~ TASK-142）

- 使用**绝对路径**：Python 解释器、项目目录、日志路径（TASK-137）。
- **不假设**继承交互式 shell 的 `PATH`、虚拟环境或工作目录（TASK-138）。
- **绝不**把令牌或真实凭据写入 plist / crontab / 脚本（TASK-139）。
- 时区为**运行机器本地时区**，夏令时由操作系统处理（TASK-140）。
- 机器关机 / 休眠时**不保证准点执行**（TASK-141）。
- 日志查看方式与并发锁行为见下文（TASK-142）。

> 示例占位符：`/Users/you/NewsAgent`、`/home/you/NewsAgent`、`/Users/you/NewsAgent/.venv/bin/python`。请替换为你的真实路径。

## 3. macOS launchd（TASK-129 ~ TASK-132）

模板文件：`doc/templates/com.newsagent.wechat.plist`

### 3.1 需要修改的字段（TASK-130）

| 字段 | 含义 |
| --- | --- |
| `Label` | 唯一标识，建议 `com.newsagent.wechat` |
| `ProgramArguments` | 第 1 项是 Python 绝对路径；第 2 项是 `-m`；第 3 项 `newsagent.cli.main`；之后是命令参数 |
| `WorkingDirectory` | 项目绝对路径 |
| `StandardOutPath` / `StandardErrorPath` | 日志绝对路径 |
| `StartCalendarInterval` | 每日时间（`Hour` / `Minute`，24 小时制） |
| `EnvironmentVariables` | 如 `NEWSAGENT_WECHAT_DIR`、`NEWSAGENT_DB_PATH` |

### 3.2 安装 / 验证 / 禁用 / 移除（TASK-131）

```bash
# 安装（复制到用户级 LaunchAgents）
cp doc/templates/com.newsagent.wechat.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.newsagent.wechat.plist

# 验证（手动触发一次并查看日志）
launchctl start com.newsagent.wechat
cat /Users/you/NewsAgent/logs/wechat-launchd.err

# 临时禁用
launchctl unload ~/Library/LaunchAgents/com.newsagent.wechat.plist

# 永久移除
rm ~/Library/LaunchAgents/com.newsagent.wechat.plist
```

### 3.3 目标机验证要点（TASK-132）

在目标 macOS 上实测：运行路径、文件权限、机器休眠时是否补执行、错过触发时间后的行为。**不要假设** launchd 一定在休眠后补跑。

## 4. Linux cron（TASK-133 ~ TASK-136）

模板：`doc/templates/wechat.cron`

### 4.1 设置要点（TASK-134）

- 使用绝对路径调用 venv 内的 Python 或 `news` 入口。
- 用 `cd` 切到项目目录，或让命令内部使用绝对路径。
- 通过重定向把 stdout/stderr 写入日志。
- cron 环境**不继承**你的 `PATH` / venv；必要时在 crontab 顶部设置 `PATH=`。

### 4.2 安装 / 检查 / 禁用 / 移除（TASK-135）

```bash
crontab -e              # 编辑并粘贴模板内容
crontab -l              # 检查当前任务
# 禁用：注释掉对应行，或删除该行后重新保存
crontab -r              # 移除全部任务（谨慎）
```

### 4.3 目标机验证要点（TASK-136）

在目标 Linux 上实测：命令是否可执行、权限是否正确、本地时区是否符合预期。cron 的具体时区行为受系统配置影响。

## 5. 并发与重复执行

- 发送服务在每次发送时生成 `execution_id` 并写入 `delivery-state.json`，可用于诊断（TASK-103 / TASK-104）。
- 建议用系统锁（如 `flock`）避免同一任务重叠执行（TASK-103）。模板中已给出可选 `flock` 示例。
- 去重保护**不阻止**用户有意手动重发历史报告。

## 6. 日志查看（TASK-142）

| 平台 | 日志位置 |
| --- | --- |
| launchd | `StandardOutPath` / `StandardErrorPath` 指定的文件 |
| cron | 模板中 `>> logs/wechat-cron.log 2>&1` 指定的文件 |
| 发送状态 | `~/.newsagent/wechat/delivery-state.json` |

## 7. 当前状态

- 阶段 7 的**模板与文档**已交付（TASK-129 ~ TASK-142 中除真机验证外的部分）。
- **TASK-132 / TASK-136 的真机验证未完成**：需要目标 macOS / Linux 环境，本环境不满足。
