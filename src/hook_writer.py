#!/usr/bin/env python3
"""hook 端入口 —— 把 Claude Code 事件落成会话状态文件。

挂在 ~/.claude/settings.json 的 hooks 里，配 `async: true`。
用系统 Python 运行即可（只依赖标准库，不需要 venv）。

三条铁律（与探针一致）：
  1. 绝不阻塞 —— 由 settings.json 的 async: true 保证；
  2. 绝不崩溃 —— 所有异常吞掉，永远 exit 0；
  3. 绝不输出 —— 不碰 stdout/stderr，避免污染 hook 协议。

状态文件走"临时文件 + os.replace"原子写，UI 端任意时刻读到的都是完整 JSON。
每个会话一个文件，互不干扰；某个会话写失败不会波及别人。
"""

import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import core  # noqa: E402


def _session_file(session_id):
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(session_id))
    return core.SESSIONS_DIR / f"{safe[:120]}.json"


def _read_existing(path, attempts=5, backoff=0.005):
    """读回已有的状态文件；读不到返回 {}。

    带重试是因为 UI 端的 `Aggregator.acknowledge` 用的是**非原子**的
    `write_text`（见 aggregator.acknowledge），撞上就会读到半截 JSON。
    而这里读失败的代价不只是"少一个字段"——**title 会因此丢掉**，
    表现是这个会话在悬停提示里突然没了名字。所以宁可多试几次。
    """
    for attempt in range(attempts):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            if attempt == attempts - 1:
                return {}
            time.sleep(backoff * (attempt + 1))
    return {}


def _write_atomic(path, data, attempts=20, backoff=0.005):
    """原子写：先写临时文件，再整体替换。

    Windows 上 `os.replace` 在**目标文件正被任何进程打开时**会抛
    PermissionError（哪怕对方只是在读，因为 Python 的 open 不请求
    FILE_SHARE_DELETE）。UI 端每 250ms 轮询一遍，撞上这个窗口是迟早的事，
    所以必须重试——否则表现就是"偶尔灯不更新"，且极难复现。

    重试上限 20 次 × 5ms 递增，最坏约 1 秒；hook 是 async 的，
    这点耗时不阻塞 Claude。
    """
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                break
            time.sleep(backoff * (attempt + 1))
    # 实在替换不了就放弃，并清掉临时文件别让它堆积
    try:
        tmp.unlink()
    except OSError:
        pass
    raise PermissionError(f"无法原子写入 {path}")


def _title_from_prompt(event, payload):
    """会话标题 = hook 看到的**第一条**用户指令。

    只在这一刻产生。合并规则见 handle()：旧的留着就永远不覆盖，
    所以后续任何 prompt 都改不动它。

    **但"第一条 prompt"不等于"第一条指令"**：`UserPromptSubmit` 也会为 IDE
    注入的上下文触发（实测见过 `<ide_opened_file>...`）。那种不是人打的字，
    拿它当标题会让那一行永远卡着一段垃圾，所以要滤掉。
    """
    if event != "UserPromptSubmit":
        return ""
    text = core.clip_text(payload.get("prompt") or "")
    return "" if core.is_injected_prompt(text) else text


def handle(payload):
    event = payload.get("hook_event_name") or ""
    sid = payload.get("session_id") or payload.get("cwd") or "global"
    path = _session_file(sid)

    if event in core.TERMINAL_EVENTS:
        try:
            path.unlink()
        except OSError:
            pass
        return

    # 先读旧的。**必须在所有分支之前读**——原来只在 state is None 时才读，
    # 于是改变灯态的事件会写一个全新 dict，title 被下一个事件整个抹掉。
    existing = _read_existing(path)

    state = core.map_event(event, payload)
    if state is None:
        # 本事件不改变灯态，但若会话已存在仍需刷新心跳与主人信息，
        # 否则中间只夹着"无影响事件"的会话会被僵尸清理误杀。
        if not path.exists():
            return
        state = existing.get("state", core.DARK)
        # 灯态没变就不重算原因：一条无害事件不该把好端端的中文原因擦成空白
        reason = core.reason_text(event, payload) or existing.get("reason", "")
    else:
        reason = core.reason_text(event, payload)

    # 旧标题如果是注入内容（早期版本没滤，真存进去过），当作没有——
    # 这样下一条真指令能把它覆盖掉，坏标题会自愈。
    old_title = existing.get("title") or ""
    if core.is_injected_prompt(old_title):
        old_title = ""

    cwd = payload.get("cwd") or ""
    chain = core.process_chain()
    owner_pid, owner_name = core.find_owner(chain)

    _write_atomic(path, {
        "session_id": sid,
        "cwd": cwd,
        "project": core.project_name(cwd),
        "state": state,
        "event": event,
        "reason": reason,
        # 第一条指令胜出：旧的留着就不覆盖
        "title": old_title or _title_from_prompt(event, payload),
        # Claude Code 自己总结的标题，挂在 transcript 尾部。每个事件都重读一次
        # ——它异步生成、中途还会变，只在某个事件上读会漏掉更新。实测它离文件尾
        # 不超过 32 KB，只读尾部窗口，代价可以忽略。
        #
        # 存成**独立字段**而不是覆盖 title：实测有一半的 transcript 压根没有这条
        # 记录（子 agent 的全部没有），覆盖会让那半边的会话凭空丢掉名字。
        # 谁优先由显示层决定（见 tooltip.session_name）。
        #
        # 末尾的 `or existing.get(...)` 是**保留上一次读到的名字**：读不到时不能
        # 擦成空。这不是理论担忧——transcript 正被 Claude Code 持续追加，而本仓库
        # 的 `_write_atomic` 已经在为同一类 Windows 文件占用问题重试了
        # （os.replace 撞上别人读文件会抛 PermissionError）。读失败就把名字抹掉，
        # 表现是提示框里的会话名莫名其妙地变回空白，且极难复现。
        "ai_title": (core.read_ai_title(payload.get("transcript_path") or "")
                     or existing.get("ai_title") or ""),
        "tool": payload.get("tool_name") or "",
        "transcript": payload.get("transcript_path") or "",
        "updated_at": time.time(),
        "owner_pid": owner_pid,
        "owner_name": owner_name,
        "chain": chain,
    })


def main():
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    payload = json.loads(raw) if raw.strip() else {}
    if isinstance(payload, dict):
        core.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        handle(payload)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # 观察者绝不能影响被观察者
    sys.exit(0)
