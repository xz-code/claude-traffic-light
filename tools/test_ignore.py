#!/usr/bin/env python3
"""右键「忽略会话」的回归测试。

背景：红灯（等待你回复/授权）唯一的自动出口是"同一个会话的下一个事件"。
被丢下的会话（VSCode 窗口开着、claude.exe 还活着）永远不会有下一个事件，
僵尸清理也够不着它——`_is_zombie` 对认得出的主人只信 PID 存活，
那个 6 小时的兜底根本走不到。于是红灯永远闪，还因为红的绝对优先级
把别的会话的绿/黄一起盖住（"告警被藏起来"，和伪点击那次是同一类伤害）。

修法是人肉出口：右键 → 忽略会话 → 删掉那**一个**会话的状态文件。

这个测试要锁死三件事，每一件写错都会静默伤人：

1. **只影响那一个会话**。删错了就是"告警被静默"，而你不会知道曾经有过告警。
   所以除了断言目标消失，还要断言其他文件**逐字节没变**。
2. **不是永久静音**。被忽略的会话真又有动静时必须自己回来，否则你会真的
   错过下一次授权请求。
3. **噪声不能让它复活**。不改变灯态的事件不该把文件吵回来——这正是
   `hook_writer.handle` 里那个 `if not path.exists(): return` 在挡的东西。

走的是**真的** `hook_writer.handle()`（经由 simulate 的 feed），
所以复活语义是被实测的，不是被重写的。

跑法：  .venv\\Scripts\\python.exe tools\\test_ignore.py
"""

import os
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import core        # noqa: E402
import aggregator  # noqa: E402
import simulate    # noqa: E402

SIM_PREFIX = simulate.SIM_PREFIX
Aggregator = aggregator.Aggregator


def file_bytes(name):
    """会话文件的原始字节；不存在返回 None（用来断言"没被动过"）。"""
    p = core.SESSIONS_DIR / f"{SIM_PREFIX}{name}.json"
    try:
        return p.read_bytes()
    except OSError:
        return None


def session_of(name):
    """当前聚合里那个模拟会话的 Session 快照；没有返回 None。"""
    target = f"{SIM_PREFIX}{name}"
    _, sessions = simulate.current()
    for s in sessions:
        if s.session_id == target:
            return s
    return None


def lamp():
    """只统计模拟会话的聚合灯态 —— 真实会话不该影响这里的断言。"""
    state, sessions = simulate.current(sim_only=True)
    return state, list(sessions)


def log_tail(since):
    """debug.log 从 since 字节往后新增的内容。"""
    try:
        return core.DEBUG_LOG.read_bytes()[since:].decode("utf-8", errors="replace")
    except OSError:
        return ""


def gui_checks(check):
    """第二段：右键菜单的**接线**（不是逻辑）。

    单独成段有两个原因：需要 Qt（起不来时不该把上面那些更要紧的断言一起拖下水），
    以及这段验的是"点哪个条目对应哪个会话"这类接线错误——它们不会让上面的逻辑
    测试变红，却会让用户点 A 结果 B 被删掉。
    """
    from PySide6.QtWidgets import QApplication, QMenu

    from widget import Config, TrafficLight

    class FakeSession:
        """_add_ignore_menu 只读这几个字段（不碰 path）。"""

        def __init__(self, sid, project, state, title=""):
            self.session_id = sid
            self.project = project
            self.state = state
            self.title = title

    _app = QApplication.instance() or QApplication(sys.argv)
    light = TrafficLight(Config())
    for t in (light._blink, light._poll, light._topmost):
        t.stop()

    # ---- 子菜单内容：排除暗态、保持优先级顺序、标出点灯的那一个 ----
    light.sessions = [
        FakeSession("s-gamma", "lkw", core.RED),
        FakeSession("s-beta", "qms_backend", core.GREEN),
        FakeSession("s-delta", "sleepy", core.DARK),
    ]
    m = QMenu()
    sub = light._add_ignore_menu(m)
    texts = [a.text() for a in sub.actions()] if sub else []
    check("子菜单排除暗态会话，顺序 = 优先级顺序",
          texts == ["● lkw · #s-gamma（等待你的回复）",
                    "qms_backend · #s-beta（完成，待验收）"],
          f"texts={texts}")

    # ---- `&` 必须转义，否则被 Qt 当助记符吃掉 ----
    m2 = QMenu()
    light.sessions = [FakeSession("s-amp", "R&D", core.GREEN)]
    sub2 = light._add_ignore_menu(m2)
    amp = [a.text() for a in sub2.actions()] if sub2 else []
    check("项目名里的 & 被转义成 &&",
          amp == ["● R&&D · #s-amp（完成，待验收）"], f"texts={amp}")

    # ---- 同名消歧：这是个会删状态文件的菜单，两条目绝不能长得一样 ----
    m3 = QMenu()
    light.sessions = [
        FakeSession("s-one", "lkw", core.RED, title="优化悬浮提示"),
        FakeSession("s-two", "lkw", core.GREEN, title="改登录页"),
    ]
    sub3 = light._add_ignore_menu(m3)
    labels = [a.text() for a in sub3.actions()] if sub3 else []
    check("同项目两个会话的条目必须不同（选错就是一次告警被抹掉）",
          len(labels) == 2 and labels[0] != labels[1]
          and "优化悬浮提示" in labels[0] and "改登录页" in labels[1],
          f"labels={labels}")

    # ---- 标题是用户自己打的字，一样可能带 & ----
    m4 = QMenu()
    light.sessions = [FakeSession("s-t", "lkw", core.GREEN, title="R&D 修复")]
    sub4 = light._add_ignore_menu(m4)
    amp2 = [a.text() for a in sub4.actions()] if sub4 else []
    check("标题里的 & 也要转义",
          amp2 == ["● lkw · R&&D 修复（完成，待验收）"], f"texts={amp2}")

    # ---- 没有可忽略的会话时：条目和分隔线都不该加 ----
    m3 = QMenu()
    light.sessions = [FakeSession("s-delta", "sleepy", core.DARK)]
    ret3 = light._add_ignore_menu(m3)
    check("全是暗态时什么都不加（含分隔线）",
          ret3 is None and m3.actions() == [], f"ret={ret3} n={len(m3.actions())}")

    m4 = QMenu()
    light.sessions = []
    check("一个会话都没有时也不炸",
          light._add_ignore_menu(m4) is None and m4.actions() == [],
          "应静默返回")

    # ---- 触发接线：抽**中间**那个条目。
    # 这里刻意用三个会话。只有两个的话，"第二个"和"最后一个"是同一个，
    # 闭包晚绑定那个 bug 恰好会蒙对（每个条目都忽略最后一个 = 抽第二个时正确），
    # 断言就形同虚设——这是变异测试跑出来的教训。
    light.sessions = [
        FakeSession("s-gamma", "lkw", core.RED),
        FakeSession("s-beta", "qms_backend", core.GREEN),
        FakeSession("s-alpha", "another", core.YELLOW),
    ]
    real_refresh = light.refresh         # 留一份真的，最后那段端到端要还回去
    recorded = []
    light.agg.ignore = lambda s, by="unknown": recorded.append((s.session_id, by))
    light.refresh = lambda: recorded.append(("refresh", None))

    m5 = QMenu()
    sub5 = light._add_ignore_menu(m5)
    check("前置：三个非暗态会话 → 三个条目",
          len(sub5.actions()) == 3, f"n={len(sub5.actions())}")

    sub5.actions()[1].trigger()
    check("抽中间那个条目 → 忽略的必须是它，不是最后一个（抓闭包晚绑定）",
          [r for r in recorded if r[0] != "refresh"] == [("s-beta", "menu")],
          f"recorded={recorded}")

    recorded.clear()
    sub5.actions()[0].trigger()
    check("触发后立刻 refresh（不等下一轮 250ms 轮询）",
          ("refresh", None) in recorded
          and [r for r in recorded if r[0] != "refresh"] == [("s-gamma", "menu")],
          f"recorded={recorded}")

    # ---- 端到端：拆掉所有桩，让真实的 Aggregator 去删真实文件 ----
    # 上面那些只验了"接线"，没验"闭包里捕获的 Session 到底指向磁盘上哪个文件"。
    # 索引指错人同样会造成"点了 A、B 被删"，而那是本功能最不能出的错。
    del light.agg.ignore                 # 去掉实例属性，露出真正的 Aggregator.ignore
    light.refresh = real_refresh
    simulate.clear()
    simulate.feed("alpha", "PreToolUse", tool_name="AskUserQuestion")   # 红
    simulate.feed("beta", "Stop")                                       # 绿
    light.refresh()

    targets6 = [s for s in light.sessions if s.state != core.DARK]
    red_idx = [i for i, s in enumerate(targets6)
               if s.session_id == f"{SIM_PREFIX}alpha"]
    m6 = QMenu()
    sub6 = light._add_ignore_menu(m6)
    if sub6 and red_idx:
        sub6.actions()[red_idx[0]].trigger()
    check("端到端：点红灯那条 → 真的只删掉它自己的文件",
          file_bytes("alpha") is None and file_bytes("beta") is not None,
          f"alpha={file_bytes('alpha') is None} beta={file_bytes('beta') is not None}")
    simulate.clear()


def main():
    results = []

    def check(name, passed, note=""):
        results.append((name, bool(passed), note))

    simulate.clear()

    # ---------------------------------------------------------- 1. 场景搭建
    #   alpha=黄  beta=绿  gamma=红   →   聚合应为红
    simulate.feed("alpha", "UserPromptSubmit", prompt="干活")
    simulate.feed("beta", "Stop")
    simulate.feed("gamma", "PreToolUse", tool_name="AskUserQuestion")

    state, sessions = lamp()
    check("三个会话：黄/绿/红 → 聚合红",
          state == core.RED and len(sessions) == 3,
          f"state={state} n={len(sessions)}")

    # ------------------------------------------------- 2. 只影响那一个会话
    before_alpha = file_bytes("alpha")
    before_gamma = file_bytes("gamma")

    ok_ret = simulate.ignore("beta")
    state, sessions = lamp()
    ids = sorted(s.session_id for s in sessions)

    check("忽略非灯首的 beta → 它的文件消失",
          file_bytes("beta") is None, "SIM-beta.json 应不存在")
    check("其他会话的文件逐字节没变",
          file_bytes("alpha") == before_alpha
          and file_bytes("gamma") == before_gamma,
          "alpha / gamma 不该被碰")
    check("聚合仍是红，但会话数恰好少 1",
          state == core.RED and len(sessions) == 2,
          f"state={state} ids={ids}")

    # ------------------------------------------------------------ 3. 幂等
    check("ignore() 首次返回 True", ok_ret is True, f"got={ok_ret}")

    # 文件已不在，scan() 不会再给出这个会话，所以手工造一个指向已删路径的快照
    ghost = aggregator.Session({}, core.SESSIONS_DIR / f"{SIM_PREFIX}beta.json")
    try:
        again, raised = Aggregator.ignore(ghost, by="test"), None
    except Exception as exc:            # noqa: BLE001 - 这里就是要抓任何异常
        again, raised = None, exc
    check("再忽略一次不抛异常且返回 False",
          raised is None and again is False, f"got={again} exc={raised}")

    # ------------------------------------------- 4. 会话号是精确匹配，不是前缀
    simulate.feed("a", "UserPromptSubmit", prompt="干活")
    simulate.feed("ab", "UserPromptSubmit", prompt="干活")
    simulate.ignore("a")
    check("忽略 SIM-a 不会带走 SIM-ab",
          file_bytes("a") is None and file_bytes("ab") is not None,
          "SIM-ab.json 应仍在")
    simulate.ignore("ab")

    # ------------------------------- 5. 忽略灯首的红灯 —— 本功能要解决的场景
    simulate.feed("beta", "Stop")        # 把 beta 弄回来（绿，未被确认）
    state, sessions = lamp()
    check("前置：红压着绿",
          state == core.RED and len(sessions) == 3,
          f"state={state} n={len(sessions)}")

    simulate.ignore("gamma")
    state, sessions = lamp()
    check("忽略灯首的红灯 → 被盖住的绿立刻露出来",
          state == core.GREEN and len(sessions) == 2,
          f"state={state} n={len(sessions)}")

    # ------------------------------------------------ 6. 依次清空 → 绿→黄→暗
    simulate.ignore("beta")
    state_mid, _ = lamp()
    simulate.ignore("alpha")
    state_end, sessions_end = lamp()
    check("再清掉绿 → 黄", state_mid == core.YELLOW, f"state={state_mid}")
    check("清掉最后一个 → 暗", state_end == core.DARK and not sessions_end,
          f"state={state_end} n={len(sessions_end)}")

    # --------------------------------------------- 7. 噪声事件不会让它复活
    simulate.feed("alpha", "Notification",
                  notification_type="idle_prompt", message="空闲提醒")
    noise1 = file_bytes("alpha")
    simulate.feed("alpha", "某个未知事件")
    noise2 = file_bytes("alpha")
    state, _ = lamp()
    check("idle_prompt 不会复活被忽略的会话", noise1 is None, "不该重建文件")
    check("未知事件也不会复活它", noise2 is None, "不该重建文件")
    check("噪声之后灯仍是暗的", state == core.DARK, f"state={state}")

    # --------------------------------------- 8. 真有动静时必须自己回来
    simulate.feed("alpha", "UserPromptSubmit", prompt="继续")
    revived = simulate.read_state("alpha")
    state, _ = lamp()
    check("UserPromptSubmit 让它复活成黄",
          revived == core.YELLOW and state == core.YELLOW,
          f"file={revived} state={state}")

    simulate.feed("alpha", "PreToolUse", tool_name="AskUserQuestion")
    check("复活后照样能亮红灯（复活是完整的）",
          simulate.read_state("alpha") == core.RED, "不该只复活一半")

    # ------------------------------------------------------------ 9. 审计行
    simulate.clear()
    simulate.feed("alpha", "PreToolUse", tool_name="AskUserQuestion")
    target = session_of("alpha")

    try:
        size_before = core.DEBUG_LOG.stat().st_size
    except OSError:
        size_before = 0

    old = os.environ.get("AI_TRAFFIC_LIGHT_DEBUG")
    os.environ["AI_TRAFFIC_LIGHT_DEBUG"] = "1"
    try:
        Aggregator.ignore(target, by="test")
        tail = log_tail(size_before)
    finally:
        if old is None:
            os.environ.pop("AI_TRAFFIC_LIGHT_DEBUG", None)
        else:
            os.environ["AI_TRAFFIC_LIGHT_DEBUG"] = old

    check("debug.log 里留下了可审计的一行",
          "IGNORE" in tail and f"{SIM_PREFIX}alpha" in tail and "by=test" in tail,
          "应含 IGNORE SIM-alpha... by=test")

    # ------------------------------------------------------ 第二段：Qt 菜单
    try:
        gui_checks(check)
    except ImportError as exc:
        # 起不来 Qt 就不算失败，明写 SKIP，别让它把上面那些更要紧的断言盖掉
        results.append(("Qt 菜单接线段", None, f"跳过：{exc}"))

    # ---------------------------------------------------------------- 汇总
    print(f"{'场景':<44}{'结果':<8}说明")
    print("-" * 76)
    ok = True
    for name, passed, note in results:
        if passed is None:
            print(f"{name:<44}{'SKIP':<8}{note}")
            continue
        ok = ok and passed
        print(f"{name:<44}{'OK' if passed else 'FAIL':<8}{note}")
    print()
    print("忽略会话（逻辑段）:", "全部符合预期" if ok else "有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        simulate.clear()
    sys.exit(code)
