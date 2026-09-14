#!/usr/bin/env python3
"""把项目打成便携版（onedir 文件夹）。

产物：
    dist/AiTrafficLight-portable/
        AiTrafficLight.exe         GUI 主程序
        _internal/                 PyInstaller 依赖（Qt 等）
        hook/
            python/                便携 Python（官方 embeddable 包）
            hook_writer.py         hook 本体
            core.py
        start.bat / stop.bat
        说明.txt

为什么 hook 要单独带一份便携 Python：
    目标机器不保证装了 Python，而 hook 必须能跑。hook 又不能编译成 exe——
    它挂在每一次工具调用上，PyInstaller 的解包开销会让每次调用多花几百毫秒。
    所以走"裸 .py + 最小解释器"这条路。

用法：
    .venv\\Scripts\\pip install pyinstaller
    .venv\\Scripts\\python.exe build.py
    .venv\\Scripts\\python.exe build.py --skip-gui   只组装，不重跑 PyInstaller
"""

import argparse
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
BUILD = ROOT / "build_cache"
DIST = ROOT / "dist"
OUT = DIST / "AiTrafficLight-portable"
APP_NAME = "AiTrafficLight"

PY_VER = platform.python_version()          # 如 3.14.2

#: 按顺序尝试。官方站点在国内经常只有几十 KB/s，
#: 实测 5 分钟只下来 400KB —— 所以国内镜像排前面。
EMBED_MIRRORS = [
    "https://registry.npmmirror.com/-/binary/python/{v}/python-{v}-embed-amd64.zip",
    "https://mirrors.huaweicloud.com/python/{v}/python-{v}-embed-amd64.zip",
    "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip",
]

#: 单个镜像的下载时限（秒）。超了就换下一个，不再傻等。
MIRROR_DEADLINE = 90


def log(msg):
    print(f"[build] {msg}", flush=True)


# ---------------------------------------------------------------- 便携 Python

def _download(url, dest, deadline=MIRROR_DEADLINE):
    """带整体时限的下载。成功返回 True，失败返回 False 并清掉半截文件。"""
    import time
    t0, got, total = time.time(), 0, 0
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "ai-traffic-light-build"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            with open(dest, "wb") as fh:
                while True:
                    if time.time() - t0 > deadline:
                        raise TimeoutError(f"超过 {deadline}s 仍未下完")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    got += len(chunk)
        if total and got < total:
            raise OSError(f"只收到 {got}/{total} 字节")
        return True
    except Exception as exc:
        speed = got / 1024 / max(0.01, time.time() - t0)
        log(f"  失败（{got / 1024:.0f}KB, {speed:.0f}KB/s）：{exc}")
        try:
            dest.unlink()
        except OSError:
            pass
        return False


def ensure_embeddable():
    """下载并解压 Python embeddable 包，返回解压后的目录。会缓存。"""
    BUILD.mkdir(parents=True, exist_ok=True)
    target = BUILD / f"python-{PY_VER}-embed-amd64"
    if (target / "python.exe").exists():
        log(f"便携 Python 已缓存：{target}")
        return target

    archive = BUILD / f"python-{PY_VER}-embed-amd64.zip"
    if not archive.exists():
        for template in EMBED_MIRRORS:
            url = template.format(v=PY_VER)
            host = url.split("/")[2]
            log(f"尝试镜像 {host}")
            if _download(url, archive):
                log(f"  成功（{archive.stat().st_size / 1024 / 1024:.1f} MB）")
                break
        else:
            raise SystemExit(
                "\n所有镜像都下载失败了。\n"
                "可以手动下载 python-{v}-embed-amd64.zip 放到：\n"
                f"  {archive}\n"
                "然后重跑本脚本。".format(v=PY_VER)
            )

    log(f"解压到 {target}")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)
    return target


def check_ctypes(py_dir):
    """embeddable 包不一定带 _ctypes.pyd，而 core.py 依赖它。

    这一点必须查——缺了的话 hook 会整个 import 失败，表现为"灯永远不变色"，
    而且没有任何显式报错，极难排查。
    """
    py = py_dir / "python.exe"
    proc = subprocess.run(
        [str(py), "-c", "import ctypes, json, pathlib; print('OK')"],
        capture_output=True,
    )
    out = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()
    if proc.returncode != 0 or "OK" not in out:
        raise SystemExit(
            f"\n随包的 Python 无法 import ctypes —— hook 会静默失效。\n{out}"
        )
    log("便携 Python 自检通过（ctypes / json / pathlib 可用）")


# ---------------------------------------------------------------- GUI

def build_gui():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit(
            "没装 PyInstaller。先运行：\n  .venv\\Scripts\\pip install pyinstaller"
        )

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--windowed", "--onedir",
        "--name", APP_NAME,
        "--paths", str(SRC),
        # main.py 是在 sys.path 注入之后才 import 这些模块的，
        # PyInstaller 的静态分析看不到，必须显式声明，否则打出来的包一启动就崩。
        "--hidden-import", "widget",
        "--hidden-import", "aggregator",
        "--hidden-import", "core",
        "--hidden-import", "hook_install",
        "--hidden-import", "tooltip",
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "pyinstaller"),
        "--specpath", str(BUILD),
    ]
    icon = BUILD / "app.ico"
    if icon.exists():
        args += ["--icon", str(icon)]
    args.append(str(SRC / "main.py"))

    log("PyInstaller 打包 GUI …")
    proc = subprocess.run(args, cwd=str(ROOT))
    if proc.returncode != 0:
        raise SystemExit("PyInstaller 失败，看上面的输出")
    return DIST / APP_NAME


# ---------------------------------------------------------------- 组装

def assemble(gui_dir, py_dir):
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    log(f"复制 GUI -> {OUT}")
    for item in gui_dir.iterdir():
        dest = OUT / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    hook_dir = OUT / "hook"
    (hook_dir / "python").mkdir(parents=True, exist_ok=True)

    log("复制 hook 脚本")
    for name in ("hook_writer.py", "core.py"):
        shutil.copy2(SRC / name, hook_dir / name)

    log("复制便携 Python")
    for item in py_dir.iterdir():
        dest = hook_dir / "python" / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    # 注意：不能直接把仓库根目录的 start.bat / stop.bat 复制过来。
    # 那两个是**开发版**，引用 .venv\Scripts\pythonw.exe 和 tools\stop.py，
    # 便携文件夹里这两样都不存在——用户双击只会得到"请先安装依赖"，
    # 而依赖其实已经随包带上了。必须生成便携版专用的。
    (OUT / "start.bat").write_text(PORTABLE_START_BAT, encoding="ascii")
    (OUT / "stop.bat").write_text(PORTABLE_STOP_BAT, encoding="ascii")

    (OUT / "说明.txt").write_text(README_TXT, encoding="utf-8")
    log(f"组装完成 -> {OUT}")


#: 便携版专用启动器。ASCII-only：.bat 里的非 ASCII 文本在默认 OEM 代码页下会乱码。
PORTABLE_START_BAT = """@echo off
rem  AI Traffic Light - portable launcher
rem  Its dependencies travel with it, so there is nothing to install.

cd /d "%~dp0"

if not exist "AiTrafficLight.exe" goto missing
start "" "AiTrafficLight.exe"
exit /b 0

:missing
echo.
echo   AiTrafficLight.exe not found next to this script.
echo   Keep this .bat in the same folder as the .exe.
echo.
pause
exit /b 1
"""

#: 停止靠启动时写下的 pid 文件，不靠匹配命令行——
#: 匹配命令行既可能漏掉，也可能把执行匹配的 shell 自己杀掉。
PORTABLE_STOP_BAT = """@echo off
rem  AI Traffic Light - stop the running instance

setlocal
set PIDFILE=%LOCALAPPDATA%\\ai-traffic-light\\light.pid

if not exist "%PIDFILE%" goto notrunning

set /p PID=<"%PIDFILE%"
if "%PID%"=="" goto notrunning

taskkill /PID %PID% /F >nul 2>&1
if errorlevel 1 (
  echo   Process %PID% was not running.
) else (
  echo   Stopped PID %PID%.
)
del "%PIDFILE%" >nul 2>&1
goto done

:notrunning
echo   Not running.

:done
ping -n 3 127.0.0.1 >nul
endlocal
"""

README_TXT = """AI 状态灯 —— 便携版
============================================================

怎么用
------------------------------------------------------------
1. 双击 AiTrafficLight.exe，屏幕上会出现一盏红绿灯。
2. 右键系统托盘里的灯图标 ▸ 「安装 hook 到 Claude Code」。
3. 正常使用 Claude Code，灯会跟着变：

     红（闪） = Claude 在等你回复 / 授权
     黄       = 正在干活
     绿       = 干完了，点一下灯珠才算确认
     暗       = 空闲

不需要在这台机器上装 Python。安装 hook 时，
程序会把 hook 运行时复制到：

    %LOCALAPPDATA%\\ai-traffic-light\\hook\\

所以这个文件夹之后可以随便挪、随便删，灯照常工作。

操作
------------------------------------------------------------
  拖动灯          移动位置（自动记住）
  点亮的灯珠      红灯→跳到等待你的窗口；绿灯→确认
  鼠标悬停        看是哪个会话、哪条指令、在等什么
  右键灯          更多菜单
  托盘图标        安装/卸载 hook、显示隐藏、退出

常见问题
------------------------------------------------------------
* 灯一直是暗的
    多半是 hook 没装。托盘右键 ▸ 安装 hook。
    装完要新开一个 Claude Code 会话才会生效。

* 点红灯没跳到窗口
    需要那台机器装了 VSCode，且 `code` 在 PATH 里
    （装 VSCode 时勾选「添加到 PATH」）。

* 悬停时提示里没有会话名，或者原因是英文
    多半是 hook 运行时还是旧的 —— 它是**复制**到
    %LOCALAPPDATA%\\ai-traffic-light\\hook\\ 的一份副本，换版本不会自动更新。
    托盘右键 ▸ 卸载 hook，再 ▸ 安装 hook 即可。

* 想看日志
    托盘右键 ▸ 打开状态目录。里面 sessions\\ 是各会话状态，
    debug.log 需要设 AI_TRAFFIC_LIGHT_DEBUG=1 启动才会有。

* 想彻底卸载
    托盘右键 ▸ 卸载 hook（会一并清掉复制过去的文件），
    然后删掉这个文件夹和 %LOCALAPPDATA%\\ai-traffic-light\\ 即可。
"""


# ---------------------------------------------------------------- 冒烟测试

def smoke_test():
    """用随包的 Python 真跑一次 hook，确认它确实能落盘。

    这一步不能省：embeddable 包和系统 Python 有差异（._pth 限制 sys.path、
    可能缺扩展模块），而这些差异的失败方式全都是"静默不工作"。
    """
    import json
    import os
    import time

    py = OUT / "hook" / "python" / "python.exe"
    hook = OUT / "hook" / "hook_writer.py"
    if not py.exists() or not hook.exists():
        raise SystemExit(f"冒烟测试失败：找不到 {py} 或 {hook}")

    sessions = Path(os.environ["LOCALAPPDATA"]) / "ai-traffic-light" / "sessions"
    probe = sessions / "SMOKE-test.json"
    if probe.exists():
        probe.unlink()

    payload = json.dumps({
        "hook_event_name": "PreToolUse",
        "session_id": "SMOKE-test",
        "cwd": str(OUT),
        "tool_name": "Bash",
    }).encode("utf-8")

    log("冒烟测试：用随包 Python 跑一次 hook")
    proc = subprocess.run([str(py), str(hook)], input=payload,
                          capture_output=True, timeout=60)
    out = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()

    for _ in range(20):
        if probe.exists():
            break
        time.sleep(0.1)

    if not probe.exists():
        raise SystemExit(
            "冒烟测试失败：hook 没写出状态文件。\n"
            f"退出码 {proc.returncode}\n输出：{out or '(空)'}"
        )

    data = json.loads(probe.read_text(encoding="utf-8"))
    probe.unlink()
    log(f"冒烟测试通过：state={data.get('state')} "
        f"owner={data.get('owner_name')}({data.get('owner_pid')})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-gui", action="store_true",
                    help="跳过 PyInstaller，直接复用上次的 dist（调试组装流程用）")
    args = ap.parse_args()

    py_dir = ensure_embeddable()
    check_ctypes(py_dir)

    gui_dir = DIST / APP_NAME
    if not args.skip_gui:
        gui_dir = build_gui()
    elif not gui_dir.exists():
        raise SystemExit(f"--skip-gui 但找不到 {gui_dir}，先完整跑一次")

    assemble(gui_dir, py_dir)
    smoke_test()

    # 清理必须在冒烟测试**之后**：测试会用随包的 Python 跑一次 hook，
    # 那次运行才会 import core 并生成 __pycache__。
    # 放前面清理等于白清——这个顺序错过一次。
    shutil.rmtree(OUT / "hook" / "__pycache__", ignore_errors=True)

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    log(f"完成。产物：{OUT}")
    log(f"总大小：{size / 1024 / 1024:.1f} MB")
    log("拷到别的机器：整个文件夹复制过去，双击 exe，托盘菜单装 hook。")


if __name__ == "__main__":
    main()
