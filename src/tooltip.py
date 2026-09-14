"""悬停提示的文案、配色与富文本渲染。

**刻意不 import Qt**：这样离屏预览（tools/render_tooltip.py）和回归测试都能
直接调这里，不需要起 QApplication，也让"提示长什么样"变成一件可以断言的事。

渲染走 Qt 的富文本子集（实测可用：`<table>`、`<font color>`、`<td align=right>`）。
Qt 的 QLabel/QTipLabel 内部就是 QTextDocument，所以这里生成的 HTML 在
QTextDocument 里渲染出来的结果与真机一致——离屏预览才有意义。
"""

import html

import core

# ---------------------------------------------------------------- 外观常量

#: 与灯体外壳同色（widget.HOUSING），提示框才不像个外来的东西
HOUSING_BG = "#1c1c1e"
#: 分隔线 / 边框
SEP = "#3a3a3c"
#: 正文
FG = "#e8e8ea"
#: 弱化文字（原因、底部提示、溢出）
MUTED = "#8e8e93"

#: 四态用色，与灯珠亮色一致（widget.LAMPS）
STATE_COLOR = {
    core.RED: "#ff3b30",
    core.YELLOW: "#ffcc00",
    core.GREEN: "#34c759",
    core.DARK: "#8e8e93",
}

STATE_TEXT = {
    core.RED: "等待你的回复",
    core.YELLOW: "正在干活",
    core.GREEN: "完成，待验收",
    core.DARK: "空闲",
}

#: 名字里项目名 / 标题各自保留多少字
PROJECT_SHOW = 18
TITLE_SHOW = 12

#: 最多列几个会话，多出来的折成一行"…还有 N 个"
MAX_ROWS = 6

#: 分隔线的字符个数，约等于表格宽度（中文破折线大致等宽）
RULE_CHARS = 28

#: 提示框的外观。挂在 QApplication 上，只影响 QToolTip。
#: 实测：这条真的会作用到 QTipLabel 上（采样过底色），不是摆设。
#: 不设 font-size 是有意的——用系统默认字体，和实测时的渲染条件一致。
TOOLTIP_QSS = f"""
QToolTip {{
    background-color: {HOUSING_BG};
    color: {FG};
    border: 1px solid {SEP};
    padding: 6px;
}}
"""


# ---------------------------------------------------------------- 片段

def esc(s):
    """一切用户来源的文字都要过这里。

    标题是用户自己打的字，可以包含 `<`、`&`——不转义就会破坏整个表格
    （轻则排版乱掉，重则后面的行整段消失）。项目名来自目录名，同理。
    """
    return html.escape(str(s or ""))


def _dim(text):
    return f'<font color="{MUTED}">{text}</font>'


def _rule():
    return (f'<tr><td colspan="2">'
            f'<font color="{SEP}">{"━" * RULE_CHARS}</font></td></tr>')


def _hint(state):
    if state == core.RED:
        return "单击跳到等待中的窗口"
    if state == core.GREEN:
        return "单击确认，绿灯熄灭"
    return ""


# ---------------------------------------------------------------- 会话名

def session_name(session):
    """会话的显示名：`项目 · 标题`。

    返回**未转义**的原文——调用方决定怎么转义（HTML 走 esc，
    Qt 菜单要把 `&` 写成 `&&`）。这两种转义规则不同，所以不能在这里替它选。

    没有标题就回退到会话号前 8 位。这不是故障：本次升级之前就开着的会话
    没有 title 字段，那个会话结束、或者它自己再发一次指令，就被补上了。
    """
    project = (session.project or "(未知)")[:PROJECT_SHOW]
    title = (getattr(session, "title", "") or "").strip()
    if not title:
        title = "#" + (session.session_id or "?")[:8]
    else:
        title = title[:TITLE_SHOW]
    return f"{project} · {title}"


# ---------------------------------------------------------------- 渲染

def _session_row(session, bold):
    """一行会话：左边彩色圆点+名字，右边同色状态词，右对齐。

    两列都**不设固定宽度**，交给表格自适应。这是量出来的决定
    （tools/render_tooltip.py --variants）：自适应和固定宽度**一样对齐**
    （各状态色的最右 x 取值唯一），但自适应窄得多——固定宽度会白扔一百多像素
    的空白，看着就"糙"。对齐靠的是 <td align="right">，不是靠宽度。
    """
    color = STATE_COLOR.get(session.state, FG)
    name = esc(session_name(session))
    state = esc(STATE_TEXT.get(session.state, session.state))
    if bold:
        name, state = f"<b>{name}</b>", f"<b>{state}</b>"
    return (
        f'<tr>'
        f'<td><font color="{color}">&#9679;</font> {name}</td>'
        f'<td align="right"><font color="{color}">{state}</font></td>'
        f'</tr>'
    )


def tooltip_html(sessions):
    """把聚合后的会话列表渲染成悬停提示的 HTML。

    只列**非暗态**会话。暗态不进提示：一盏灭着的灯悬停出一串"空闲"，
    只会把真正在等的那些淹掉。

    `sessions` 已经是聚合器的优先级顺序（红 > 绿 > 黄），不重排——
    第一行天然就是正在点亮这盏灯的那一个。
    """
    live = [s for s in sessions if s.state != core.DARK]
    if not live:
        return _dim("AI 状态灯 —— 没有活跃会话")

    shown = live[:MAX_ROWS]
    hidden = len(live) - len(shown)
    head, rest = shown[0], shown[1:]

    rows = [_session_row(head, bold=True)]
    if head.reason:
        # 原因只给灯首看：其他会话各带一行原因会把提示撑成一堵墙
        rows.append(f'<tr><td colspan="2">'
                    f'{_dim("&nbsp;&nbsp;&nbsp;" + esc(head.reason))}</td></tr>')

    if rest:
        rows.append(_rule())
        rows.extend(_session_row(s, bold=False) for s in rest)
    if hidden:
        rows.append(f'<tr><td colspan="2">'
                    f'{_dim(f"&nbsp;&nbsp;&nbsp;…还有 {hidden} 个")}</td></tr>')

    hint = _hint(head.state)
    if hint:
        rows.append(_rule())
        rows.append(f'<tr><td colspan="2">{_dim(esc(hint))}</td></tr>')

    return ('<table cellspacing="0" cellpadding="4">'
            + "".join(rows) + "</table>")
