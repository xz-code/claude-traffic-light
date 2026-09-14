# Claude Code Hook 实测记录

> 本文档只记录**实测得到**的事实。每条都标注了验证状态，未验证的一律显式标出。
> 实测环境：2026-09-10，Claude Code VSCode 扩展，Windows 11。
> 采集方式：`probe_hook.py`（`async: true`，纯观察者），挂全局 `~/.claude/settings.json`。

## 一、已验证的结论

### 1. hook 在 VSCode 扩展里会触发 ✅

项目最关键的假设，已证实。而且**热重载生效**——写入 `settings.json` 后无需重启任何东西，立刻就开始了记录。

### 2. 当前 build 接受 13 个事件 ✅

未知事件名**不会**导致整份配置被丢弃。实测注册以下 13 个后配置仍正常生效：

```
SessionStart, SessionEnd, UserPromptSubmit, Stop, SubagentStop,
PreToolUse, PostToolUse, PreCompact, Notification,
PermissionRequest, PermissionDenied, PostToolUseFailure, SubagentStart
```

> 注意：`PermissionRequest` / `PermissionDenied` / `PostToolUseFailure` / `SubagentStart`
> 属于版本相关的新增事件。接受 ≠ 会触发，**是否真的会触发尚未验证**。

### 3. `notification_type` 存在且正确 ✅

研究曾标记 `notification_type` 在部分 build 上会整个缺失（issue #11964）。**本 build 没有这个问题**：

```json
{ "notification_type": "permission_prompt",
  "message": "Claude needs your permission to use AskUserQuestion" }
```

### 4. 红态有两条冗余检出路径，且快的先到 ✅

实测一次 `AskUserQuestion` 的完整时间线：

```
t+0.0s   PreToolUse   (tool_name = AskUserQuestion)   ← 进入红态
t+6.0s   Notification (notification_type = permission_prompt)
t+16.5s  PostToolUse  (tool_name = AskUserQuestion)   ← 红态解除（用户回答后）
```

结论：**`PreToolUse` 比 `Notification` 快 6 秒**，应作为主路径；
`Notification(permission_prompt)` 作为兜底，覆盖 `PreToolUse` 看不到的场景。

## 二、实测载荷

### PreToolUse

```json
{
  "session_id": "2683f6d5-...",
  "transcript_path": "C:\\Users\\<user>\\.claude\\projects\\d--...-ai-traffic-light\\<sid>.jsonl",
  "cwd": "D:\\...\\ai-traffic-light",
  "prompt_id": "977f306d-...",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": { ... },
  "tool_use_id": "toolu_...",
  "permission_mode": "default",
  "effort": { ... }
}
```

### PostToolUse

在 PreToolUse 基础上增加 `tool_response`、`duration_ms`。

### Notification

```json
{ "session_id": "...", "transcript_path": "...", "cwd": "...",
  "prompt_id": "...", "hook_event_name": "Notification",
  "message": "Claude needs your permission to use Bash",
  "notification_type": "permission_prompt" }
```

### UserPromptSubmit

```json
{ "session_id": "...", "transcript_path": "...",
  "cwd": "d:\\...\\ai-traffic-light",
  "prompt_id": "...", "hook_event_name": "UserPromptSubmit",
  "permission_mode": "auto", "prompt": "开始" }
```

> 注意三点：
>
> 1. **`prompt` 不一定是人打的字。** 见下面「注入的上下文」——本项目拿它当会话标题的
>    唯一来源，所以这一条直接决定了标题会不会是一行垃圾。
> 2. 这里**没有 `tool_name`**——hook 端不能用统一的字段名去取工具，
>    得按事件分别取（`PreToolUse` 看 `tool_name`，`Notification` 看 `message`）。
> 3. `cwd` 在这一条里是**正斜杠**（`d:\...\ai-traffic-light`），而 `PreToolUse` 给的是反斜杠。
>    任何路径比较都必须先 `core.normalize_path()`。

#### 注入的上下文 ⚠️

**`UserPromptSubmit` 不只为"人敲的字"触发——IDE 注入的上下文也会走它。**
2026-09-11 在真机上撞到：会话标题变成了

```
<ide_opened_file>The user opened the file d:\...\ai-traffic-light\start.bat …
```

也就是说这条事件会带着 `<ide_opened_file>…</ide_opened_file>` 这样的载荷进来。
后果不止是难看：标题的规则是"第一条胜出"，所以这条垃圾**会永远卡住**，
之后说再多真话都不会覆盖它。

判定规则（`core.is_injected_prompt`）：**以 `<标签>` 开头的就当作注入**。
用正则而不是标签清单，是因为 Claude Code 加新标签时不会通知我们，
而误判的代价只是那一行暂时没有标题（下一条真指令就补上）。

### Stop

```json
{ "session_id": "...", "transcript_path": "...", "cwd": "...",
  "prompt_id": "...", "hook_event_name": "Stop",
  "permission_mode": "auto", "effort": {"level": "xhigh"},
  "stop_hook_active": false,
  "last_assistant_message": "## 结果\n\n**地基成立……**",
  "background_tasks": [], "session_crons": [] }
```

> `stop_reason` **不存在**，所以识别不出 max_tokens / 异常结束。

## 三、两个与预期不符的细节 ⚠️

### 1. `cwd` 用反斜杠

实测为 `D:\...\ai-traffic-light`，**不是**正斜杠。
解析、去重、以及拼 `code -r <cwd>` 跳转命令时都必须做路径规范化。

### 2. hook 的父进程是 shell，不是 Claude

`os.getppid()` 拿到的是 `bash.exe`（VSCode 扩展下）或 `cmd.exe`（经 cmd 调用时），
**不是** `claude.exe` / `node.exe`。

影响：前面设想的"按 PID 清理僵尸会话"不能只取一层父进程，
必须沿进程链向上多走一到两层。**该方案尚未实现，可行性待验证。**

## 四、状态映射（设计决策）

语义采用本项目定义：**红 = 等待你 / 黄 = 工作中 / 绿 = 完成待验收 / 暗 = 空闲**。

| 事件 | 状态 | 验证状态 |
|---|---|---|
| `UserPromptSubmit` | 🟡 黄 | ✅（探针其实抓到了，早先误标为"未触发"） |
| `PreToolUse`（一般工具） | 🟡 黄 | ✅ |
| `PreToolUse(AskUserQuestion)` | 🔴 红 | ✅ |
| `PreToolUse(ExitPlanMode)` | 🔴 红 | 未验证 |
| `Notification(permission_prompt)` | 🔴 红 | ✅ |
| `PostToolUse(AskUserQuestion)` | 🟡 黄（解除红） | ✅ |
| `PostToolUse` | 🟡 黄 | ✅ |
| `Stop` | 🟢 绿 | ✅（载荷含 `stop_hook_active`、`last_assistant_message`） |
| `SessionStart` / `SessionEnd` | ⚫ 暗 | 未触发 |

### 与原参考项目的两处有意偏离

原项目 `vibecoding-signal-light` 的绿是"空闲"，本项目绿是"完成待验收"，
语义不同，故以下两处**不能照搬**：

- **`SubagentStop` → 🟡 黄**（原项目映射为 `tool_done`）
  子代理干完不代表主回合结束，照搬会满屏假绿灯。
- **`PostToolUseFailure` → 🟡 黄**（原项目映射为 `blocked` / 红）
  工具失败时 Claude 通常自行重试，并未在等人。
  红的定义应严格限定为"**回合被人为挂起，在等你**"。

> 上述第二条为待确认决策，理由见上。

## 五、尚未验证的风险

| 风险 | 状态 |
|---|---|
| `Stop`(绿) 是否可靠触发、`stop_hook_active` 取值 | ✅ 已实测：正常收尾会触发，载荷**没有** `stop_reason`，无法识别异常结束 |
| `UserPromptSubmit`(黄) 载荷 | ✅ 已实测，七个字段见下（**没有 `tool_name`**） |
| `SessionStart/End`(暗) 触发时机与 `source`/`reason` 取值 | 未触发 |
| `PermissionRequest` 等新事件是否真的会触发 | 未验证 |
| `ExitPlanMode` 归红是否正确 | 未验证 |
| 进程链回溯拿 Claude PID 是否可行 | 未验证 |
| 经典 `Notification(idle_prompt)` 是否会误报 | 未验证（研究称其为 ~60s 定时器启发式，**不要用作绿态信号**） |

## 六、附：参考项目的可复用资产

`../vibecoding-signal-light`（GitHub: `starlight36/vibecoding-signal-light`）是同一问题的
**硬件实现**（MCP2221A USB GPIO + 实体红绿灯，Rust 运行时）。以下为其实战沉淀，可直接借鉴：

- `native/src/hooks/claude_code.rs` —— 12 事件的映射表（含版本相关事件）
- `specs/001-rust-runtime-migration/contracts/hook-event-contract.md` —— 会话键优先级、
  Owner Process（按 PID 清理死会话）契约、退出码约定
- `native/src/runtime/` —— 本地 IPC + 会话存储 + 状态聚合服务
- `legacy-python-main` 分支 —— 该项目的**旧 Python 实现**

**会话键优先级**（直接采用）：

```
session_id → CLAUDE_CODE_SESSION_ID → cwd → global
```

**聚合优先级**（本项目）：🔴 红 > 🟢 绿 > 🟡 黄 > ⚫ 暗。

## 七、本项目写入的状态文件字段

hook 端不是只读观察者——它往 `%LOCALAPPDATA%\ai-traffic-light\sessions\` 写文件。
这里记的是**我们写入的契约**，它由"实测载荷里有什么"直接推出来。

| 字段 | 来源 | 说明 |
|---|---|---|
| `state` | `core.map_event()` | 灯态，UI 端聚合的依据 |
| `title` | `UserPromptSubmit` 的 `prompt` | 见下 |
| `reason` | `core.reason_text()` | 一句中文，给悬停提示用 |
| `cwd` / `project` | `cwd` | 路径要先规范化（实测是反斜杠） |
| `owner_pid` / `owner_name` / `chain` | 进程链回溯 | 僵尸清理靠它 |

### `title` 的规则

- **只有 `UserPromptSubmit` 会产生它**，值 = `core.clip_text(prompt)`（折叠空白、截 60 字）。
- **注入的上下文不算**：prompt 以 `<标签>` 开头的一律跳过（见上文的实测发现）。
- **hook 看到的第一条 prompt 胜出**：写入时用的是
  `existing.get("title") or _title_from_prompt(...)`，已有的永不被覆盖。
  但旧值如果**本身就是注入内容**（早期版本没滤就存进去了），当作没有，
  于是下一条真指令能把它覆盖掉——坏标题会自愈。
- 因此 `handle()` **必须先读旧文件再写**。这一点踩过：原实现对改变灯态的事件
  直接写一个全新 dict、不读旧文件，于是 `title` 会在下一个事件就被抹掉。
  而 `_read_existing` 必须带重试——`Aggregator.acknowledge` 用的是非原子的
  `write_text`，撞上会读到半截 JSON，读失败的代价也正是丢掉 `title`。

两个已知例外（是设计取舍，不是 bug）：

1. **升级前就开着的会话没有 `title`**（那时还没这个字段），显示层回退到
   `#会话号前8位`。那个会话结束就没了。
2. **被删后重建的会话，标题是让它复活的那条指令**，不是最初那条。
   `SessionEnd` 和右键「忽略会话」都会删文件；重建时 hook 从零开始看，
   所以"第一条"就是复活后的第一条。
绿排第二，因为它是"需你确认才消失"的可操作状态；黄不打扰人。
