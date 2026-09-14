#!/usr/bin/env python3
""""点红灯跳窗口"的自动化验证。

思路：把"VSCode 有没有切过去"变成可观测的事实——用 Win32 读前台窗口标题，
执行跳转前后各读一次。这样就不用靠肉眼判断了。

注意：运行时会真的抢走一次前台焦点，这是测试本身的要求。

用法：  python tools\\test_jump.py [目标项目目录]
"""

import ctypes
import pathlib
import shutil
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: 默认跳到一个"别的项目"——取本仓库的兄弟目录，别人克隆下来也能跑。
#: 不存在就退回本仓库，保证测试不会因为路径缺失而失败。
#: 自己的项目路径用命令行第一个参数覆盖。
DEFAULT_TARGET = str(ROOT.parent / "vibecoding-signal-light")
if not pathlib.Path(DEFAULT_TARGET).exists():
    DEFAULT_TARGET = str(ROOT)

_u32 = ctypes.windll.user32
_u32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_u32.GetWindowTextW.restype = ctypes.c_int
_u32.GetForegroundWindow.restype = ctypes.c_void_p
_u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
_u32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

_ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def title_of(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    _u32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def foreground_title():
    hwnd = _u32.GetForegroundWindow()
    return title_of(hwnd) if hwnd else ""


def vscode_windows(needle="visual studio code"):
    """列出所有可见的顶层 VSCode 窗口标题，用于诊断到底切到了哪个。"""
    found = []

    def cb(hwnd, _lparam):
        if not _u32.IsWindowVisible(hwnd):
            return True
        t = title_of(hwnd)
        if t and needle in t.lower():
            found.append(t)
        return True

    _u32.EnumWindows(_ENUMPROC(cb), None)
    return found


def show_windows(label):
    wins = vscode_windows()
    print(f"{label} 可见 VSCode 窗口 {len(wins)} 个:")
    for t in wins:
        print(f"    {t}")
    return wins


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        show_windows("当前")
        print(f"\n当前前台窗口: {foreground_title()}")
        return 0

    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET
    keyword = target.replace("\\", "/").rstrip("/").split("/")[-1]

    print(f"目标项目 : {target}")
    print(f"项目名   : {keyword}")
    code = shutil.which("code")
    print(f"code CLI : {code or '** 找不到 **'}")
    if not code:
        print("\n结论: 失败 —— PATH 里没有 code，跳转必然不生效。")
        return 1

    pre = set(vscode_windows())
    print()
    show_windows("[前]")

    # 目标项目得先真的在某个 VSCode 窗口里开着，"跳到那个窗口"才有意义。
    # 否则 code -r 只会新开一个窗口，测的就不是我们要测的东西了。
    if not any(keyword.lower() in t.lower() for t in pre):
        print(f"\n** 注意：当前没有任何 VSCode 窗口开着「{keyword}」。")
        print("   code -r 会为新目录另开窗口，而不是把已有窗口拉到前台——")
        print("   那样测不到「跳转」这个功能。请先在 VSCode 里打开该项目，再重跑。")
        return 3

    before = foreground_title()
    print(f"\n跳转前前台窗口: {before or '(读不到)'}")

    print(f'\n执行: code -r "{target}"')
    t0 = time.time()
    proc = subprocess.run(
        f'code -r "{target}"',
        shell=True, capture_output=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    print(f"  退出码 {proc.returncode}，耗时 {time.time() - t0:.2f}s")
    if proc.stderr:
        print("  stderr:", proc.stderr.decode("utf-8", "replace")[:200])

    # VSCode 把窗口提到前台、再刷新标题都需要时间，轮询到稳定为止
    after, settled = before, None
    for _ in range(40):                       # 最多 10 秒
        time.sleep(0.25)
        after = foreground_title()
        if after != before:
            break
    for _ in range(20):                       # 再给它 5 秒把标题写全
        if keyword.lower() in after.lower():
            break
        time.sleep(0.25)
        after = foreground_title()

    print(f"跳转后前台窗口: {after or '(读不到)'}")
    print()
    show_windows("[后]")

    changed = after != before
    matched = keyword.lower() in after.lower()
    new_windows = set(vscode_windows()) - pre

    print()
    print(f"  窗口发生变化 : {changed}")
    print(f"  标题含项目名 : {matched}（找 '{keyword}'）")
    print(f"  是否新开窗口 : {bool(new_windows)}"
          + (f" -> {new_windows}" if new_windows else ""))

    if matched:
        print("\n结论: 通过 —— 目标项目的 VSCode 窗口确实被拉到了前台。")
        return 0
    if changed:
        print("\n结论: 存疑 —— 前台窗口变了，但标题里没有项目名。")
        print("     可能是切到了该项目的窗口但标题还没刷新，"
              "也可能切错了窗口。请对照上面的窗口列表人工确认。")
        return 2
    print("\n结论: 失败 —— 前台窗口没变。")
    print("     排查：目标项目在 VSCode 里开着吗？多窗口时 -r 是否指向了正确工作区？")
    return 1


if __name__ == "__main__":
    sys.exit(main())
