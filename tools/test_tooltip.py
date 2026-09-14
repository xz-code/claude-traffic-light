#!/usr/bin/env python3
"""悬停提示的回归测试。

锁四件事，每一件写错都有一类具体的坏结果：

1. **标题持久化**——会话标题是"第一条指令胜出"，但 hook_writer 对改变灯态的事件
   是**写一个全新 dict、不读旧文件**。稍不留神，title 会在下一个事件就被抹掉，
   表现是提示里的会话名突然变成 `#a3f2` 那种鬼东西。这里用真的 hook_writer 验。
2. **转义**——标题是用户自己打的字。不转义的话一个 `<` 就能把整个表格吃掉。
3. **中文原因**——别把 Claude Code 的英文原文漏到提示里。
4. **右列对齐 + 只在变化时 setToolTip**——对齐靠数像素量，不靠眼睛；
   防闪烁靠数 setToolTip 的调用次数。

跑法：  .venv\\Scripts\\python.exe tools\\test_tooltip.py
"""

import json
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import core          # noqa: E402
import aggregator    # noqa: E402
import tooltip       # noqa: E402
import simulate      # noqa: E402


def fake(project, title, state, reason="", sid=None):
    sid = sid or (project[:4].ljust(4, "0") + "0" * 28)
    return aggregator.Session(
        {"session_id": sid, "cwd": "d:/x/" + project, "project": project,
         "state": state, "reason": reason, "title": title},
        core.SESSIONS_DIR / f"{sid}.json",
    )


def logic_checks(check):
    """第一段：纯逻辑，不碰 Qt。"""

    # ---------------------------------------------------------- 标题持久化
    simulate.clear()
    simulate.feed("alpha", "UserPromptSubmit", prompt="帮我重构这个模块")
    check("首次 UserPromptSubmit 写下 title",
          simulate.read_file("alpha").get("title") == "帮我重构这个模块",
          f"got={simulate.read_file('alpha').get('title')!r}")

    simulate.feed("alpha", "PreToolUse", tool_name="Bash")
    simulate.feed("alpha", "PostToolUse", tool_name="Bash")
    check("后续事件不会把 title 抹掉（真 dict 覆盖写的老毛病）",
          simulate.read_file("alpha").get("title") == "帮我重构这个模块",
          "PreToolUse/PostToolUse 之后 title 应该还在")

    simulate.feed("alpha", "UserPromptSubmit", prompt="再来一轮")
    check("第二条指令不覆盖 title（第一条胜出）",
          simulate.read_file("alpha").get("title") == "帮我重构这个模块",
          "后来者不该篡位")

    # ------------------------------------------- IDE 注入的上下文不是"指令"
    # 实测踩到过：`UserPromptSubmit` 也会为 IDE 注入的上下文触发，
    # 于是 `<ide_opened_file>...` 被当成第一条指令，而那行标题会永远卡住。
    check("以标签开头判定为注入",
          core.is_injected_prompt("<ide_opened_file>x") is True, "")
    check("人打的字不算注入",
          core.is_injected_prompt("帮我改个 bug") is False, "")
    check("空串不算注入", core.is_injected_prompt("") is False, "")

    simulate.feed("inj", "UserPromptSubmit",
                  prompt="<ide_opened_file>The user opened the file "
                         "d:\\x\\y.txt in the IDE.</ide_opened_file>")
    check("IDE 注入的 prompt 不产生 title",
          simulate.read_file("inj").get("title") == "",
          f"got={simulate.read_file('inj').get('title')!r}")

    simulate.feed("inj", "PreToolUse", tool_name="Bash")
    simulate.feed("inj", "UserPromptSubmit", prompt="真正的第一条指令")
    check("注入之后的第一条真指令才成为标题",
          simulate.read_file("inj").get("title") == "真正的第一条指令",
          f"got={simulate.read_file('inj').get('title')!r}")

    # 早期版本已经把注入内容存成标题了 —— 下一条真指令要能覆盖它（自愈）
    legacy = core.SESSIONS_DIR / f"{simulate.SIM_PREFIX}legacy.json"
    legacy.write_text(json.dumps({
        "session_id": f"{simulate.SIM_PREFIX}legacy",
        "title": "<ide_opened_file>老会话的坏标题</ide_opened_file>",
    }, ensure_ascii=False), encoding="utf-8")
    simulate.feed("legacy", "UserPromptSubmit", prompt="自愈之后的标题")
    check("已存的坏标题会被真指令覆盖（自愈）",
          simulate.read_file("legacy").get("title") == "自愈之后的标题",
          f"got={simulate.read_file('legacy').get('title')!r}")

    # -------------------------------------------------------------- 原因
    simulate.feed("alpha", "PreToolUse", tool_name="AskUserQuestion")
    reason = simulate.read_file("alpha").get("reason")
    check("PreToolUse(AskUserQuestion) 的原因",
          reason == "Claude 在向你提问", f"got={reason!r}")

    simulate.feed("alpha", "Notification",
                  notification_type="idle_prompt", message="不关你的事")
    check("灯态没变时不会擦掉已有原因",
          simulate.read_file("alpha").get("reason") == reason, "无害事件不该清空原因")

    simulate.feed("alpha", "Notification",
                  notification_type="permission_prompt",
                  message="Claude needs your permission to use Write")
    check("授权提示的原因抠出了工具名且是中文",
          simulate.read_file("alpha").get("reason") == "请求授权：Write",
          f"got={simulate.read_file('alpha').get('reason')!r}")

    simulate.feed("alpha", "Stop")
    check("Stop 换成验收文案",
          simulate.read_file("alpha").get("reason") == "本轮结束，等你验收",
          f"got={simulate.read_file('alpha').get('reason')!r}")

    # -------------------------------------------- reason_text 整张映射表
    cases = [
        ("Notification", {"notification_type": "permission_prompt",
                          "message": "Claude needs your permission to use Bash"},
         "请求授权：Bash"),
        ("Notification", {"notification_type": "permission_prompt",
                          "message": "没有工具名的怪消息"}, "请求授权"),
        ("Notification", {"notification_type": "idle_prompt", "message": "x"}, ""),
        ("PreToolUse", {"tool_name": "AskUserQuestion"}, "Claude 在向你提问"),
        ("PreToolUse", {"tool_name": "ExitPlanMode"}, "计划待你批准"),
        ("PreToolUse", {"tool_name": "Bash"}, ""),
        ("Stop", {}, "本轮结束，等你验收"),
        ("UserPromptSubmit", {"prompt": "x"}, "正在干活"),
        ("某个未知事件", {}, ""),
    ]
    bad = [f"{e}/{p}→{core.reason_text(e, p)!r}" for e, p, want in cases
           if core.reason_text(e, p) != want]
    check("reason_text 整张映射表", not bad, f"偏差：{bad}" if bad else
          f"{len(cases)} 条全对")

    # ---------------------------------------------------------- clip_text
    check("clip_text 折叠换行与多余空白",
          core.clip_text("  第一行\n\n第二行   第三  ") == "第一行 第二行 第三",
          "换行/连续空格都要收成一个")
    check("clip_text 按上限截断",
          len(core.clip_text("字" * 200)) == core.TITLE_MAX,
          f"应为 {core.TITLE_MAX}")
    check("clip_text 容忍 None", core.clip_text(None) == "", "")

    # -------------------------------------------------------------- 会话名
    with_title = fake("lkw", "优化悬浮提示", core.RED)
    without = fake("lkw", "", core.RED, sid="a3f2" + "0" * 28)
    check("有标题就用标题",
          tooltip.session_name(with_title) == "lkw · 优化悬浮提示",
          f"got={tooltip.session_name(with_title)!r}")
    check("没标题回退到会话号前 8 位",
          tooltip.session_name(without) == "lkw · #a3f20000",
          f"got={tooltip.session_name(without)!r}")

    # ---------------------------------------------------------- HTML 生成
    html_empty = tooltip.tooltip_html([])
    check("没有会话时给一句话，不是空框",
          "没有活跃" in html_empty and "<table" not in html_empty, "")

    html_dark = tooltip.tooltip_html([fake("lkw", "x", core.DARK)])
    check("全是暗态时也不列表格", "<table" not in html_dark, "")

    mixed = [fake("lkw", "优化悬浮提示", core.RED, "Claude 在向你提问"),
             fake("lkw", "改登录页", core.GREEN),
             fake("other", "改 bug", core.YELLOW),
             fake("sleepy", "闲着", core.DARK)]
    html = tooltip.tooltip_html(mixed)
    # 会话行 = 带右对齐状态格的那种行；分隔线和底部提示不带，所以不能数 <tr>
    sess_rows = html.count('align="right"')
    check("暗态会话不进提示（4 个会话只列 3 个）",
          sess_rows == 3 and "闲着" not in html,
          f"会话行={sess_rows}，暗态的「闲着」不该出现")
    check("按优先级：红在绿前、绿在黄前",
          html.index("等待你的回复") < html.index("完成，待验收")
          < html.index("正在干活"), "")

    # 转义：标题里塞 HTML
    nasty = [fake("lkw", "<b>粗</b>&引号\"", core.RED)]
    html_nasty = tooltip.tooltip_html(nasty)
    check("标题里的 < > & 被转义，没变成真标签",
          "&lt;b&gt;" in html_nasty and "<b>粗" not in html_nasty,
          "否则一个 < 就能把整个表格吃掉")
    check("转义后仍然是一张合法的表格",
          html_nasty.count("<table") == 1 and html_nasty.count("</table>") == 1, "")

    # 溢出折叠
    many = [fake(f"p{i}", f"第{i}件事", core.YELLOW) for i in range(9)]
    html_many = tooltip.tooltip_html(many)
    check(f"超过 {tooltip.MAX_ROWS} 个会话时折叠",
          "…还有 3 个" in html_many, f"9 个会话，应显示 6 + 还有 3")

    # 原因只给灯首
    check("原因只出现在灯首那一行",
          html.count("Claude 在向你提问") == 1, "")

    # 只有红灯才提示"单击跳转"
    check("红灯的底部提示是跳转",
          "单击跳到等待中的窗口" in html, "")
    check("绿灯的底部提示是确认熄灭",
          "单击确认，绿灯熄灭" in tooltip.tooltip_html(
              [fake("lkw", "x", core.GREEN)]), "")
    check("黄灯没有底部操作提示",
          "单击" not in tooltip.tooltip_html([fake("lkw", "x", core.YELLOW)]), "")


def gui_checks(check):
    """第二段：需要 Qt。构造不出 QApplication 就抛 ImportError 由调用方跳过。"""
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import QApplication

    from widget import Config, TrafficLight
    import render_tooltip

    _app = QApplication.instance() or QApplication(sys.argv)
    light = TrafficLight(Config())
    for t in (light._blink, light._poll, light._topmost):
        t.stop()

    calls = []
    real_set = light.setToolTip
    light.setToolTip = lambda text: (calls.append(text), real_set(text))

    live = [fake("lkw", "优化悬浮提示", core.RED, "Claude 在向你提问"),
            fake("lkw", "改登录页", core.GREEN)]
    light.agg.current = lambda: (core.RED, live)

    light._tooltip = ""          # 清掉构造时设的那一次，保证首轮真的会设
    light.refresh()
    after_first = len(calls)
    check("首轮会设一次提示", after_first == 1, f"calls={after_first}")

    light.refresh()
    light.refresh()
    check("内容没变时不重复 setToolTip（防闪烁）",
          len(calls) == 1, f"连刷三次只该设一次，实际 {len(calls)}")

    light._toggle_blink()
    light._toggle_blink()
    check("闪烁定时器不碰提示框",
          len(calls) == 1, f"calls={len(calls)}")

    changed = [fake("lkw", "优化悬浮提示", core.RED, "计划待你批准"),
               fake("lkw", "改登录页", core.GREEN)]
    light.agg.current = lambda: (core.RED, changed)
    light.refresh()
    check("内容变了要更新（不会陈旧）",
          len(calls) == 2 and calls[0] != calls[1], f"calls={len(calls)}")

    # 真的走了富文本路径吗——Qt 把表格建出来才算数
    doc = QTextDocument()
    doc.setHtml(light.toolTip())
    check("Qt 真的把表格建出来了（没退化成纯文本）",
          "<table" in doc.toHtml(), "")
    check("纯文本回退里也有内容",
          "等待你的回复" in doc.toPlainText(), "")

    # 对齐：数像素，不靠眼睛
    pm = render_tooltip.render_honest(light.toolTip())
    edges = render_tooltip.right_edges(pm)
    distinct = sorted(set(edges.values()))
    check("右列右边缘对齐（每种状态色的最右 x 唯一）",
          len(distinct) <= 1, f"最右 x 取值 {distinct}")


def main():
    results = []

    def check(name, passed, note=""):
        results.append((name, bool(passed), note))

    try:
        logic_checks(check)
    finally:
        simulate.clear()

    try:
        gui_checks(check)
    except ImportError as exc:
        results.append(("Qt 段", None, f"跳过：{exc}"))

    print(f"{'场景':<46}{'结果':<8}说明")
    print("-" * 78)
    ok = True
    for name, passed, note in results:
        if passed is None:
            print(f"{name:<46}{'SKIP':<8}{note}")
            continue
        ok = ok and passed
        print(f"{name:<46}{'OK' if passed else 'FAIL':<8}{note}")
    print()
    print("悬停提示:", "全部符合预期" if ok else "有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
