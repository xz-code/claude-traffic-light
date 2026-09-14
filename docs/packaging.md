# 打包方案

> 状态：**已实施**。三条交互验收全部通过后开始打包，产物已在本机验证。
> 剩余未验证项见文末表格。

## 决定

| 项 | 选择 | 理由 |
|---|---|---|
| 分发范围 | 拷到别的电脑（公司 / 家里） | 必须真正可移植 |
| UI 形态 | **onedir 便携文件夹** | 启动快（开机自启不拖泥），可整包拷走 |
| hook 运行时 | **随包嵌一个便携 Python** | 目标机器不保证有 Python；hook 保持裸 `.py` 便于排查 |
| hook 落点 | **`%LOCALAPPDATA%\ai-traffic-light\hook\`** | 与便携文件夹解耦，文件夹删了 hook 也不会瞎 |
| hook 安装方式 | **GUI 托盘菜单里「安装 / 卸载 hook」** | 全程不需要 Python |

## 为什么 hook 不编译成 exe

实测数据（2026-09-10，本机）：

```
裸 .py + 系统 Python   中位 299ms / 次
PowerShell 起一次      中位 552ms / 次
```

hook 挂在每一个 `PreToolUse` / `PostToolUse` 上，一个 40 次工具事件的回合就是
**12 秒后台开销**。PyInstaller 的 onefile 还会每次解压上百 MB 到临时目录，
既慢又反复触发杀软扫描。所以 hook 必须走"裸 `.py` + 一个最小解释器"这条路。

（PowerShell 虽然是零依赖，但比 Python 还慢近一倍，已排除。）

## 最终产物长什么样

```
ai-traffic-light-portable\
  AiTrafficLight.exe        GUI 主程序
  _internal\                PyInstaller 依赖（Qt 等）
  hook\                     随包携带的 hook 运行时（安装时会被复制走）
    python\                 Python embeddable 包（~15MB）
      python.exe
      python3xx.zip
      *.pyd / *.dll
    hook_writer.py
    core.py
  start.bat                 启动
  stop.bat                  停止
  说明.txt
```

## 关键：hook 的安装流程

**不能指向便携文件夹**——文件夹一旦被移动或删除，hook 就瞎了。
正确流程是「复制到固定位置」：

```
用户双击 AiTrafficLight.exe
      │
      ├─ 托盘右键 ▸ 安装 hook
      │     ├─ 1. 把 hook\python\ + hook_writer.py + core.py
      │     │     复制到 %LOCALAPPDATA%\ai-traffic-light\hook\
      │     ├─ 2. 读写 ~/.claude/settings.json，指向复制后的位置
      │     └─ 3. 备份原 settings.json
      │
      └─ 此后便携文件夹可以随便挪、随便删，灯照常工作
```

装完写进 `settings.json` 的命令形如：

```json
"command": "\"%LOCALAPPDATA%\\ai-traffic-light\\hook\\python\\python.exe\" \"%LOCALAPPDATA%\\ai-traffic-light\\hook\\hook_writer.py\""
```

## 一个容易踩的坑：embeddable Python 的 `._pth`

Python 官方的 embeddable 包带一个 `python3xx._pth` 文件，它会**限制 `sys.path`
且让解释器进入 isolated 模式**——脚本所在目录**不一定**会被加进 `sys.path`，
于是 `import core` 可能直接失败。

好消息：`src/hook_writer.py` 开头已经做了

```python
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
```

所以不依赖 `._pth` 的行为。**但打包后必须实测一次**，别假设。

## 需要先做的一处重构

`install.py` 里的「写 settings.json」逻辑现在只有 CLI 能调。
GUI 托盘菜单要复用同一份逻辑，所以应该抽成 `src/hook_install.py`，
由 `install.py` 和 GUI 共同引用——**只留一份真相**，否则两边会写出不一样的配置。

## 打包命令

**不要手敲 `pyinstaller`**——单跑 PyInstaller 只会得到 GUI，打不出可用的包：
没有随包的 embeddable Python、没有 hook 组装、没有冒烟测试。用 `build.py`：

```bash
.venv\Scripts\pip install pyinstaller     # 首次
.venv\Scripts\python.exe build.py
```

`build.py` 依次做五件事：

| 步骤 | 做什么 |
|---|---|
| `ensure_embeddable()` | 取 Python embeddable 包，缓存在 `build_cache/`，有就不重下 |
| `check_ctypes()` | 自检那个包能不能跑 |
| `build_gui()` | PyInstaller → `dist/AiTrafficLight/` |
| `assemble()` | 组装 → `dist/AiTrafficLight-portable/` |
| `smoke_test()` | **用随包的 Python 真跑一次 hook**，验它写得出 `state=working` |

唯一参数 `--skip-gui`：只重跑组装、复用上次的 dist，调组装流程时用。

`--paths src` 与那几个 `--hidden-import` 仍然必需（`main.py` 是在 `sys.path`
注入之后才 `from widget import run`，PyInstaller 的静态分析看不到），
它们已经写在 `build_gui()` 里，包括后来新增的 `tooltip`。

### 打完必做的两件事

**① 验产物真的带上了新代码。** 看文件名**不够**——PyInstaller 把应用模块压进
exe 内部的 PYZ，`find` 找不到 `tooltip.py` 之类的文件。要看只能**直接跑 exe**：
模块没打进去的话 `from widget import run` 会立刻抛 ImportError，进程活不过几秒。
（先停掉开发版那盏灯——单实例互斥体挡在 import 之前，否则它会在导入前就退出。）

**② 已装的 hook 不会自动更新。** `build.py` 只写 `dist/`，**完全不碰**
`%LOCALAPPDATA%\ai-traffic-light\hook\`。只要动了 hook 端（`hook_writer.py` / `core.py`），
构建完还得让那份副本更新：托盘菜单「卸载 hook → 安装 hook」，或者直接复制那两个文件。

漏掉②的表现是"灯的行为不对但看不出为什么"——比如提示里没有会话名、原因还是英文。
`hook_install.is_ours_current()` 也帮不上忙：它比对的是 hook **目录**，不是文件内容。

## 待办与风险

| 项 | 状态 |
|---|---|
| 三条交互验收（悬停 / 拖动 / 跳转） | ✅ 用户确认全过 |
| 抽 `src/hook_install.py` 供 GUI 复用 | ✅ 已做，CLI 往返测试 13→0→13 通过 |
| 托盘菜单加「安装 / 卸载 hook」 | ✅ 已做，菜单文案跟随实际状态 |
| 下载并随包携带 embeddable Python | ✅ npmmirror 镜像，`ctypes` 自检通过 |
| 生成 `.ico` 应用图标 | ✅ `tools/make_icon.py` |
| 冻结版在本机实测（Qt 插件是否齐全） | ✅ 窗口正常创建，启动/停止闭环通过 |
| 随包 Python 能否跑通 hook | ✅ 打包时冒烟测试，写出 `state=working` 并识别出 `claude.exe` |
| 便携版专用 start/stop 脚本 | ✅ 不再复用开发版（开发版引用 `.venv`，在便携文件夹里会误报缺依赖） |
| **GUI 菜单里点「安装 hook」的完整流程** | ⚠️ **未实测**——需要人点一下，见下 |
| 开机自启指向打包后的 exe | 未做（便携版目前靠手动启动） |
| Windows Defender 误报 | 未验证，onedir 比 onefile 风险低 |

### 冻结版体积

122.2 MB（其中 `_internal/` 是 Qt 依赖，`hook/python/` 约 11.5 MB）。

## 打包后的验证清单（在目标机器上）

1. 双击 exe，灯能出现
2. 托盘右键 ▸ 安装 hook，提示成功
3. 打开任意项目跑一次 Claude Code，**灯应该从暗变黄**
4. 让 Claude 问你一个问题，**灯应该变红闪**
5. 点红灯，应该跳到对应 VSCode 窗口（前提：那台机器装了 VSCode 且 `code` 在 PATH 里）
6. 重启电脑，灯应该自动起来（若开了自启）
