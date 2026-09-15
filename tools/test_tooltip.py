#!/usr/bin/env python3
"""悬停提示的回归测试。

锁四件事，每一件写错都有一类具体的坏结果：

1. **标题持久化**——会话标题是"第一条指令胜出"，但 hook_writer 对改变灯态的事件
   是**写一个全新 dict、不读旧文件**。稍不留神，title 会在下一个事件就被抹掉，
   表现是提示里的会话名突然变成 `#a3f2` 那种鬼东西。这里用真的 hook_writer 验。
2. **转义**——标题是用户自己打的字。不转义的话一个 `<` 就能把整个表格吃掉。
3. **中文原因**——别把 Claude Code 的英文原文漏到提示里。
4. **右列对齐 + 只在变化时重弹**——对齐靠数像素量，不靠眼睛；
   防闪烁靠数"弹出提示框"这个动作被触发的次数。

跑法：  .venv\\Scripts\\python.exe tools\\test_tooltip.py
"""

import json
import pathlib
import shutil
import sys
import tempfile

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


def fake(project, title, state, reason="", sid=None, ai_title=""):
    sid = sid or (project[:4].ljust(4, "0") + "0" * 28)
    return aggregator.Session(
        {"session_id": sid, "cwd": "d:/x/" + project, "project": project,
         "state": state, "reason": reason, "title": title,
         "ai_title": ai_title},
        core.SESSIONS_DIR / f"{sid}.json",
    )


def bands(html_text):
    """把 HTML 渲染成**透明底**图后按行扫描，切成"内容带"：[(左x, 右x, 高), ...]。

    分离靠高度：分隔线是一条 1~2px 的横线，文字行有十几像素高，
    在纵向扫描里天然分得开。

    刻意**不走 render_honest**——那个函数会把面板底色调成不透明的，
    整张图连成一整块，什么都量不出来。这里用同一个引擎、同一份 HTML
    直接渲染，只是不画外框。
    """
    from PySide6.QtGui import QImage, QPainter, QTextDocument
    from PySide6.QtWidgets import QApplication

    doc = QTextDocument()
    doc.setDocumentMargin(0)          # QLabel 内部就是 0，不设会多出 4px 白边
    doc.setDefaultFont(QApplication.font())
    doc.setHtml(html_text)

    img = QImage(int(doc.idealWidth()) + 8, int(doc.size().height()) + 8,
                 QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    doc.drawContents(p)
    p.end()

    out, cur = [], None
    for y in range(img.height()):
        xs = [x for x in range(img.width()) if (img.pixel(x, y) >> 24) & 0xFF]
        if not xs:
            if cur:
                out.append(cur)
                cur = None
            continue
        lo, hi = min(xs), max(xs)
        cur = ([min(cur[0], lo), max(cur[1], hi), cur[2], y] if cur
               else [lo, hi, y, y])
    if cur:
        out.append(cur)
    return [(b[0], b[1], b[3] - b[2] + 1) for b in out]


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

    # ---------------------------------------- Claude Code 自己总结的标题
    # `ai_title` 来自 transcript 尾部一条**无官方文档**的 `ai-title` 记录。
    # 下面每条断言都对应用探针（tools/probe_ai_title.py）在本机实测到的一个
    # 具体现象，不是照着想象写的。
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="atl-aititle-"))

    def transcript(name, records, tail_pad=0):
        """造一个假 transcript 并返回路径。

        `tail_pad` 往文件尾塞一段非 JSON 的填充，用来把记录推离文件尾
        ——测窗口边界用。
        """
        path = tmp / f"{name}.jsonl"
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
        path.write_text(body + "\n" + ("x" * tail_pad), encoding="utf-8")
        return str(path)

    def ai(title):
        return {"type": "ai-title", "sessionId": "s", "aiTitle": title}

    check("能从 transcript 尾部读到 aiTitle",
          core.read_ai_title(transcript("basic", [ai("优化悬浮提示")]))
          == "优化悬浮提示", "")

    # 实测：17 个有过该记录的文件里 7 个的标题中途变过，甚至中英文互换
    # （`nodemon not recognized` -> `nodemon 未找到`）。取第一条 = 显示过时名字。
    check("标题变过时取最后一条，不是第一条",
          core.read_ai_title(transcript("multi", [
              ai("nodemon not recognized"), ai("nodemon 未找到")])) == "nodemon 未找到",
          "取第一条会显示一个已经过时的名字")

    # 窗口的牙：标题被推到窗口之外就必须读不到。
    # 谁要是把实现改成"整读文件"，这条立刻挂——而整读正是不能接受的
    #（最大的 transcript 3.8 MB，这个函数每个 hook 事件都跑一次）。
    check("窗口之外的旧标题读不到（证明没有整读文件）",
          core.read_ai_title(transcript("far", [ai("太远的名字")],
                                        tail_pad=core.AI_TITLE_SCAN * 2)) == "",
          "整读会让这条挂")

    check("窗口之内的标题读得到（后面还跟着别的记录也不影响）",
          core.read_ai_title(transcript("near", [
              ai("近处的名字"),
              {"type": "last-prompt", "lastPrompt": "x" * 2000}])) == "近处的名字", "")

    # 窗口是从文件中间切进去的，切出来的第一行必然是半截的。
    # **危险的恰恰是被切在一条 ai-title 记录正中间的那种**：它长得像条记录、
    # 能通过那个廉价的字节预筛，然后在 json.loads 上才炸。必须跳过它继续往下扫，
    # 而不是就此罢手——罢手的话，紧跟着的完整记录就永远读不到了。
    # （拿一段纯填充当半截行是测不出东西的：它过不了预筛，压根走不到解析。）
    half = tmp / "half.jsonl"
    cut = json.dumps(ai("被切掉的那条"), ensure_ascii=False)[:-2]  # 掐掉尾巴凑成半截
    half.write_text(
        "y" * core.AI_TITLE_SCAN + "\n"
        + cut + "\n"
        + json.dumps(ai("完整的那条"), ensure_ascii=False) + "\n",
        encoding="utf-8")
    check("被窗口切断的半截记录不崩，也不挡后面的完整记录",
          core.read_ai_title(str(half)) == "完整的那条", "")

    # 别的记录里恰好出现 "ai-title" 这几个字（比如用户提问里提到它）
    # 不能被误取——这正是实现里先做字节预筛、再验 type 的原因。
    check("别处出现 ai-title 字样不会被误取",
          core.read_ai_title(transcript("decoy", [
              ai("真的名字"),
              {"type": "user", "message": {"content": "什么叫 ai-title？"}}]))
          == "真的名字", "")

    check("空的 aiTitle 不擦掉上一条",
          core.read_ai_title(transcript("blank", [ai("好名字"), ai("")]))
          == "好名字", "空标题该被忽略，否则好名字会被擦成空白")

    check("transcript 不存在时返回空串，不抛异常",
          core.read_ai_title(str(tmp / "根本没有这个文件.jsonl")) == "", "")
    check("空路径 / None 一律返回空串",
          core.read_ai_title("") == "" and core.read_ai_title(None) == "", "")
    check("aiTitle 超长按 title 上限截断",
          len(core.read_ai_title(transcript("long", [ai("字" * 200)])))
          == core.TITLE_MAX, f"应为 {core.TITLE_MAX}")
    check("aiTitle 里的换行会被折叠",
          core.read_ai_title(transcript("nl", [ai("第一行\n第二行")]))
          == "第一行 第二行", "")

    # ---- 接进 hook：ai_title 与 title 并存，谁也别覆盖谁
    # 覆盖会让一半的会话丢掉名字（实测 36 个 transcript 里 17 个压根没有
    # `ai-title`，子 agent 的全部没有），所以必须是两个独立字段。
    simulate.feed("ai1", "UserPromptSubmit", prompt="我打的第一句话",
                  transcript_path=transcript("hook", [ai("Claude 总结的名字")]))
    d = simulate.read_file("ai1")
    check("hook 把 aiTitle 存进 ai_title",
          d.get("ai_title") == "Claude 总结的名字", f"got={d.get('ai_title')!r}")
    check("ai_title 不覆盖 title，两个字段并存",
          d.get("title") == "我打的第一句话", f"got={d.get('title')!r}")

    simulate.feed("ai1", "PreToolUse", tool_name="Bash",
                  transcript_path=transcript("hook", [ai("后来改过的名字")]))
    d = simulate.read_file("ai1")
    check("标题变了以后下一个事件就跟着更新",
          d.get("ai_title") == "后来改过的名字", f"got={d.get('ai_title')!r}")
    check("更新 ai_title 时 title 仍然纹丝不动",
          d.get("title") == "我打的第一句话", f"got={d.get('title')!r}")

    # 读不到时必须**保留上一次读到的名字**，不能擦成空。
    # 这个场景不是假想的：transcript 正被 Claude Code 持续追加，而本仓库的
    # _write_atomic 已经在为同一类 Windows 文件占用问题重试了——读它同样可能失败。
    simulate.feed("ai1", "PreToolUse", tool_name="Bash", transcript_path="")
    check("某个事件读不到 transcript 时，不会擦掉已读到的 ai_title",
          simulate.read_file("ai1").get("ai_title") == "后来改过的名字",
          f"got={simulate.read_file('ai1').get('ai_title')!r}")

    # 没有 transcript（子 agent、老会话）时必须优雅退化
    simulate.feed("noai", "UserPromptSubmit", prompt="没有 transcript 的会话")
    d = simulate.read_file("noai")
    check("读不到 aiTitle 时 ai_title 为空，title 照常",
          d.get("ai_title") == "" and d.get("title") == "没有 transcript 的会话",
          f"got ai_title={d.get('ai_title')!r} title={d.get('title')!r}")

    # -------------------------------------------------------------- 会话名
    only_title = fake("lkw", "只有第一条指令", core.RED)
    both = fake("lkw", "只有第一条指令", core.RED, ai_title="优化悬浮提示")
    blank_ai = fake("lkw", "只有第一条指令", core.RED, ai_title="   ")
    without = fake("lkw", "", core.RED, sid="a3f2" + "0" * 28)

    check("ai_title 优先于 title（两个都在时）",
          tooltip.session_name(both) == "lkw · 优化悬浮提示",
          f"got={tooltip.session_name(both)!r}")
    check("没有 ai_title 时回退到 title（一半的会话走这条路）",
          tooltip.session_name(only_title) == "lkw · 只有第一条指令",
          f"got={tooltip.session_name(only_title)!r}")
    check("ai_title 只有空白时视为没有，回退 title",
          tooltip.session_name(blank_ai) == "lkw · 只有第一条指令",
          f"got={tooltip.session_name(blank_ai)!r}")
    check("两者都没有才回退到会话号前 8 位",
          tooltip.session_name(without) == "lkw · #a3f20000",
          f"got={tooltip.session_name(without)!r}")

    # 标题上限从 12 放宽到 24 的直接收益：这类中等长度的 AI 标题不再被切断
    check("中等长度的 AI 标题能完整显示",
          tooltip.session_name(fake("lkw", "", core.RED,
                                    ai_title="Windows 桌面 AI 状态指示灯"))
          == "lkw · Windows 桌面 AI 状态指示灯",
          f"got={tooltip.session_name(fake('lkw','',core.RED,ai_title='Windows 桌面 AI 状态指示灯'))!r}")

    check("超长 AI 标题仍按 TITLE_SHOW 截断",
          len(tooltip.session_name(fake("lkw", "", core.RED,
                                        ai_title="字" * 100)).split(" · ")[1])
          == tooltip.TITLE_SHOW, f"应为 {tooltip.TITLE_SHOW}")

    # 从英文词中间切开会在末尾留一个空格（实测 `Claude Code 会话名称规则`
    # 截 12 字得到 `'Claude Code '`）。中文切不出空格，所以只有英文会犯。
    check("英文标题从词中间截断时不留尾随空格",
          tooltip.session_name(fake("lkw", "", core.RED,
                                    ai_title="Check configured Claude Code API"))
          == "lkw · Check configured Claude",
          f"got={tooltip.session_name(fake('lkw','',core.RED,ai_title='Check configured Claude Code API'))!r}")

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

    shutil.rmtree(tmp, ignore_errors=True)


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
    # 定时器也要停，否则它们会在断言之间自己触发
    light._show_timer.stop()
    light._hide_timer.stop()

    # 两个观测点：弹出（pops）与换内容（sets）。
    # 防闪烁要守的是"别重复动它"——内容没变就什么都不该发生。
    pops, sets = [], []
    light._show_tooltip = lambda: pops.append(1)
    light._panel.set_content = lambda html: sets.append(html)

    live = [fake("lkw", "优化悬浮提示", core.RED, "Claude 在向你提问"),
            fake("lkw", "改登录页", core.GREEN)]
    light.agg.current = lambda: (core.RED, live)

    light._tooltip = ""          # 清掉构造时算出来的，保证首轮真的会算
    light.refresh()
    first = light._tooltip
    check("首轮算出了提示内容", bool(first), "内容不该为空")
    check("没在显示时不弹面板（别自己冒出来）",
          pops == [], f"弹了 {len(pops)} 次")

    light.refresh()
    light.refresh()
    check("内容没变时不重复设（防闪烁）",
          sets == [] and pops == [], f"连刷两次设了 {len(sets)} 次")

    light._toggle_blink()
    light._toggle_blink()
    check("闪烁定时器不碰面板",
          sets == [] and pops == [], f"设了 {len(sets)} 次")

    # 显示中才是"刷新内容"那条分支
    light._panel.show()
    changed = [fake("lkw", "优化悬浮提示", core.RED, "计划待你批准"),
               fake("lkw", "改登录页", core.GREEN)]
    light.agg.current = lambda: (core.RED, changed)
    light.refresh()
    check("显示中内容变了要更新（不会陈旧）",
          len(sets) == 1 and sets[0] != first,
          f"设了 {len(sets)} 次，且内容应与首轮不同")

    light._panel.hide()
    light.agg.current = lambda: (core.RED, live)
    light.refresh()
    check("没显示时内容变了也不设（等下次悬停再说）",
          len(sets) == 1, f"又设了 {len(sets) - 1} 次")

    # 真的走了富文本路径吗——Qt 把表格建出来才算数
    doc = QTextDocument()
    doc.setHtml(light._tooltip)
    check("Qt 真的把表格建出来了（没退化成纯文本）",
          "<table" in doc.toHtml(), "")
    check("纯文本回退里也有内容",
          "等待你的回复" in doc.toPlainText(), "")

    # 对齐：数像素，不靠眼睛
    pm = render_tooltip.render_honest(light._tooltip)
    edges = render_tooltip.right_edges(pm)
    distinct = sorted(set(edges.values()))
    check("右列右边缘对齐（每种状态色的最右 x 唯一）",
          len(distinct) <= 1, f"最右 x 取值 {distinct}")

    # 分隔线必须跟着表格宽度自适应。原来用 `━`×28 写死像素宽，标题放宽到
    # 24 字后表格有 289px 而分隔线只有 203px——短 86px，肉眼就是"没对齐"。
    # 这条只能量像素：字符线的宽度在源码里根本看不出来。
    wide = [fake("lkw", "", core.RED, ai_title="Windows 桌面 AI 状态指示灯")]
    b = bands(tooltip.tooltip_html(wide))
    thin = sorted([x for x in b if x[2] <= 3], key=lambda x: -(x[1] - x[0]))
    thick = [x for x in b if x[2] > 3]
    sep_w = thin[0][1] - thin[0][0] if thin else -1
    row_w = max((x[1] - x[0]) for x in thick) if thick else -1
    check("分隔线跟着表格宽度自适应（不是写死的字符数）",
          sep_w >= row_w - 8,
          f"分隔线={sep_w}px 文字行={row_w}px（写死的 ━×28 只有 203px，会挂）")


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
