"""共享核心：路径、状态定义、事件映射。

hook 端（系统 Python，仅标准库）与 UI 端（venv 里的 PySide6）都导入本模块，
保证"事件怎么变成灯态"这件事只有一份真相。
"""

import os
import pathlib
import re
import time

# ---------------------------------------------------------------- 路径

STATE_DIR = pathlib.Path(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
) / "ai-traffic-light"
SESSIONS_DIR = STATE_DIR / "sessions"
CONFIG_PATH = STATE_DIR / "config.json"
DEBUG_LOG = STATE_DIR / "debug.log"


def debug(msg, exc_info=False):
    """排查用日志。只有设了 AI_TRAFFIC_LIGHT_DEBUG 才会写，平时零开销。"""
    if not os.environ.get("AI_TRAFFIC_LIGHT_DEBUG"):
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(DEBUG_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%H:%M:%S')}] pid={os.getpid()} {msg}\n")
            if exc_info:
                import traceback
                fh.write("".join(traceback.format_stack()[:-1]))
    except Exception:
        pass

# ---------------------------------------------------------------- 状态


RED = "waiting"      # 需要你的回复 / 确认 / 授权
YELLOW = "working"   # AI 正在干活
GREEN = "done"       # 干完了，可验收（点一下才熄灭）
DARK = "idle"        # 没有活跃任务

#: 聚合优先级，数字越小越优先抢占灯。
#: 绿排第二，因为它是"需你确认才消失"的可操作状态；黄不打扰人。
PRIORITY = {RED: 0, GREEN: 1, YELLOW: 2, DARK: 3}

#: 会让 Claude 当场停下来等人的工具。命中即判红。
#: AskUserQuestion —— Claude 反问你；ExitPlanMode —— 计划模式待批。
BLOCKING_TOOLS = frozenset({"AskUserQuestion", "ExitPlanMode"})

#: 仅这些 notification_type 判红。
#: 特别注意：绝不能把 idle_prompt 也算进来——它是 ~60 秒定时器启发式驱动的，
#: 会延迟且会在长思考期间误报，拿它当信号等于随机亮红灯。
RED_NOTIFICATIONS = frozenset({"permission_prompt"})

#: 收到即代表"这个会话结束了"，直接删状态文件。
TERMINAL_EVENTS = frozenset({"SessionEnd"})


def map_event(event_name, payload, current_state=None):
    """把一个 hook 事件映射成新灯态。

    返回灯态字符串；返回 None 表示"本事件不影响灯态，保持原样"。

    `current_state` 目前未参与判断，但保留在签名里，方便以后加
    "只在黄态时才转绿"之类的状态机约束。
    """
    if event_name in TERMINAL_EVENTS:
        return None  # 由调用方负责删除

    if event_name == "SessionStart":
        return DARK

    if event_name == "UserPromptSubmit":
        return YELLOW

    if event_name == "PreToolUse":
        tool = payload.get("tool_name")
        return RED if tool in BLOCKING_TOOLS else YELLOW

    if event_name in ("PostToolUse", "PostToolUseFailure", "PermissionDenied"):
        # 工具失败不算红：Claude 通常会自行重试，并没有在等人。
        # 红的定义严格限定为"回合被人为挂起"。
        return YELLOW

    if event_name in ("PreCompact", "SubagentStart", "SubagentStop"):
        # SubagentStop 归黄不归绿：子代理干完不代表主回合结束，
        # 归绿会满屏假绿灯。
        return YELLOW

    if event_name == "PermissionRequest":
        # 本 build 实测不触发（走的是 Notification），但留着以防别的版本会发。
        return RED

    if event_name == "Notification":
        if payload.get("notification_type") in RED_NOTIFICATIONS:
            return RED
        return None

    if event_name == "Stop":
        # 注意：本 build 的 Stop 载荷里没有 stop_reason 字段，
        # 所以无法像参考项目那样识别 max_tokens / error 异常结束。
        return GREEN

    return None  # 未知事件一律忽略，不猜


# ---------------------------------------------------------------- 文案

#: 会话标题（首条指令摘要）的存盘长度上限
TITLE_MAX = 60

#: 从 Claude Code 的授权提示里抠出工具名：
#: "Claude needs your permission to use AskUserQuestion" -> AskUserQuestion
_NOTICE_TOOL_RE = re.compile(r"permission to use\s+([^\s,.;]+)")

#: 以标签开头的 prompt 是 IDE / 客户端**注入的上下文**，不是人打的字。
#: 实测踩到过：`UserPromptSubmit` 不只为人敲的字触发，IDE 打开文件也会走它，
#: 于是 `<ide_opened_file>The user opened the file ...` 被当成了"第一条指令"，
#: 而那行标题会永远卡住（第一条胜出）。
_INJECTED_RE = re.compile(r"^<[a-z][a-z0-9_-]*>")


def clip_text(s, limit=TITLE_MAX):
    """折叠空白并截断。用于把用户的第一句提问收成一行标题。"""
    return " ".join((s or "").split())[:limit]


def is_injected_prompt(text):
    """这段文本是系统注入的上下文，而不是人打的字？"""
    return bool(_INJECTED_RE.match(text or ""))


def reason_text(event, payload):
    """把一个事件翻译成一句中文的"原因"，给悬停提示用。

    刻意不透传 Claude Code 的英文原文：同一个字段一会儿中文、一会儿英文，
    长度从 12 字到 60 字不等——这正是悬停提示读不下去的原因之一。
    认不出的场景**宁可返回空串**（提示框干脆不显示原因行），也不糊一句英文上去。

    与 map_event 分开是必要的：一句话可能对应多个事件，而同一个事件也可能
    带不同的话——工具名只藏在 Notification 的 message 里，不在事件名里。
    """
    if event == "Notification":
        if payload.get("notification_type") not in RED_NOTIFICATIONS:
            return ""
        m = _NOTICE_TOOL_RE.search(payload.get("message") or "")
        return f"请求授权：{m.group(1)}" if m else "请求授权"

    if event == "PreToolUse":
        tool = payload.get("tool_name")
        if tool == "AskUserQuestion":
            return "Claude 在向你提问"
        if tool == "ExitPlanMode":
            return "计划待你批准"
        return ""

    if event == "Stop":
        return "本轮结束，等你验收"

    if event == "UserPromptSubmit":
        return "正在干活"

    return ""


def project_name(cwd):
    """从 cwd 取出可读的项目名。"""
    if not cwd:
        return "(未知)"
    try:
        return pathlib.PurePath(cwd.rstrip("\\/")).name or cwd
    except Exception:
        return cwd


def normalize_path(p):
    """规范化路径：实测 hook 给的 cwd 是反斜杠，跳转和去重都要统一。"""
    if not p:
        return ""
    try:
        return os.path.normcase(os.path.normpath(p))
    except Exception:
        return p


# ---------------------------------------------------------------- 进程链

_WIN = os.name == "nt"

if _WIN:
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _MAX_PATH = 260

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * _MAX_PATH),
        ]

    def _k32():
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        return k

    def _snapshot():
        """返回 (parent_of, name_of) 两张 pid 索引表。"""
        k = _k32()
        snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        parent_of, name_of = {}, {}
        if snap == wintypes.HANDLE(-1).value:
            return parent_of, name_of
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            if k.Process32FirstW(snap, ctypes.byref(entry)):
                while True:
                    pid = entry.th32ProcessID
                    parent_of[pid] = entry.th32ParentProcessID
                    name_of[pid] = entry.szExeFile
                    if not k.Process32NextW(snap, ctypes.byref(entry)):
                        break
        finally:
            k.CloseHandle(snap)
        return parent_of, name_of

    def process_chain(start_pid=None, max_depth=12):
        """从 start_pid 向上回溯父进程，返回 [[pid, name], ...]。

        之所以要回溯，是因为实测 hook 的父进程是 bash.exe / cmd.exe 这类 shell，
        而不是 Claude 本身——只取一层拿不到真正的主人。
        """
        start = os.getpid() if start_pid is None else start_pid
        try:
            parent_of, name_of = _snapshot()
        except Exception:
            return []
        chain, seen, pid = [], set(), start
        for _ in range(max_depth):
            if not pid or pid in seen or pid not in name_of:
                break
            seen.add(pid)
            chain.append([pid, name_of[pid]])
            pid = parent_of.get(pid, 0)
        return chain

    def process_name(pid):
        """按 pid 查进程可执行文件名，查不到返回 None。"""
        try:
            _, name_of = _snapshot()
            return name_of.get(int(pid))
        except Exception:
            return None

    def pid_alive(pid):
        try:
            k = _k32()
            h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return False
            k.CloseHandle(h)
            return True
        except Exception:
            return False

else:
    def process_chain(start_pid=None, max_depth=12):
        return [[os.getpid(), "python"]]

    def process_name(pid):
        return "python"

    def pid_alive(pid):
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False


#: 真正的"主人"进程名。实测 hook 的祖先链里，Claude 本体是 claude.exe，
#: 中间夹着两三层每次调用临时拉起的 bash.exe（秒生秒灭），
#: 所以判定存活必须跳过 shell，直接认这几个名字。
OWNER_CANDIDATES = frozenset({"claude.exe", "claude", "node.exe", "bun.exe"})


def find_owner(chain):
    """从进程链里挑出真正的主人，返回 (pid, name)。

    找不到候选时退回链尾（VSCode 主进程）并如实返回它的名字，
    让调用方知道这个 PID 只能证明"宿主还开着"，不能证明会话还活着。
    """
    if not chain:
        return None, None
    for pid, name in chain:
        if (name or "").lower() in OWNER_CANDIDATES:
            return pid, name
    return chain[-1][0], chain[-1][1]
