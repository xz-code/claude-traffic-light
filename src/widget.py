"""置顶悬浮的三色信号灯。

形态：无边框、置顶、不占任务栏、背景全透明（只画灯本身）。
交互：拖动移位（自动记忆），单击执行动作，悬停看详情，右键出菜单。
"""

import ctypes
import json
import os
import subprocess
import sys
import time

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QGuiApplication, QIcon, QPainter,
                           QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QLabel, QMenu, QMessageBox,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

import aggregator
import core
import hook_install
import tooltip

# ---------------------------------------------------------------- 外观参数

SCALE = 1.0
D = int(34 * SCALE)          # 灯珠直径
PAD = int(9 * SCALE)         # 外壳内边距
GAP = int(10 * SCALE)        # 灯珠间距
W = PAD * 2 + D
H = PAD * 2 + D * 3 + GAP * 2
RADIUS = int(14 * SCALE)     # 外壳圆角

HOUSING = QColor(28, 28, 30)

# ---------------------------------------------------------------- 悬停提示时机

#: 鼠标在灯上停多久才弹提示框。太短会在"扫过去"时误弹，太长显得迟钝。
HOVER_SHOW_DELAY_MS = 400
#: 鼠标离开灯之后宽限多久才收。留一点余量，擦着边缘时不会闪。
HOVER_HIDE_GRACE_MS = 200

#: 悬停面板与灯之间的间隙
PANEL_GAP = 8

#: 面板内容的内缩 = QSS 的 1px 边框 + 6px 内边距。
#: 必须与 tooltip.PANEL_QSS 的 border 和 tools/render_tooltip.py 的
#: INSET 三者对齐，否则离屏预览和真机看到的间距不是一回事。
PANEL_INSET = 7

#: (键名, 亮色, 灭色)。灭色取亮色的深色版，保留一点色相，
#: 这样即使灯灭着也能看出这是红绿灯而不是三个黑洞。
LAMPS = [
    (core.RED,    QColor(255, 59, 48),  QColor(74, 18, 16)),
    (core.YELLOW, QColor(255, 204, 0),  QColor(74, 60, 8)),
    (core.GREEN,  QColor(52, 199, 89),  QColor(15, 61, 30)),
]

#: 四个状态的文案。真相在 tooltip.py（悬停提示和右键菜单共用同一份），
#: 这里保留这个名字是因为外部按它引用。
STATE_TEXT = tooltip.STATE_TEXT


def _ignore_label(session):
    """右键「忽略会话」子菜单里的一行文案。

    用和悬停提示同一份会话名（`项目 · 标题`）。只用项目名的话，同项目的两个
    会话会得到两条**逐字相同**的菜单项——而这个菜单会删状态文件，选错就是
    一次告警被静默抹掉，正是 test_ignore.py 存在的理由。

    `&` 要转义成 `&&`：Qt 把单个 `&` 当助记符，`R&D` 会渲染成带下划线的 `RD`。
    标题是用户自己打的字，一样可能带 `&`，所以整个名字都要过一遍——
    原来只处理了项目名。
    """
    name = tooltip.session_name(session).replace("&", "&&")
    return f"{name}（{STATE_TEXT.get(session.state, session.state)}）"


def _no_window_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def jump_to_window(cwd):
    """把包含 cwd 的 VSCode 窗口拉到前台。

    `code -r` 是"复用窗口打开"，正好能把已打开的该目录窗口激活，
    比猜进程树 / 抓 HWND 可靠得多。
    """
    if not cwd:
        return False
    try:
        subprocess.Popen(
            f'code -r "{cwd}"',
            shell=True,
            creationflags=_no_window_flags(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


class HoverPanel(QWidget):
    """悬停提示面板——**自己画的，不是 QToolTip**。

    为什么不用 QToolTip：**它的生命周期不归我们管**。到时限会自己收走，
    而那个隐藏是 `QTimer::singleShot`——停不掉（实测：QTipLabel 的子定时器
    0 个）；定期用相同文本重调 `showText` 也不重置它的计时（实测：3/6/9 秒
    都还在，第 10 秒照样消失，而且它一旦销毁，下次弹出必然是新建窗口 = 可见
    的一闪）。所以"鼠标停在提示框上就一直显示"这条，它根本做不到。

    自己拥有窗口之后一切都归我们管：
      * 显示 / 隐藏完全由 TrafficLight 的状态机说了算，没有别的超时
      * 位置自己摆（贴灯一侧，并钳进屏幕）
      * 刷新内容只是改文本，不存在"重弹"
      * 不再需要按类名去 Qt 内部翻 QTipLabel 改窗口属性

    窗口属性沿用灯那一套：`Qt.Tool` 让它不进任务栏、也不抢焦点。
    """

    def __init__(self, owner):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        self.owner = owner
        self.setObjectName("hoverPanel")        # 给 tooltip.PANEL_QSS 用
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating)   # 不抢焦点
        self.setFocusPolicy(Qt.NoFocus)

        self._label = QLabel(self)
        self._label.setTextFormat(Qt.RichText)
        self._label.setFocusPolicy(Qt.NoFocus)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(PANEL_INSET, PANEL_INSET,
                               PANEL_INSET, PANEL_INSET)
        lay.addWidget(self._label)

    def set_content(self, html):
        """换内容并重新量尺寸。位置不动——调用方要的就是"刷新但不跳位"。"""
        self._label.setText(html)
        self.adjustSize()

    def paintEvent(self, event):
        """自己画背景和边框。

        **不能靠 QSS 的 background-color。** 面板开了 WA_TranslucentBackground
        （为了圆角外那圈能透出桌面），而它隐含 WA_NoSystemBackground，Qt 会
        **跳过自动背景绘制**——QSS 里写 background-color 一点用没有。
        实测过：grab() 出来 8132 个像素里只有 255 个不透明，全是文字，
        背景像素 0 个。

        而这不只是难看：整块面板透明，在 Windows 上就等于**点击穿透**
        （同 WA_TranslucentBackground 的已知行为）。鼠标事件直接穿过去，
        面板收不到 Enter，灯那边发出去的 Leave 就撤销不掉，
        于是面板刚显示就消失——**一个根因，两个症状**。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 缩半像素，1px 的描边才不会被画到窗口边缘外面去
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(tooltip.SEP), 1))
        p.setBrush(QColor(tooltip.HOUSING_BG))
        p.drawRoundedRect(rect, tooltip.PANEL_RADIUS, tooltip.PANEL_RADIUS)

    def show_beside(self, anchor):
        """摆到 anchor（灯的矩形）旁边：优先右侧，放不下就翻到左侧，再钳进屏幕。

        必须钳：灯可以被拖到桌面任何地方，包括贴着屏幕边缘或任务栏。
        用 availableGeometry 而不是 geometry，免得压住任务栏。
        """
        self.adjustSize()
        scr = (QGuiApplication.screenAt(anchor.center())
               or QGuiApplication.primaryScreen())
        avail = scr.availableGeometry()
        pw, ph = self.width(), self.height()

        x = anchor.right() + 1 + PANEL_GAP
        if x + pw > avail.right() + 1:
            x = anchor.left() - PANEL_GAP - pw      # 右边放不下就翻到左边
        x = max(avail.left(), min(x, avail.right() + 1 - pw))

        y = anchor.center().y() - ph // 2
        y = max(avail.top(), min(y, avail.bottom() + 1 - ph))

        self.move(x, y)
        self.show()
        self.raise_()

    # 鼠标移到面板上也算"还在悬停"——否则一碰到它就被判成离开、收起来了，
    # 而"移到提示框上继续读"正是这个面板存在的理由。
    def enterEvent(self, event):
        self.owner._on_hover_enter()

    def leaveEvent(self, event):
        self.owner._on_hover_leave()


class TrafficLight(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.agg = aggregator.Aggregator()
        self.sessions = []
        self.state = core.DARK
        self._tooltip = ""      # 当前该显示的提示文本，只在真的变了时才换
        self._hovering = False  # 鼠标是否停在灯或面板上（含离开后的宽限期内）
        self._blink_on = True
        self._press_pos = None
        self._press_time = None
        self._drag_offset = None

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        self._restore_position()

        self._panel = HoverPanel(self)

        # 悬停延迟与离开宽限，都做成可取消的单次定时器：
        # 在延迟内移开就不弹，在宽限内回来就不收。
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.timeout.connect(self._show_tooltip)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_tooltip)

        self._blink = QTimer(self)
        self._blink.timeout.connect(self._toggle_blink)
        self._blink.start(500)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)
        self._poll.start(250)

        # 有些程序会抢置顶，定期用 Win32 重新压回最上层（不抢焦点）。
        self._topmost = QTimer(self)
        self._topmost.timeout.connect(self._assert_topmost)
        self._topmost.start(2000)

        self.refresh()

    # ------------------------------------------------------------ 状态

    def refresh(self):
        state, sessions = self.agg.current()
        changed = state != self.state
        if changed:
            core.debug(f"STATE {self.state} -> {state} "
                       f"[{','.join(s.session_id[:12] + '=' + s.state for s in sessions)}]")
        self.state = state
        self.sessions = sessions
        self._sync_tooltip()
        if changed or self._blink_on:
            self.update()

    def _sync_tooltip(self):
        """提示内容变了才换。

        原来是无条件每 250ms 重设一次。提示框正显示时改文本，Qt 会重算尺寸
        并重新定位——表现就是"鼠标一移动提示就疯狂闪烁"（docs/verification.md
        里列的不通过项之一）。内容没变就别碰它。

        这里不会引入陈旧：提示内容只取决于 self.sessions / self.state，
        而两者都在 refresh() 里刚更新过；_toggle_blink 只重绘灯、不碰提示。
        """
        text = tooltip.tooltip_html(self.sessions)
        if text == self._tooltip:
            return
        self._tooltip = text
        if self._panel.isVisible():
            # 正显示着：只换文本，窗口不动。面板是我们自己的，
            # 改内容不会重定位，所以不存在"看起来重弹了一下"。
            self._panel.set_content(text)

    # ------------------------------------------------------------ 悬停提示

    def _show_tooltip(self):
        """弹出提示面板。

        面板是**自己画的窗口**（HoverPanel），不是 QToolTip。原因见那个类的
        文档字符串：QToolTip 的隐藏是停不掉的 singleShot，超时后必然销毁重建，
        做不到"鼠标停在上面就一直显示"。

        顺带说明为什么早先连 QToolTip 都要手动调：**只有窗口是激活窗口时，
        Qt 才会发 QEvent.ToolTip**，而本窗口是 Qt.Tool，天生不会被激活——
        那正是不抢焦点、不打断你打字的原因。自己拥有窗口之后这一层也不存在了。
        """
        if not self._tooltip:
            return
        self._panel.set_content(self._tooltip)
        self._panel.show_beside(self.frameGeometry())

    def _hide_tooltip(self):
        self._panel.hide()

    # 这里曾经有个 _make_tip_click_through()：靠遍历顶层窗口、按类名
    # "QTipLabel" 找到 Qt 内部的提示框，再改它的原生 ex-style 设成鼠标穿透。
    # 那是为了绕开 QToolTip"必然压住灯"导致的"碰到就重弹"死循环。
    # 换成自己画的面板之后，窗口是我们的、位置也是我们摆的（贴在灯的侧边，
    # 不再压住灯、也不压住光标），整个 hack 连同它对 Qt 内部类名的依赖一起删掉。

    def _toggle_blink(self):
        self._blink_on = not self._blink_on
        if self.state == core.RED:
            self.update()

    # ------------------------------------------------------------ 绘制

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        p.setPen(Qt.NoPen)
        p.setBrush(HOUSING)
        p.drawRoundedRect(QRectF(0, 0, W, H), RADIUS, RADIUS)

        cx = W / 2
        for i, (key, lit_color, off_color) in enumerate(LAMPS):
            cy = PAD + D / 2 + i * (D + GAP)
            if key == self.state:
                # 只有红灯闪：红是"要你动手"，值得闪；黄绿常亮更耐看
                intensity = (1.0 if self._blink_on else 0.28) \
                    if self.state == core.RED else 1.0
                self._draw_lit(p, cx, cy, lit_color, intensity)
            else:
                self._draw_off(p, cx, cy, off_color)

    def _draw_lit(self, p, cx, cy, color, intensity):
        r = D / 2
        # 外发光：让灯看起来是"亮着的"而不是"涂了色"
        glow = QRadialGradient(cx, cy, r * 1.85)
        halo = QColor(color)
        halo.setAlpha(int(110 * intensity))
        glow.setColorAt(0.0, halo)
        halo_edge = QColor(color)
        halo_edge.setAlpha(0)
        glow.setColorAt(1.0, halo_edge)
        p.setBrush(glow)
        p.drawEllipse(QRectF(cx - r * 1.85, cy - r * 1.85, r * 3.7, r * 3.7))

        # 灯珠本体：左上偏移的高光让它像个玻璃罩
        body = QRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 1.35)
        bright = QColor(color).lighter(135)
        bright.setAlpha(int(255 * intensity))
        base = QColor(color)
        base.setAlpha(int(255 * intensity))
        body.setColorAt(0.0, bright)
        body.setColorAt(1.0, base)
        p.setBrush(body)
        p.drawEllipse(QRectF(cx - r, cy - r, D, D))

    def _draw_off(self, p, cx, cy, color):
        r = D / 2
        p.setBrush(color)
        p.drawEllipse(QRectF(cx - r, cy - r, D, D))

    # ------------------------------------------------------------ 位置记忆

    def _restore_position(self):
        pos = self.cfg.get("position")
        if isinstance(pos, list) and len(pos) == 2:
            p = QPoint(int(pos[0]), int(pos[1]))
            if self._on_some_screen(p):
                self.move(p)
                return
        self._center_right()

    def _on_some_screen(self, p):
        for screen in QApplication.screens():
            if screen.availableGeometry().contains(p):
                return True
        return False

    def _center_right(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - W - 40, screen.center().y() - H // 2)

    def _save_position(self):
        self.cfg["position"] = [self.x(), self.y()]
        self.cfg.save()

    # ------------------------------------------------------------ 鼠标

    def enterEvent(self, event):
        """鼠标进入点亮的灯珠区域——开始计时，不是立刻弹。

        注意"进入"只发生在**画了东西**的像素上：窗口开了
        WA_TranslucentBackground，而 Windows 上完全透明的像素是点击穿透的。
        所以灯的透明角落不会有悬停提示——和"点击区只限灯珠"是同一套边界。
        """
        self._on_hover_enter()

    def leaveEvent(self, event):
        self._on_hover_leave()

    # 面板上的进出走同一对处理函数：鼠标从灯挪到面板上时，灯会收到 Leave、
    # 面板收到 Enter，一次事件循环里净效果是"仍然在悬停"，所以不会闪。
    def _on_hover_enter(self):
        self._hovering = True
        self._hide_timer.stop()      # 还在宽限期内就又回来了：取消收起
        self._show_timer.start(HOVER_SHOW_DELAY_MS)

    def _on_hover_leave(self):
        self._hovering = False
        self._show_timer.stop()      # 延迟内就移开了：不弹
        self._hide_timer.start(HOVER_HIDE_GRACE_MS)

    def mousePressEvent(self, event):
        core.debug(
            f"PRESS button={event.button()} buttons={event.buttons()} "
            f"pos={event.position().toPoint()} "
            f"global={event.globalPosition().toPoint()}"
        )
        if event.button() == Qt.LeftButton:
            # 拖动期间不弹提示：按住左键时鼠标必然一直停在灯上，
            # 不拦的话 400ms 后就会弹出来挡住你要拖去的位置。
            self._show_timer.stop()
            self._hide_timer.stop()
            self._hide_tooltip()
            self._press_pos = event.position().toPoint()
            self._press_time = time.monotonic()
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        """只有"配对的按下 + 几乎没移动"才算点击。

        这里曾经埋过一个很隐蔽的 bug：原写法把"没有配对的按下"当成"没移动"，
        于是判定成点击。后果是——鼠标按着左键扫过这盏灯（比如在拖别的窗口），
        或锁屏/解锁时产生一次游离的 release，都会触发一次伪点击；而绿灯亮着时
        一次伪点击就会把它悄悄确认掉，告警就此消失，还极难复现。
        """
        if event.button() != Qt.LeftButton:
            return

        press, self._press_pos = self._press_pos, None
        press_time, self._press_time = self._press_time, None
        self._drag_offset = None

        if press is None:
            return  # 没有配对的按下 —— 不是点击，忽略
        if press_time is None or time.monotonic() - press_time > 1.5:
            return  # 陈旧的按下（比如中途窗口被藏起来收不到 release）

        if (event.position().toPoint() - press).manhattanLength() < 6:
            self._on_click(event.position())
        else:
            self._save_position()

        # 按下的那一下把待弹的计时取消掉了，松开后光标多半还在灯上，
        # 所以重新起一轮计时。不这么做就得先把鼠标移开再移回来。
        if self._hovering and not self._panel.isVisible():
            self._show_timer.start(HOVER_SHOW_DELAY_MS)

    def _active_lamp_rect(self):
        """当前点亮的那颗灯珠的圆，None 表示没有点亮的灯（暗态）。"""
        for i, (key, _, _) in enumerate(LAMPS):
            if key == self.state:
                cy = PAD + D / 2 + i * (D + GAP)
                r = D / 2
                return QRectF(W / 2 - r, cy - r, D, D)
        return None

    def _hit_lamp(self, pos):
        """点击是否落在点亮的那颗灯珠上。

        这是刻意收紧的：整盏灯是置顶悬浮的，鼠标很容易扫过它。
        如果外壳、灭掉的灯珠都算点击区，一次误触就可能把绿灯告警确认掉——
        而"别让我错过告警"正是这个工具存在的理由。
        把死区留出来，误触代价就没了。
        """
        lamp = self._active_lamp_rect()
        if lamp is None:
            return False
        return lamp.contains(QPointF(pos))

    def _on_click(self, pos):
        """单击动作：红跳窗口，绿确认熄灭，其余不响应。"""
        core.debug(f"ON_CLICK state={self.state} pos={pos} "
                   f"on_lamp={self._hit_lamp(pos)}")
        if not self.sessions:
            return
        if not self._hit_lamp(pos):
            return  # 点在外壳或没亮的灯上 —— 不执行任何破坏性动作
        top = self.sessions[0]
        if top.state == core.RED:
            jump_to_window(top.cwd)
        elif top.state == core.GREEN:
            for s in self.sessions:
                if s.state == core.GREEN:
                    self.agg.acknowledge(s, by="click")
            self.refresh()

    def _ignore_session(self, session):
        """右键「忽略会话」的动作：删掉这一个会话的状态文件，立刻重算灯态。

        这是红态唯一的**人肉出口**。红态本来只有一个自动出口——同一个会话的
        下一个事件——而被丢下的会话（窗口开着、claude.exe 还活着）永远不会有
        下一个事件，僵尸清理也够不着它。于是红灯永远闪，还把别的会话的绿/黄
        一起盖住。这个动作就是那个缺口。

        refresh 无条件调用：不刷新的话最多要等 250ms 下一轮轮询灯才灭，
        用户会以为没点中；删除失败时它也能把界面拉回和磁盘一致。
        """
        self.agg.ignore(session, by="menu")
        self.refresh()

    def _add_ignore_menu(self, menu):
        """把「忽略会话」子菜单挂到 menu 上；没有可忽略的会话时什么都不加。

        抽成独立方法是为了**可测**：contextMenuEvent 里的 menu.exec() 会阻塞，
        测试走不了那条路，但可以直接造一个 QMenu 调这里、再 trigger 各条目。
        """
        targets = [s for s in self.sessions if s.state != core.DARK]
        if not targets:
            return None
        menu.addSeparator()
        sub = menu.addMenu("忽略会话")
        head = self.sessions[0]          # scan() 已按优先级排好，第一个就是点灯的那个
        for s in targets:
            label = _ignore_label(s)
            if s is head:
                label = "● " + label     # 标出正在点亮这盏灯的那一个
            act = sub.addAction(label)
            # 默认参数 s=s 不是装饰，它同时挡两个坑：
            #   1. 闭包捕获的是变量不是值——去掉它，每个条目都会忽略循环结束时的
            #      最后一个会话；
            #   2. QAction.triggered 自带一个 checked 布尔量，写成 lambda s: ...
            #      收到的其实是 False。
            # 另外刻意捕获 Session 对象而不是下标：menu.exec() 会转起嵌套事件循环，
            # 250ms 的轮询还在旁边重赋 self.sessions，按索引重新取会指错人。
            act.triggered.connect(
                lambda _checked=False, s=s: self._ignore_session(s)
            )
        return sub

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_jump = QAction("跳到等待中的窗口", menu)
        act_jump.setEnabled(self.state == core.RED)
        act_jump.triggered.connect(lambda: jump_to_window(self.sessions[0].cwd)
                                   if self.sessions else None)
        menu.addAction(act_jump)

        act_recenter = QAction("回到默认位置", menu)
        act_recenter.triggered.connect(lambda: (self._center_right(),
                                                self._save_position()))
        menu.addAction(act_recenter)

        self._add_ignore_menu(menu)      # 没有可忽略的会话时它自己什么都不加

        menu.addSeparator()
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(QApplication.quit)
        menu.addAction(act_quit)
        menu.exec(event.globalPos())

    # ------------------------------------------------------------ Win32

    def _assert_topmost(self):
        # 面板也是 topmost，正在显示时别去跟它抢层级——否则灯会把面板咬掉一块。
        # 让过这一轮而已，2 秒后的下一轮自然会把灯补回最上层。
        if self._panel.isVisible():
            return
        if sys.platform != "win32":
            return
        try:
            HWND_TOPMOST = -1
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
            ctypes.windll.user32.SetWindowPos(
                int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
            )
        except Exception:
            pass

    def render_icon(self, size=64):
        """给托盘用的小图标。"""
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(HOUSING)
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
        d = size * 0.22
        for i, (key, lit_color, off_color) in enumerate(LAMPS):
            cy = size * 0.25 + i * size * 0.25
            color = lit_color if key == self.state else off_color
            p.setBrush(color)
            p.drawEllipse(QRectF(size / 2 - d / 2, cy - d / 2, d, d))
        p.end()
        return QIcon(pm)


class Config:
    """极简配置：只存窗口位置。"""

    def __init__(self):
        self.path = core.CONFIG_PATH
        self._data = {}
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __setitem__(self, key, value):
        self._data[key] = value

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass


def run():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    # 只含悬停面板一条规则，不会波及右键菜单和对话框
    app.setStyleSheet(tooltip.PANEL_QSS)

    cfg = Config()
    light = TrafficLight(cfg)
    light.show()

    tray = QSystemTrayIcon(light.render_icon())
    tray.setToolTip("AI 状态灯")
    menu = QMenu()

    act_toggle = QAction("显示 / 隐藏", menu)
    act_toggle.triggered.connect(
        lambda: light.hide() if light.isVisible() else light.show()
    )
    menu.addAction(act_toggle)

    menu.addSeparator()

    # ---- hook 安装/卸载 ----
    # 这条是便携版能在新机器上"开箱可用"的关键：全程不需要目标机器装 Python，
    # 也不需要用户手改 settings.json。
    act_hook = QAction("安装 hook 到 Claude Code", menu)

    def toggle_hook():
        installed, ours = hook_install.is_ours_current()
        if installed and ours:
            res = hook_install.uninstall()
            if res.get("ok") and res.get("removed"):
                hook_install.purge_installed_files()
        else:
            # 没装、或者装的是别处的运行时（比如开发模式指向仓库），
            # 都直接覆盖安装——不该让用户先点一次"卸载"再点一次"安装"。
            res = hook_install.install_for_this_app()
        box = QMessageBox()
        box.setWindowTitle("AI 状态灯")
        box.setIcon(QMessageBox.Information if res.get("ok")
                    else QMessageBox.Warning)
        box.setText(res.get("message", "完成"))
        if res.get("backup"):
            box.setInformativeText(f"原配置已备份到：\n{res['backup']}")
        box.exec()

    act_hook.triggered.connect(toggle_hook)
    menu.addAction(act_hook)

    def refresh_menu():
        """每次展开菜单时重新判断，避免菜单文案和实际状态对不上。"""
        installed, ours = hook_install.is_ours_current()
        if not installed:
            act_hook.setText("安装 hook 到 Claude Code")
        elif ours:
            st = hook_install.status()
            act_hook.setText(f"卸载 hook（当前已装 {st['installed']} 项）")
        else:
            act_hook.setText("改用本程序携带的运行时重装 hook")

    menu.aboutToShow.connect(refresh_menu)

    act_state_dir = QAction("打开状态目录", menu)
    act_state_dir.triggered.connect(
        lambda: os.startfile(str(core.STATE_DIR))
        if core.STATE_DIR.exists() else None
    )
    menu.addAction(act_state_dir)

    menu.addSeparator()

    act_quit = QAction("退出", menu)
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: light.show() if reason == QSystemTrayIcon.Trigger else None
    )
    tray.show()

    # 定时重建托盘图标，让它跟着灯色走
    def sync_tray():
        tray.setIcon(light.render_icon())
    t = QTimer()
    t.timeout.connect(sync_tray)
    t.start(1000)

    return app.exec()
