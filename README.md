# AI 状态灯 / AI Traffic Light

> 给 Claude Code 一盏看得见的灯。固定在桌面上，一眼就知道它是在干活、在等你、还是干完了。

![四态预览](docs/preview.png)

## 四态

| 灯 | 状态 | 含义 |
|---|---|---|
| 🔴 红（闪烁） | Waiting | 需要你的回复、确认、授权或补充文件 |
| 🟡 黄（常亮） | Working | AI 正在努力干活，安心喝咖啡 |
| 🟢 绿（常亮） | Done | 干完了，可以验收。**单击确认后才熄灭** |
| ⚫ 暗 | Idle | 没有活跃任务 |

绿灯刻意**不超时自动熄灭**——只有你点过它才算数。代价是离开工位时灯会一直绿着，
好处是完成事件绝不会被错过。

**完全静音**，不发出任何声音，纯视觉提示。

## 工作原理

```
Claude Code 事件
      │  hooks（async，不阻塞）
      ▼
src/hook_writer.py  ──原子写──▶  %LOCALAPPDATA%\ai-traffic-light\sessions\<会话>.json
                                            │  每 250ms 轮询
                                            ▼
                                   src/widget.py（PySide6 悬浮窗）
```

每个 Claude Code 会话写一个独立的状态文件，互不干扰；UI 端按
**红 > 绿 > 黄 > 暗** 的优先级聚合成一盏灯，所以多个会话同时跑也不会漏掉告警。

## 安装

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

python install.py install        # 把 hook 装进 ~/.claude/settings.json（会先备份）
python install.py autostart-on   # 可选：开机自启
```

> **升级后要重装 hook（便携版必做）。** hook 运行时是被**复制**到
> `%LOCALAPPDATA%\ai-traffic-light\hook\` 的一份副本，换版本不会自动更新。
> 症状是提示里没有会话名、原因还是英文。托盘右键点一次「卸载 hook」，
> 再点一次「安装 hook」即可。开发模式直接跑 `src\`，不用这一步。
>
> 注意托盘菜单**认不出**这种情况：它比对的是 hook 目录的路径，不是文件内容，
> 所以"同一个目录但文件旧了"它认为一切正常。

## 启动 / 停止

装好之后日常只需要双击两个文件：

| 文件 | 作用 |
|---|---|
| **`start.bat`** | 启动信号灯（无黑框窗口，可固定到任务栏/开始菜单） |
| **`stop.bat`** | 停止。平时用托盘图标的「退出」更顺手，这个是托盘够不着时的逃生口 |

命令行等价写法：

```bash
.venv\Scripts\pythonw.exe src\main.py    # 启动
.venv\Scripts\python.exe tools\stop.py   # 停止
```

**只允许开一盏灯**：重复启动会被命名互斥体挡掉，不会出现两盏叠在一起。
启动时会把 pid 写进 `%LOCALAPPDATA%\ai-traffic-light\light.pid`，
`stop.bat` 靠它精确停止——不靠匹配命令行去猜
（venv 的 `pythonw.exe` 用相对路径启动，命令行里根本没有项目名；
而用来匹配的关键字又出现在执行匹配的 shell 自己的命令行里，会连自己一起杀）。

## 交互

| 操作 | 效果 |
|---|---|
| 拖动 | 移动位置（自动记忆，多显示器安全） |
| **单击点亮的那颗灯珠**（红灯时） | 把等待你的那个 VSCode 窗口拉到前台 |
| **单击点亮的那颗灯珠**（绿灯时） | 确认，绿灯熄灭 |
| 悬停 | 富文本提示：每个会话一行（`项目 · 指令` + 彩色状态词）、等待原因、底部操作提示 |
| 右键 | 跳到等待中的窗口 / 忽略会话 / 回到默认位置 / 退出 |
| **右键 → 忽略会话 → 选一个** | 把某个你不再关心的会话从聚合里摘掉（只影响这一个） |
| 托盘图标 | 跟随灯色变化，可显示隐藏或退出 |

**点击区刻意只限于点亮的那颗灯珠**，外壳和没亮的灯珠都是死区。
原因见下面「点击安全性」。

### 悬停提示读什么

深色卡片，一行一个会话：左边是 `项目 · 指令`，右边是同样带色的状态词，右边缘对齐。

- **`指令` 是该会话的第一句提问**（截到 12 字）。它只由 hook 在第一次
  `UserPromptSubmit` 写入，之后任何事件都不会改它——所以同项目的两个会话能分清。
- 本次升级之前就开着的会话没有这句指令，显示成 `项目 · #会话号前8位`。
  不是故障，那个会话结束就没了。
- 只列**非暗态**会话，最多 6 行，再多折叠成 `…还有 N 个`。红灯永远在第一行、加粗。
- **等待原因是中文**。认不出的场景宁可只说一句笼统的话，也不把 Claude Code 的
  英文原文糊上去。
- 鼠标停住时**不闪**：只有内容真的变了才会重设提示框（改之前是每 250ms 无条件重设，
  正在显示时会被反复重算尺寸和位置）。

### 为什么需要「忽略会话」

红态只有一个自动出口：**同一个会话的下一个事件**。而一个被丢下的会话
（VSCode 窗口开着、`claude.exe` 还活着）永远不会有下一个事件——僵尸清理也够不着它，
因为进程还活着。于是那盏红灯永远闪，还因为红是绝对优先级，把你其他会话的绿/黄
一起盖住。

右键 → 忽略会话 → 点那一个：它的状态文件被删掉，红灯立刻让位。

**这不是永久静音**——那个会话下次真有动静（新的授权请求、新的工具调用、回合结束）
时会自己回来。所以"忽略"的语义是"这一轮我不要了"，不是"以后都别烦我"。

菜单里带 `●` 的那一条就是正在点亮这盏灯的会话。忽略一个**非** `●` 的红灯时灯可能
仍是红的——那是另一个红灯在占位，不是没生效。

至于为什么把它放在右键子菜单、而不是单击灯珠上：它会删状态。而你单击灯珠的犯错
成本必须是零——见下面「点击安全性」。

## 文件结构

```
src/core.py          共享核心：路径、状态定义、事件映射、中文文案、进程链回溯
src/hook_writer.py   hook 端（纯标准库，不依赖 venv）
src/aggregator.py    扫描状态文件、僵尸清理、优先级聚合
src/tooltip.py       悬停提示的文案、配色与富文本渲染（纯逻辑，不依赖 Qt）
src/widget.py        PySide6 悬浮窗
install.py           hook 安装 / 卸载 / 状态 / 开机自启
tools/simulate.py    不启动 Claude 也能驱动灯，用于调试和预览
tools/render_preview.py  离屏渲染四态预览图
tools/render_tooltip.py  离屏渲染悬停提示 + 量右列对齐
probe_hook.py        探针：把原始 hook 事件落盘，排查用
docs/hook-findings.md  全部实测记录（含验证状态标注）
```

## 开发

```bash
# 事件映射与聚合：一条命令跑完 26 步典型场景并逐条断言
python tools/simulate.py map      # 打印事件映射表自检
python tools/simulate.py playback        # 按真实节奏回放，边看灯边对数
python tools/simulate.py playback fast   # 不打间隔，纯断言
python tools/simulate.py multi    # 造三个并发会话，验证优先级聚合
python tools/simulate.py red      # 手动摆出红态
python tools/simulate.py clear    # 清掉模拟会话

# 点击判定回归测试
.venv\Scripts\python.exe tools\test_click.py

# 忽略会话的回归测试（删除范围、复活语义、菜单接线）
.venv\Scripts\python.exe tools\test_ignore.py

# 悬停提示的回归测试（标题持久化、中文原因、转义、对齐、防闪烁）
.venv\Scripts\python.exe tools\test_tooltip.py

# 重新生成预览图
.venv\Scripts\python.exe tools\render_preview.py

# 悬停提示长什么样——不用去悬停真灯
.venv\Scripts\python.exe tools\render_tooltip.py            # 固定样例，一屏看全
.venv\Scripts\python.exe tools\render_tooltip.py --align    # 量右列有没有对齐
.venv\Scripts\python.exe tools\render_tooltip.py --live     # 我此刻的提示长什么样
```

排查问题时，给悬浮窗加 `AI_TRAFFIC_LIGHT_DEBUG=1` 环境变量，
它会把每次状态变化、丢弃会话、点击判定写进
`%LOCALAPPDATA%\ai-traffic-light\debug.log`。默认关闭，零开销。

## 几个实测得来的、反直觉的实现约束

这些都是实际跑出来的，不是照文档抄的。详见 [docs/hook-findings.md](docs/hook-findings.md)。

- **红态走 `PreToolUse` + `Notification`，不靠 `PermissionRequest`**——
  实测这个事件一次都没触发过，虽然确实发生了授权提示。
- **`Notification(idle_prompt)` 必须忽略**——它是 ~60 秒定时器启发式驱动的，
  会延迟且会在长思考期间误报，拿它当信号等于随机亮红灯。
- **`SubagentStop` 归黄不归绿**——子代理干完不代表主回合结束。
- **`PostToolUseFailure` 归黄不归红**——工具失败时 Claude 会自己重试，并没有在等人。
- **`Stop` 载荷里没有 `stop_reason`**，无法识别异常结束。
- **僵尸会话靠进程链判定**：hook 的父进程是临时的 shell（秒生秒灭），
  必须沿链找到 `claude.exe` 才算找到主人。
- **hook 的 `cwd` 是反斜杠**，路径比较和跳转都要先规范化。

### 点击安全性

这是个置顶悬浮的小窗，鼠标很容易扫过它——而"误触"的后果是**告警被静默确认掉**，
你永远不会知道曾经有过告警。对一个"别让我错过"的工具来说，这不可接受。所以：

- **点击必须落在点亮的那颗灯珠上**，外壳和灭灯都是死区
- **必须有配对的按下 + 释放**，且移动不超过 6px、间隔不超过 1.5 秒

后者修的是一个真实发生过的 bug：原写法把"没有配对的按下"当成"没移动"，于是判定成点击。
鼠标按着左键扫过灯、或锁屏解锁时的一次游离 release，都会触发伪点击。
它表现为"绿灯偶尔自己灭了"，且极难复现。

判定逻辑有回归测试锁着：`tools/test_click.py`。

**「忽略会话」因此放在右键子菜单里，不占单击**。它同样是破坏性的（会删掉一次告警），
而右键要两下才能触发，也不在鼠标扫过灯珠的那条路径上。

### 原子写在 Windows 上的坑

`os.replace` 在目标文件**正被任何进程打开时**会抛 `PermissionError`——哪怕对方只是在读。
UI 端每 250ms 轮询一次，撞上这个窗口是迟早的事。所以 hook 端写入必须带重试
（`src/hook_writer.py`），且 UI 端**绝不能因为一次读取失败就删掉会话**——
那等于凭空抹掉一次告警。这两条都已经踩过并修好了。

## 卸载

```bash
python install.py uninstall      # 移除 hook（只删本项目的，保留你自己的其他 hook）
python install.py autostart-off  # 关掉开机自启
```

## 致谢

灵感来自 [vibecoding-signal-light](https://github.com/starlight36/vibecoding-signal-light)——同一问题的
硬件实现（MCP2221A USB GPIO + 实体红绿灯）。本项目的 hook 事件映射与会话键优先级
大量参考了它的实战沉淀，但语义和实现都是独立的：它用真实硬件，本项目用屏幕上的悬浮窗。
