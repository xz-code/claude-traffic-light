#!/usr/bin/env python3
"""AI 状态灯 —— 安装器（开发模式）。

    python install.py install           把 hook 装进 ~/.claude/settings.json
    python install.py uninstall         移除本项目的 hook（保留你自己的其他 hook）
    python install.py status            看当前装了什么
    python install.py dry-run           只打印将要写入的内容，不动文件
    python install.py autostart-on      开机自动启动信号灯
    python install.py autostart-off     关掉开机自启
    python install.py autostart-status  查开机自启状态

写入前一定会备份成 settings.json.bak-<时间戳>。

注意：这个脚本是给**开发模式**用的——hook 直接指向本仓库的 src\，
靠系统 Python 运行。打包后的便携版不走这里：它会把 hook 复制到
%LOCALAPPDATA%\\ai-traffic-light\\hook\\ 并用自己的嵌入式 Python，
由 GUI 托盘菜单完成，全程不需要系统装 Python。

hook 的读写逻辑在 src/hook_install.py，GUI 和本脚本共用同一份。
"""

import json
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import hook_install  # noqa: E402


def find_python():
    """挑一个用于跑 hook 的解释器。

    hook 只依赖标准库，所以优先用系统 Python 而不是项目 venv——
    venv 被删掉时 hook 不该跟着失效。
    """
    for name in ("python", "python3", "py"):
        p = shutil.which(name)
        if p:
            return p
    return sys.executable


def cmd_install(dry=False):
    src = ROOT / "src"
    py = find_python()

    print(f"解释器 : {py}")
    print(f"脚本   : {src / 'hook_writer.py'}")
    print(f"事件   : {len(hook_install.EVENTS)} 个")

    if dry:
        cmd = hook_install.build_command(src, py)
        preview = {
            "hooks": {
                event: [{
                    **({"matcher": m} if m is not None else {}),
                    "hooks": [{"type": "command", "command": cmd,
                               "async": True, "timeout": 5}],
                }]
                for event, m in hook_install.EVENTS.items()
            }
        }
        print("\n--- dry-run，未写入 ---")
        print("将合并进 settings.json 的片段：")
        print(json.dumps(preview, indent=2, ensure_ascii=False))
        return 0

    result = hook_install.install(hook_dir=src, python_exe=py)
    print(result["message"])
    if result.get("backup"):
        print(f"已备份 : {result['backup']}")
    if result["ok"]:
        print(f"已写入 : {hook_install.settings_path()}")
    return 0 if result["ok"] else 1


def cmd_uninstall():
    result = hook_install.uninstall()
    print(result["message"])
    if result.get("backup"):
        print(f"已备份 : {result['backup']}")
    return 0 if result["ok"] else 1


def cmd_status():
    st = hook_install.status()
    if not st["ok"]:
        print(st["message"])
        return 1
    print(f"配置文件 : {st['settings']}")
    print(f"hook 总数: {st['total']}（其中本项目的 {st['installed']} 个）")
    print(f"解释器   : {find_python()}")
    print(f"脚本存在 : {(ROOT / 'src' / 'hook_writer.py').exists()}")
    if st["command"]:
        print(f"  命令   : {st['command']}")
    return 0


# ---------------------------------------------------------------- 开机自启

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "AiTrafficLight"


def venv_pythonw():
    """优先用 pythonw.exe 启动 GUI —— 它是无控制台的，开机时不会弹黑框。"""
    p = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if p.exists():
        return p
    p = ROOT / ".venv" / "Scripts" / "python.exe"
    return p if p.exists() else None


def cmd_autostart(action):
    try:
        import winreg
    except ImportError:
        print("开机自启只在 Windows 上支持")
        return 1

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_READ | winreg.KEY_WRITE) as key:
        if action == "status":
            try:
                value, _ = winreg.QueryValueEx(key, RUN_NAME)
                print("开机自启 : 已开启")
                print(f"命令     : {value}")
            except FileNotFoundError:
                print("开机自启 : 未开启")
            return 0

        if action == "off":
            try:
                winreg.DeleteValue(key, RUN_NAME)
                print("已关闭开机自启")
            except FileNotFoundError:
                print("本来就没开")
            return 0

        py = venv_pythonw()
        if not py:
            print("找不到 .venv 里的解释器，先装依赖："
                  "\n  python -m venv .venv && "
                  ".venv\\Scripts\\pip install -r requirements.txt")
            return 1
        cmd = f'"{py}" "{ROOT / "src" / "main.py"}"'
        winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, cmd)
        print("已开启开机自启")
        print(f"命令     : {cmd}")
        return 0


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    table = {
        "install": cmd_install,
        "dry-run": lambda: cmd_install(dry=True),
        "uninstall": cmd_uninstall,
        "status": cmd_status,
        "autostart-on": lambda: cmd_autostart("on"),
        "autostart-off": lambda: cmd_autostart("off"),
        "autostart-status": lambda: cmd_autostart("status"),
    }
    fn = table.get(action)
    if not fn:
        print(__doc__)
        return 0
    return fn() or 0


if __name__ == "__main__":
    sys.exit(main())
