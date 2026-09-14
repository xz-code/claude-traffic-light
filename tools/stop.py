#!/usr/bin/env python3
"""停掉正在运行的信号灯。

读 %LOCALAPPDATA%\\ai-traffic-light\\light.pid（由 src/main.py 启动时写入），
按 pid 精确停止——不靠匹配命令行去猜。

为什么不用"匹配命令行"：venv 的 pythonw.exe 会以相对路径启动，
进程命令行里根本没有项目名；而匹配用的那些关键字本身又出现在
执行匹配的 powershell/cmd 自己的命令行里，会连带把自己杀掉。
两条路都踩过，所以改成让程序自己留 pid。

用法：  python tools\\stop.py
"""

import os
import pathlib
import signal
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import core  # noqa: E402


def kill(pid, what):
    if not core.pid_alive(pid):
        print(f"  {what} {pid} 已经不在运行")
        return False
    try:
        os.kill(pid, signal.SIGTERM)   # Windows 上等价于 TerminateProcess
        print(f"  已停止{what} {pid}")
        return True
    except Exception as exc:
        print(f"  停止{what} {pid} 失败: {exc}")
        return False


def main():
    pid_file = core.STATE_DIR / "light.pid"
    if not pid_file.exists():
        print("没有 PID 文件 —— 信号灯看起来没在运行。")
        print(f"（找不到 {pid_file}）")
        return 1

    try:
        lines = [ln.strip() for ln in pid_file.read_text(encoding="utf-8").split()]
        pid = int(lines[0])
    except Exception as exc:
        print(f"PID 文件读不了: {exc}")
        print(f"可以直接删掉它：{pid_file}")
        return 1

    ppid = None
    if len(lines) > 1:
        try:
            ppid = int(lines[1])
        except ValueError:
            ppid = None

    print(f"停止信号灯（PID 文件: {pid_file}）")
    kill(pid, "主进程")

    # venv 的 pythonw.exe 是个转发器，会再起一个真正跑脚本的子进程。
    # 父进程只有在确认是 python 系时才对它下手——否则可能误杀用户的终端。
    if ppid:
        name = (core.process_name(ppid) or "").lower()
        if name in ("python.exe", "pythonw.exe"):
            kill(ppid, "启动器")
        else:
            print(f"  父进程 {ppid} 是 {name or '未知'}，不像启动器，跳过")

    try:
        pid_file.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
