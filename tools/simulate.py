#!/usr/bin/env python3
"""模拟器：不启动 Claude Code 也能驱动信号灯。

两用：
  * 验证事件→灯态的映射与聚合是否和预期一致；
  * 调 UI 时手动摆出任意灯态，不必真去等 Claude 干活。

事件不会绕过映射表——它是把合成载荷喂给真正的 `hook_writer.handle()`，
所以这里跑出来的结果就是真实运行时会出现的结果。

用法：
    python tools/simulate.py playback         ★ 按真实节奏回放典型使用场景
    python tools/simulate.py playback fast    不打间隔，只做断言自检
    python tools/simulate.py map              打印事件映射表自检
    python tools/simulate.py demo             依次走一遍四态
    python tools/simulate.py multi            造三个并发会话，验证优先级聚合
    python tools/simulate.py red|yellow|green|dark    手动摆出单个灯态
    python tools/simulate.py clear            清掉所有模拟会话
"""

import json
import pathlib
import sys
import time

# Windows 控制台默认按本地代码页解释，中文会乱码；强制 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import core          # noqa: E402
import hook_writer   # noqa: E402
import aggregator    # noqa: E402

SIM_PREFIX = "SIM-"
DEMO_CWD = str(ROOT)

#: 另一个真实存在的项目目录，用来验证"点红灯跳到那个 VSCode 窗口"。
#: 找不到就退回本仓库，保证回放不会因为路径不存在而失败。
ALT_CWD = str(ROOT.parent / "vibecoding-signal-light")
if not pathlib.Path(ALT_CWD).exists():
    ALT_CWD = DEMO_CWD

ICON = {core.RED: "红", core.YELLOW: "黄", core.GREEN: "绿", core.DARK: "暗"}


# ---------------------------------------------------------------- 基础

def feed(name, event, cwd=None, **extra):
    """把一个合成事件喂给真正的 hook 处理器。"""
    payload = {
        "hook_event_name": event,
        "session_id": f"{SIM_PREFIX}{name}",
        "cwd": cwd or DEMO_CWD,
        "transcript_path": "",
        **extra,
    }
    hook_writer.handle(payload)
    return read_file(name)


def read_file(name):
    path = core.SESSIONS_DIR / f"{SIM_PREFIX}{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_state(name):
    d = read_file(name)
    return d["state"] if d else "(无)"


def current(sim_only=False):
    """当前聚合灯态。

    sim_only=True 时只统计模拟会话 —— 测试必须只断言自己造出来的东西，
    否则你真实开着的 Claude 会话会让回放结果忽明忽暗，测试就废了。
    """
    state, sessions = aggregator.Aggregator().current()
    if not sim_only:
        return state, sessions
    sessions = [s for s in sessions if s.session_id.startswith(SIM_PREFIX)]
    return (sessions[0].state if sessions else core.DARK), sessions


def clear():
    n = 0
    # 连 .tmp 一起扫：原子写被打断时可能留下孤儿临时文件
    for f in core.SESSIONS_DIR.glob(f"{SIM_PREFIX}*"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    print(f"已清掉 {n} 个模拟文件")


# ---------------------------------------------------------------- 回放

def build_steps():
    """典型使用场景。每步是 (动作说明, 期望文案, 期望聚合值, 动作)。

    第二段是刻意设计的边界情况，也是最容易做错的地方：
      * 绿挡黄 —— 未确认的绿会把另一个会话的黄压住
      * 红抢绿 —— 等待态永远优先
      * 红解除后，那盏没确认的绿会重新露出来

    第三段是"被丢下的会话卡住红灯"：忽略它之后，被它盖住的绿要重新露出来；
    而且它不是永久静音——那个会话真又有动静时会自己回来。
    """
    A_CWD, B_CWD = DEMO_CWD, ALT_CWD
    A = pathlib.Path(A_CWD).name
    B = pathlib.Path(B_CWD).name

    return [
        # ---- 第一段：单会话正常流程 ----
        (f"会话「{A}」启动", "暗", core.DARK,
         lambda: feed("alpha", "SessionStart", cwd=A_CWD)),

        (f"你给「{A}」派活", "黄", core.YELLOW,
         lambda: feed("alpha", "UserPromptSubmit", cwd=A_CWD, prompt="帮我重构这个模块")),

        ("连续调用工具…", "黄", core.YELLOW,
         lambda: feed("alpha", "PreToolUse", cwd=A_CWD, tool_name="Bash")),

        ("工具返回…", "黄", core.YELLOW,
         lambda: feed("alpha", "PostToolUse", cwd=A_CWD, tool_name="Bash")),

        ("起了个子代理帮忙", "黄", core.YELLOW,
         lambda: feed("alpha", "SubagentStart", cwd=A_CWD)),

        ("子代理干完", "黄（不是绿！子代理结束≠回合结束）", core.YELLOW,
         lambda: (feed("alpha", "SubagentStop", cwd=A_CWD),
                  feed("alpha", "PostToolUse", cwd=A_CWD, tool_name="Task"))),

        ("★ Claude 反问了你一个问题 —— 它在等你", "红（闪烁）", core.RED,
         lambda: feed("alpha", "PreToolUse", cwd=A_CWD, tool_name="AskUserQuestion")),

        ("你回答了", "黄", core.YELLOW,
         lambda: feed("alpha", "PostToolUse", cwd=A_CWD, tool_name="AskUserQuestion")),

        ("★ 弹出授权框 —— 又停下来等你", "红（闪烁）", core.RED,
         lambda: feed("alpha", "Notification", cwd=A_CWD,
                      notification_type="permission_prompt",
                      message="Claude needs your permission to use Bash")),

        ("你批准了，继续干", "黄", core.YELLOW,
         lambda: feed("alpha", "PostToolUse", cwd=A_CWD, tool_name="Bash")),

        ("★ 这一轮干完了", "绿（常亮，且一直亮着）", core.GREEN,
         lambda: feed("alpha", "Stop", cwd=A_CWD)),

        ("左等右等，绿灯就是不灭 —— 直到你点它", "绿（保持）", core.GREEN,
         lambda: None),

        # ---- 第二段：多项目并发与优先级 ----
        ("─" * 15 + f" 第二个项目「{B}」介入 " + "─" * 15, "绿（A 的绿还没确认）", core.GREEN,
         lambda: feed("beta", "SessionStart", cwd=B_CWD)),

        (f"★「{B}」开始干活，但「{A}」的绿灯还占着", "绿（绿 > 黄，黄被挡住）", core.GREEN,
         lambda: feed("beta", "UserPromptSubmit", cwd=B_CWD, prompt="改个 bug")),

        (f"「{B}」也干完了", "绿", core.GREEN,
         lambda: feed("beta", "Stop", cwd=B_CWD)),

        (f"★「{A}」又派了新活", "绿（仍是未确认的绿在占位）", core.GREEN,
         lambda: feed("alpha", "UserPromptSubmit", cwd=A_CWD, prompt="再来一轮")),

        (f"★「{A}」卡在授权上 —— 红把绿抢掉", "红（闪烁）", core.RED,
         lambda: feed("alpha", "Notification", cwd=A_CWD,
                      notification_type="permission_prompt",
                      message="Claude needs your permission to use Write")),

        ("你批准了 —— 红灯解除", "绿（未确认的绿又露出来了）", core.GREEN,
         lambda: feed("alpha", "PostToolUse", cwd=A_CWD, tool_name="Write")),

        (f"「{A}」也收工", "绿", core.GREEN,
         lambda: feed("alpha", "Stop", cwd=A_CWD)),

        (f"你关掉了「{B}」的窗口", "绿（A 还没确认）", core.GREEN,
         lambda: feed("beta", "SessionEnd", cwd=B_CWD)),

        # ---- 第三段：被丢下的会话卡住红灯，右键忽略它 ----
        (f"★「{B}」又来了并卡在授权上 —— 红灯把「{A}」待验收的绿整个盖住",
         "红（闪烁）", core.RED,
         lambda: feed("beta", "Notification", cwd=B_CWD,
                      notification_type="permission_prompt",
                      message="Claude needs your permission to use Bash")),

        (f"★你右键「忽略会话」→「{B}」——被丢下的红灯消失，绿重新露出来",
         "绿", core.GREEN,
         lambda: ignore("beta")),

        (f"被忽略的「{B}」不会被噪声事件吵回来", "绿（B 没有复活）", core.GREEN,
         lambda: feed("beta", "Notification", cwd=B_CWD,
                      notification_type="idle_prompt", message="空闲提醒")),

        ("你点了灯，确认完毕", "暗", core.DARK,
         lambda: ack_all()),

        (f"★「{B}」真的又有动静 —— 被忽略的会话自己回来了",
         "黄", core.YELLOW,
         lambda: feed("beta", "UserPromptSubmit", cwd=B_CWD, prompt="继续")),

        ("清理模拟会话，把灯交还给真实状态", "—", None,
         lambda: clear()),
    ]


def playback(fast=False, delay_scale=1.0):
    clear()
    print("=" * 74)
    if fast:
        print("回放自检（不打间隔）")
    else:
        print("典型使用场景回放 —— 请对照屏幕上灯的实际显示")
    print("=" * 74)

    mismatches = 0
    for desc, expect_text, expect_state, action in build_steps():
        print(f"\n  {desc}")
        print(f"    期望: {expect_text}")
        if action:
            action()
        state, sessions = current(sim_only=True)
        detail = "，".join(f"{s.project}={ICON.get(s.state, '?')}" for s in sessions)
        print(f"    实际: {ICON.get(state, '?')}   [{detail or '无会话'}]")

        if expect_state is not None and state != expect_state:
            mismatches += 1
            print(f"    ** 不一致：聚合得到 {state}，期望 {expect_state}")

        if not fast:
            time.sleep(3.0 * delay_scale)

    print("\n" + "=" * 74)
    if mismatches:
        print(f"回放结束：{mismatches} 处与预期不符")
    else:
        print("回放结束：全部与预期一致")
    print("=" * 74)
    return mismatches


def ack_all():
    """模拟"你点了灯"——把绿态会话确认掉。"""
    _, sessions = current()
    for s in sessions:
        if s.state == core.GREEN and s.session_id.startswith(SIM_PREFIX):
            aggregator.Aggregator.acknowledge(s, by="simulate")


def ignore(name):
    """模拟"你右键 → 忽略会话"——删掉指定模拟会话的状态文件。

    会话号用**精确匹配**，不是前缀匹配：SIM-a 和 SIM-ab 是两个不同的会话，
    拿 startswith 去删会把无辜的那个一起带走，而"只影响这一个"正是本功能的全部承诺。
    """
    target = f"{SIM_PREFIX}{name}"
    _, sessions = current()
    for s in sessions:
        if s.session_id == target:
            return aggregator.Aggregator.ignore(s, by="simulate")
    return False


# ---------------------------------------------------------------- 其余命令

def map_check():
    cases = [
        ("SessionStart", {}, core.DARK),
        ("UserPromptSubmit", {}, core.YELLOW),
        ("PreToolUse", {"tool_name": "Bash"}, core.YELLOW),
        ("PreToolUse", {"tool_name": "AskUserQuestion"}, core.RED),
        ("PreToolUse", {"tool_name": "ExitPlanMode"}, core.RED),
        ("PostToolUse", {"tool_name": "AskUserQuestion"}, core.YELLOW),
        ("PostToolUseFailure", {}, core.YELLOW),
        ("SubagentStop", {}, core.YELLOW),
        ("PermissionRequest", {}, core.RED),
        ("Notification", {"notification_type": "permission_prompt"}, core.RED),
        ("Notification", {"notification_type": "idle_prompt"}, None),
        ("Stop", {}, core.GREEN),
        ("SessionEnd", {}, None),
        ("某个未知事件", {}, None),
    ]
    # 「原因」放最后一列：它是中文，列宽在终端里对不齐，放末尾就不用对齐了
    print(f"{'事件':<22}{'关键字段':<42}{'灯态':<10}{'结果':<7}原因")
    print("-" * 100)
    all_ok = True
    for event, payload, want in cases:
        got = core.map_event(event, payload)
        good = got == want
        all_ok = all_ok and good
        key = ", ".join(f"{k}={v}" for k, v in payload.items())[:40]
        print(f"{event:<22}{key:<42}{str(got):<10}"
              f"{'OK' if good else 'FAIL':<7}"
              f"{core.reason_text(event, payload) or '—'}")
    print()
    print("映射表:", "全部符合预期" if all_ok else "有偏差")


def demo():
    steps = [
        ("开始新会话", "SessionStart", {}),
        ("用户派活", "UserPromptSubmit", {"prompt": "帮我改个 bug"}),
        ("调用工具", "PreToolUse", {"tool_name": "Bash"}),
        ("工具跑完", "PostToolUse", {"tool_name": "Bash"}),
        ("Claude 反问", "PreToolUse", {"tool_name": "AskUserQuestion"}),
        ("你已回答", "PostToolUse", {"tool_name": "AskUserQuestion"}),
        ("授权弹窗", "Notification",
         {"notification_type": "permission_prompt",
          "message": "Claude needs your permission to use Bash"}),
        ("回合结束", "Stop", {}),
        ("会话结束", "SessionEnd", {}),
    ]
    expect = {
        "SessionStart": core.DARK, "UserPromptSubmit": core.YELLOW,
        "Stop": core.GREEN, "SessionEnd": "(无)",
    }
    print(f"{'动作':<14}{'事件':<18}{'得到':<10}{'期望':<10}结果")
    print("-" * 62)
    ok = True
    for label, event, extra in steps:
        feed("demo", event, **extra)
        got = read_state("demo")
        want = expect.get(event, got)
        good = got == want
        ok = ok and good
        print(f"{label:<14}{event:<18}{got:<10}{want:<10}{'OK' if good else 'FAIL'}")
    print()
    print("事件映射自检:", "全部通过" if ok else "有失败项")
    clear()


def multi():
    clear()
    print("造三个并发会话：")
    feed("proj-a", "UserPromptSubmit", prompt="干活")
    time.sleep(0.01)
    feed("proj-b", "Stop")
    time.sleep(0.01)
    feed("proj-c", "PreToolUse", tool_name="AskUserQuestion")
    for n in ("proj-a", "proj-b", "proj-c"):
        print(f"  {n:<10} -> {read_state(n)}")
    state, _ = current(sim_only=True)
    print(f"\n聚合结果: {state} —— 应为红（红 > 绿 > 黄）")


def make(name, event, **extra):
    feed(name, event, **extra)
    print(f"  {name:<10} -> {read_state(name)}")
    state, _ = current()
    print(f"  聚合     -> {state}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "playback"
    core.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    if cmd == "playback":
        fast = len(sys.argv) > 2 and sys.argv[2] == "fast"
        return playback(fast=fast)
    elif cmd == "map":
        map_check()
    elif cmd == "demo":
        demo()
    elif cmd == "clear":
        clear()
    elif cmd == "multi":
        multi()
    elif cmd == "red":
        make("manual", "PreToolUse", tool_name="AskUserQuestion")
    elif cmd == "redat":
        # 造一个指向指定项目的红灯，用来验证"点红灯跳到那个窗口"。
        # 用法: python tools/simulate.py redat "d:\path\to\project"
        if len(sys.argv) < 3:
            print('用法: python tools/simulate.py redat "<项目目录>"')
            return 2
        target = sys.argv[2]
        if not pathlib.Path(target).exists():
            print(f"目录不存在: {target}")
            return 2
        feed("manual", "PreToolUse", cwd=target, tool_name="AskUserQuestion")
        d = read_file("manual")
        print(f"  项目 : {d.get('project')}")
        print(f"  cwd  : {d.get('cwd')}")
        print(f"  状态 : {d.get('state')}（红灯闪烁，点灯珠可跳到该项目窗口）")
        return 0
    elif cmd == "yellow":
        make("manual", "UserPromptSubmit", prompt="干活")
    elif cmd == "green":
        make("manual", "Stop")
    elif cmd == "dark":
        make("manual", "SessionStart")
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
