#!/usr/bin/env python3
"""AI Traffic Light — hook 探针。

纯观察者，唯一职责：把收到的每一个 Claude Code hook 事件原样落盘。

三条铁律：
  1. 绝不阻塞  —— 由 settings.json 的 async: true 保证；
  2. 绝不崩溃  —— 所有异常吞掉，永远 exit 0；
  3. 绝不输出  —— 不碰 stdout/stderr，避免污染 hook 协议。

落盘格式为 JSONL，每行一条，直观看、好 grep。
"""

import json
import os
import sys
from datetime import datetime, timezone

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe-events.jsonl")


def parent_info():
    """尽力识别是谁拉起了本 hook 进程。

    这条信息直接决定"僵尸会话能否按 PID 清理"：
      - 若 parent 是 claude/node  → PID 方案可行；
      - 若 parent 是 shell        → 需再往上找一层，方案仍可行但更麻烦。
    """
    info = {"ppid": None, "parent_name": None, "parent_exe": None}
    try:
        import ctypes
        from ctypes import wintypes

        info["ppid"] = os.getppid()

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, info["ppid"])
        if handle:
            try:
                size = wintypes.DWORD(32768)
                buf = ctypes.create_unicode_buffer(size.value)
                if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    info["parent_exe"] = buf.value
                    info["parent_name"] = os.path.basename(buf.value)
            finally:
                k32.CloseHandle(handle)
    except Exception as exc:  # 探针本身绝不能因它失败
        info["parent_error"] = repr(exc)
    return info


def build_record():
    raw = ""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:
        pass

    payload = None
    try:
        payload = json.loads(raw) if raw.strip() else None
    except Exception:
        payload = None

    record = {
        "received_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
        "argv": sys.argv[1:],
        "pid": os.getpid(),
        "raw_len": len(raw),
        "json_ok": isinstance(payload, dict),
    }
    record.update(parent_info())

    if isinstance(payload, dict):
        # 把最关键的字段提到顶层，方便直接 grep，不必解析嵌套 JSON
        for key in (
            "hook_event_name",
            "session_id",
            "cwd",
            "tool_name",
            "notification_type",
            "message",
            "permission_mode",
            "stop_hook_active",
            "stop_reason",
            "source",
            "reason",
            "trigger",
        ):
            if key in payload:
                record[key] = payload[key]
        record["payload"] = payload  # 原始载荷全留，字段名以实测为准
    else:
        record["raw"] = raw[:2000]

    return record


def main():
    record = build_record()
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
