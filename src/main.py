"""AI 状态灯入口。

运行：  .venv\\Scripts\\python.exe src\\main.py
"""

import ctypes
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

_MUTEX = None  # 必须持有引用，否则句柄被回收，互斥体就失效了

PID_FILE = None  # 启动后写入，供 stop 脚本精确停止（见文件末尾）


def write_pid_file():
    """把自己的 pid 和父 pid 记下来，让停止脚本不必靠猜命令行。

    venv 的 pythonw.exe 是个转发器，会再起一个真正跑脚本的子进程，
    所以两个 pid 都要记；只杀一个会留下孤儿。
    """
    global PID_FILE
    try:
        import core
        PID_FILE = core.STATE_DIR / "light.pid"
        core.STATE_DIR.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(
            f"{os.getpid()}\n{os.getppid()}\n", encoding="utf-8"
        )
    except Exception:
        PID_FILE = None


def clear_pid_file():
    try:
        if PID_FILE is not None:
            PID_FILE.unlink()
    except Exception:
        pass


def acquire_single_instance():
    """用 Windows 命名互斥体保证只开一盏灯。

    没有这层保护，重复启动会得到多盏叠在一起的灯——看起来就像灯坏了，
    而且每个实例都在抢置顶，画面会很混乱。
    返回 True 表示本次是唯一实例。
    """
    global _MUTEX
    if sys.platform != "win32":
        return True
    try:
        ERROR_ALREADY_EXISTS = 183
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        k32.CreateMutexW.restype = ctypes.c_void_p
        _MUTEX = k32.CreateMutexW(None, False, "Local\\AiTrafficLightSingleton")
        return ctypes.get_last_error() != ERROR_ALREADY_EXISTS
    except Exception:
        return True  # 拿不准就放行，别让保护逻辑本身把程序挡死


if __name__ == "__main__":
    if not acquire_single_instance():
        print("信号灯已在运行，本次启动忽略。")
        sys.exit(0)

    write_pid_file()
    try:
        from widget import run  # noqa: E402

        code = run()
    finally:
        clear_pid_file()
    sys.exit(code)
