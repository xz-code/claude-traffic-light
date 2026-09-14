"""hook 的安装 / 卸载 / 状态检查。

CLI（install.py）和 GUI 托盘菜单**共用这一份逻辑**。
分成两份实现的话，两边迟早会写出不一样的 settings.json，
而这类不一致极难发现——灯会莫名其妙地不亮，却看不出哪里错了。
"""

import json
import os
import pathlib
import shutil
import sys
import time

#: 与 core.py 的事件映射表配套；改动这里记得同步那边。
EVENTS = {
    "SessionStart": None,
    "SessionEnd": None,
    "UserPromptSubmit": None,
    "Stop": None,
    "SubagentStop": "*",
    "SubagentStart": "*",
    "PreToolUse": "*",
    "PostToolUse": "*",
    "PostToolUseFailure": "*",
    "PreCompact": "*",
    "Notification": "*",
    "PermissionRequest": "*",
    "PermissionDenied": "*",
}

#: 靠它从既有配置里认出哪些条目是本项目的。
#: 卸载是精确匹配——只删我们的，绝不碰用户自己挂的其他 hook。
MARKER = "hook_writer.py"

#: 安装到固定位置时，需要跟着搬走的文件。
HOOK_FILES = ("hook_writer.py", "core.py")

#: 随包携带的便携 Python 放在这个子目录名里。
PYTHON_DIRNAME = "python"


def settings_path():
    return pathlib.Path(os.environ.get("USERPROFILE") or pathlib.Path.home()) \
        / ".claude" / "settings.json"


def load_settings():
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise RuntimeError(f"读不了 {settings_path()}：{exc}") from exc


def backup_settings():
    """写入前一定要备份。返回备份路径，失败返回 None。"""
    path = settings_path()
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak-{stamp}")
    try:
        dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return dest
    except Exception:
        return None


def strip_ours(hooks):
    """摘掉本项目条目，保留别人的。返回 (新 hooks, 摘掉几个)。"""
    removed = 0
    cleaned = {}
    for event, groups in hooks.items():
        kept = []
        for group in groups if isinstance(groups, list) else []:
            handlers = group.get("hooks") or []
            ours = [h for h in handlers if MARKER in str(h.get("command", ""))]
            theirs = [h for h in handlers if h not in ours]
            removed += len(ours)
            if theirs:
                new_group = dict(group)
                new_group["hooks"] = theirs
                kept.append(new_group)
        if kept:
            cleaned[event] = kept
    return cleaned, removed


def build_command(hook_dir, python_exe):
    return f'"{python_exe}" "{pathlib.Path(hook_dir) / "hook_writer.py"}"'


def copy_hook_files(source_dir, target_dir):
    """把 hook 脚本复制到固定位置。返回复制过去的文件数。

    为什么要复制：settings.json 里写的是绝对路径。如果指向便携文件夹，
    那个文件夹一旦被移动或删除，hook 就瞎了——而且灯会静默停在暗态，
    你根本不会知道原因。复制到 %LOCALAPPDATA% 之后，
    便携文件夹随便挪、随便删，hook 照常工作。
    """
    source_dir = pathlib.Path(source_dir)
    target_dir = pathlib.Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in HOOK_FILES:
        src = source_dir / name
        if src.exists():
            shutil.copy2(src, target_dir / name)
            n += 1
    return n


def copy_python_runtime(source_dir, target_dir):
    """把随包的便携 Python 也复制到固定位置。返回 True 表示复制了。

    这一步不能省。只复制脚本、让 python 仍然指向便携文件夹的话，
    "文件夹可以随便挪" 这个性质就没了——hook 会依赖那个文件夹还在原地，
    而这正是我们想避免的失效方式。
    """
    src = pathlib.Path(source_dir) / PYTHON_DIRNAME
    if not src.is_dir():
        return False
    shutil.copytree(src, pathlib.Path(target_dir) / PYTHON_DIRNAME,
                    dirs_exist_ok=True)
    return True


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def install(hook_dir, python_exe=None, source_dir=None):
    """把 hook 装进 settings.json。

    hook_dir    —— settings.json 里要指向的目录（最终生效的位置）
    python_exe  —— 跑 hook 的解释器；source_dir 里带了便携 Python 时可以省略
    source_dir  —— 若给出，先把 hook 脚本（和便携 Python）复制到 hook_dir

    返回一个 dict：{"ok", "message", "command", "backup", "copied"}
    """
    hook_dir = pathlib.Path(hook_dir)

    if source_dir is not None:
        copied = copy_hook_files(source_dir, hook_dir)
        if copied == 0:
            return {"ok": False,
                    "message": f"没找到要复制的 hook 文件（源目录 {source_dir}）"}
        # 解释器也要搬过去。只搬脚本的话，python 仍然指向便携文件夹，
        # "文件夹可以随便挪" 就落空了。
        if copy_python_runtime(source_dir, hook_dir):
            python_exe = hook_dir / PYTHON_DIRNAME / "python.exe"
    else:
        copied = 0
        if not (hook_dir / "hook_writer.py").exists():
            return {"ok": False,
                    "message": f"{hook_dir / 'hook_writer.py'} 不存在"}

    if python_exe is None:
        return {"ok": False, "message": "没有可用的解释器"}
    python_exe = pathlib.Path(python_exe)
    if not python_exe.exists():
        return {"ok": False, "message": f"解释器不存在：{python_exe}"}

    cmd = build_command(hook_dir, python_exe)

    try:
        data = load_settings()
    except RuntimeError as exc:
        return {"ok": False, "message": str(exc)}

    backup = backup_settings()
    hooks, _ = strip_ours(data.get("hooks") or {})   # 先摘旧的，避免重复叠加

    for event, matcher in EVENTS.items():
        group = {"hooks": [{"type": "command", "command": cmd,
                            "async": True, "timeout": 5}]}
        if matcher is not None:
            group["matcher"] = matcher
        hooks.setdefault(event, []).append(group)

    data["hooks"] = hooks
    try:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "message": f"写 settings.json 失败：{exc}"}

    return {"ok": True, "copied": copied, "backup": backup, "command": cmd,
            "message": f"已安装 {len(EVENTS)} 个 hook 事件\n{cmd}"}


def uninstall():
    """移除本项目 hook，保留用户自己的其他 hook。"""
    try:
        data = load_settings()
    except RuntimeError as exc:
        return {"ok": False, "message": str(exc)}

    hooks, removed = strip_ours(data.get("hooks") or {})
    if removed == 0:
        return {"ok": True, "removed": 0, "message": "没找到本项目的 hook，无需卸载"}

    backup = backup_settings()
    data["hooks"] = hooks
    if not hooks:
        data.pop("hooks", None)
    try:
        settings_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "message": f"写 settings.json 失败：{exc}"}

    return {"ok": True, "removed": removed, "backup": backup,
            "message": f"已移除 {removed} 个 hook 条目"}


def status():
    """返回当前安装状态，供 GUI/CLI 显示。"""
    try:
        data = load_settings()
    except RuntimeError as exc:
        return {"ok": False, "message": str(exc)}

    hooks = data.get("hooks") or {}
    commands = [
        h.get("command", "")
        for groups in hooks.values() for g in groups
        for h in (g.get("hooks") or [])
        if MARKER in str(h.get("command", ""))
    ]
    total = sum(len(g.get("hooks") or []) for groups in hooks.values() for g in groups)
    return {
        "ok": True,
        "installed": len(commands),
        "total": total,
        "command": commands[0] if commands else None,
        "settings": str(settings_path()),
    }


def installed_paths():
    """从已装的 hook 命令里解析出 (hook_dir, python_exe)。

    用来判断"现在装的这一份"和"我打算装的那一份"是不是同一个，
    以及卸载时要不要连固定位置的文件一起清掉。
    """
    st = status()
    if not st.get("ok") or not st.get("command"):
        return None, None
    cmd = st["command"]
    parts = [p for p in cmd.split('"') if p.strip()]
    if len(parts) >= 2:
        try:
            return pathlib.Path(parts[1]).parent, pathlib.Path(parts[0])
        except Exception:
            pass
    return None, None


def purge_installed_files():
    """删掉复制到固定位置的 hook 运行时（脚本 + 便携 Python）。

    只在确认那确实是本程序管理的目录时才动手——绝不递归删一个来路不明的路径。
    """
    hook_dir, _ = installed_paths()
    if hook_dir is None:
        return False
    try:
        import core
        managed = core.STATE_DIR / "hook"
    except Exception:
        return False
    try:
        if hook_dir.resolve() != managed.resolve():
            return False       # 不是我们管的目录（比如开发模式的 src\），不碰
        shutil.rmtree(managed)
        return True
    except Exception:
        return False


def expected_hook_dir():
    """本程序安装 hook 时**应该**指向的目录。

    用来判断"现在装的那一份"和"我这一份"是不是同一个。
    比如开发模式跑着、却想切换到便携版的运行时，就需要重装而不是卸载。
    """
    import core
    if is_frozen():
        return core.STATE_DIR / "hook"
    return pathlib.Path(__file__).resolve().parent


def is_ours_current():
    """当前装的 hook 是不是本程序这一份。返回 (是否已装, 是否本程序)。"""
    st = status()
    if not st.get("ok") or not st.get("installed"):
        return False, False
    cur, _ = installed_paths()
    if cur is None:
        return True, False
    try:
        return True, cur.resolve() == expected_hook_dir().resolve()
    except Exception:
        return True, False


def install_for_this_app():
    """按当前运行方式选安装策略 —— GUI 托盘菜单调这个。

    打包版：把便携文件夹里的 hook 脚本 + 便携 Python 复制到
            %LOCALAPPDATA%\\ai-traffic-light\\hook\\，并指向复制后的位置。
            之后便携文件夹随便挪、随便删，都不影响 hook。
    开发版：直接用仓库里的 src\\ + 系统 Python，不复制任何东西。
    """
    import core

    if is_frozen():
        root = pathlib.Path(sys.executable).parent
        source = root / "hook"
        if not (source / "hook_writer.py").exists():
            return {"ok": False,
                    "message": f"这个包不完整：找不到 {source / 'hook_writer.py'}"}
        return install(hook_dir=core.STATE_DIR / "hook", source_dir=source)

    src = pathlib.Path(__file__).resolve().parent
    py = shutil.which("python") or shutil.which("python3") or sys.executable
    return install(hook_dir=src, python_exe=py)
