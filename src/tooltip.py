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

#: 名字里项目名 / 标题各自保留多少字。
#:
#: 标题从 12 提到 24 是**量出来的**决定：12 是为中文短 prompt 调的，
#: 而 Claude Code 的 AI 标题更长、且爱把通用词堆在前面，实测 19 条真实标题里
#: 12 条被切成了没用的残渣（`Windows 桌面 AI 状态指示灯` -> `Windows 桌面 A`、
#: `Check configured Claude Code API` -> `Check config`）。放宽后提示框从
#: 238px 宽到约 305px，这是可接受的代价。
PROJECT_SHOW = 18
TITLE_SHOW = 24

#: 最多列几个会话，多出来的折成一行"…还有 N 个"
MAX_ROWS = 6

#: 面板圆角半径。widget.HoverPanel 的 paintEvent 和
#: tools/render_tooltip.py 的离屏预览共用，保证"预览所见 = 真机所得"。
PANEL_RADIUS = 6

#: 悬停面板里**文字**的样式。
#:
#: ⚠️ 背景和边框**故意不写在这里**：面板开了 WA_TranslucentBackground（为了
#: 圆角外那圈能透出桌面），而它隐含 WA_NoSystemBackground，Qt 会**跳过自动
#: 背景绘制**——QSS 的 background-color/border 写了也不生效（实测 grab() 出来
#: 一个背景像素都没有，只有文字）。所以底色和边框改由 HoverPanel.paintEvent
#: 自己画，颜色从这里取。
#:
#: 不设 font-size 是有意的——用系统默认字体，和实测时的渲染条件一致。
PANEL_QSS = f"""
#hoverPanel QLabel {{
    color: {FG};
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
    """横贯整个表格的分隔线。

    用单元格底边框，**刻意不用 `━` 字符**：字符线的像素宽是写死的，而表格是
    自适应的。实测标题放宽到 24 字后表格宽 289px，而 `━`×28 只有 203px——
    短 86px，看着像没对齐。底边框跟着表格走，表格多宽都对得上（实测 297 vs 289）。
    """
    return (f'<tr><td colspan="2" '
            f'style="border-bottom: 1px solid {SEP}"></td></tr>')


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

    标题的取舍，三级回退：

    1. `ai_title` —— Claude Code 按**对话内容**总结的名字，会跟着会话推进更新
       （实测 17 个有该字段的会话里 7 个中途变过，甚至中英文互换）。优先用它，
       是因为"在聊什么"比"第一句问了什么"更贴近你此刻想知道的。
    2. `title` —— 本程序记的**第一条指令**，一旦写下永不改变。不是备胎而是主力
       的一半：实测 36 个 transcript 里 17 个压根没有 `ai-title` 记录
       （子 agent 的全没有、老会话也没有），那些会话只能靠它。
    3. 会话号前 8 位 —— 本次升级之前就开着的、连 title 都没有的会话。

    第 3 级看着像故障其实不是：那个会话自己再发一次指令，就被补上了。
    """
    project = (session.project or "(未知)")[:PROJECT_SHOW]
    title = ((getattr(session, "ai_title", "") or "").strip()
             or (getattr(session, "title", "") or "").strip())
    if not title:
        title = "#" + (session.session_id or "?")[:8]
    else:
        # rstrip 是必要的：从词中间切开时会在末尾留一个空格
        # （实测 `Claude Code 会话名称规则` 截 12 字就截出了 `'Claude Code '`）。
        # 中文切不出空格，所以这个毛病只在英文标题上看得见。
        title = title[:TITLE_SHOW].rstrip()
    return f"{project} · {title}"


# ---------------------------------------------------------------- 渲染

def _session_row(session, bold):
    """一行会话：左边彩色圆点+名字，右边同色状态词，右对齐。

    两列都**不设固定宽度**，交给表格自适应。这是量出来的决定：自适应和固定宽度
    **一样对齐**（各状态色的最右 x 取值唯一），但自适应窄得多——固定宽度会白扔
    一百多像素的空白，看着就"糙"。对齐靠的是 <td align="right">，不是靠宽度。

    （当初做这个对比用的 `--variants` 已从 render_tooltip.py 移除。对齐那一条
    现在仍可复核：`render_tooltip.py --align`。宽度对比的那半已经没有现成命令，
    别再照抄一个不存在的参数。）
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
