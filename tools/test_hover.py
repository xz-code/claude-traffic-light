#!/usr/bin/env python3
"""悬停面板的状态机回归测试。

锁的是"手感规则"本身，**不依赖真实鼠标**——直接驱动事件和定时器。

（这里吃过亏：用 QCursor.setPos() 模拟悬停时，如果光标本来就在目标位置，
Windows 不产生 WM_MOUSEMOVE，enterEvent 根本不触发，于是探针会给出
"面板没弹"这种看起来像 bug、其实是我实验做错了的结论。）

验的东西：
  * 进入后不立刻弹，等 HOVER_SHOW_DELAY_MS 才弹
  * 在延迟内移开 → 一次都不弹
  * 离开后不立刻收，等 HOVER_HIDE_GRACE_MS 才收
  * 在宽限内回来 → 不收
  * 鼠标移到**面板**上 → 不收（面板存在的理由就是这个）
  * 按住左键 → 立刻收，且拖动期间不弹
  * 松开左键 → 重新计时
  * 内容变了只换文本、不动窗口；没显示时不弹
  * 面板摆在灯的旁边，不压住灯

会真的把面板显示出来（屏幕角落闪几下），因为断言的就是真实可见性。

跑法：  .venv\\Scripts\\python.exe tools\\test_hover.py
"""

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import core                                                    # noqa: E402

# 隔离：绝不能碰真实的会话状态（Aggregator.scan() 会删僵尸文件）
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="hover-test-"))
core.SESSIONS_DIR = _TMP / "sessions"
core.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
core.CONFIG_PATH = _TMP / "config.json"

from PySide6.QtCore import QEvent, QPointF, Qt                  # noqa: E402
from PySide6.QtGui import QMouseEvent                           # noqa: E402
from PySide6.QtTest import QTest                                # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

import tooltip                                                  # noqa: E402
from widget import (HOVER_HIDE_GRACE_MS, HOVER_SHOW_DELAY_MS,   # noqa: E402
                    Config, TrafficLight)


def _mouse_event(kind):
    return QMouseEvent(kind, QPointF(26, 70), QPointF(1000, 1000),
                       Qt.LeftButton, Qt.NoButton, Qt.NoModifier)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(tooltip.PANEL_QSS)

    light = TrafficLight(Config())
    for t in (light._blink, light._poll, light._topmost,
              light._show_timer, light._hide_timer):
        t.stop()
    light._on_click = lambda pos: None      # 别真的去跳窗口/确认
    light.move(400, 400)

    results = []

    def check(name, passed, note=""):
        results.append((name, passed, note))

    def shown():
        return light._panel.isVisible()

    def settle():
        """把状态清干净：面板收起、鼠标不在上面。"""
        light._show_timer.stop()
        light._hide_timer.stop()
        light._hovering = False
        light._hide_tooltip()

    def content(n):
        """造点会变化的会话内容，让提示文本跟着变。"""
        from render_tooltip import fake
        return [fake("lkw", f"第 {n} 件事", core.RED, "Claude 在向你提问")]

    # ---------------- 延迟：扫过不弹，停下才弹 ----------------

    settle()
    light.enterEvent(None)
    QTest.qWait(HOVER_SHOW_DELAY_MS // 2)
    check("延迟未到时不弹", not shown(),
          f"等了 {HOVER_SHOW_DELAY_MS // 2}ms")

    QTest.qWait(HOVER_SHOW_DELAY_MS // 2 + 80)
    check("延迟到了要弹", shown(), f"共等了 {HOVER_SHOW_DELAY_MS + 80}ms")

    settle()
    light.enterEvent(None)
    QTest.qWait(60)                       # 很快就扫过去了
    light.leaveEvent(None)
    QTest.qWait(HOVER_SHOW_DELAY_MS + 120)
    check("延迟内移开就一次都不弹", not shown(), "扫过（60ms）后移开")

    # ---------------- 宽限：离开不是立刻收 ----------------

    settle()
    light.enterEvent(None)
    QTest.qWait(HOVER_SHOW_DELAY_MS + 80)
    light.leaveEvent(None)
    QTest.qWait(HOVER_HIDE_GRACE_MS // 2)
    check("离开后宽限内不收", shown(),
          f"离开 {HOVER_HIDE_GRACE_MS // 2}ms")

    QTest.qWait(HOVER_HIDE_GRACE_MS // 2 + 80)
    check("宽限到了就收", not shown(), f"共离开 {HOVER_HIDE_GRACE_MS + 80}ms")

    settle()
    light.enterEvent(None)
    QTest.qWait(HOVER_SHOW_DELAY_MS + 80)
    light.leaveEvent(None)
    QTest.qWait(HOVER_HIDE_GRACE_MS // 2)
    light.enterEvent(None)                # 宽限内回来了
    QTest.qWait(HOVER_HIDE_GRACE_MS + 80)
    check("宽限内回来就不收（擦边不闪）", shown(), f"离开 {HOVER_HIDE_GRACE_MS // 2}ms 后回来")

    # ---------------- 鼠标移到面板上：不收 ----------------

    # 必须走**面板自己的** enterEvent，不能直接调 light._on_hover_enter()：
    # 那样会绕过面板的处理器，把 enterEvent 改成空操作也验不出来
    # （这条是真的踩过——变异测试第一次没逮住，就是因为我图省事直接调了内部方法）。
    light.leaveEvent(None)                # 光标离开灯
    light._panel.enterEvent(None)         # 挪到面板上
    QTest.qWait(HOVER_HIDE_GRACE_MS + 120)
    check("鼠标停在面板上不收（这是面板存在的理由）", shown(),
          "光标从灯挪到面板上")

    # ---------------- 拖动：按住就收，期间不弹 ----------------

    settle()
    light.enterEvent(None)
    QTest.qWait(HOVER_SHOW_DELAY_MS + 80)
    check("拖动前：面板在显示", shown(), "已悬停并弹出")

    light.mousePressEvent(_mouse_event(QEvent.MouseButtonPress))
    check("按下左键立刻收起", not shown(), "按下左键")

    QTest.qWait(HOVER_SHOW_DELAY_MS + 150)
    check("拖动期间不弹（不挡你要拖去的位置）", not shown(), f"按住并等了 {HOVER_SHOW_DELAY_MS + 150}ms")

    light.mouseReleaseEvent(_mouse_event(QEvent.MouseButtonRelease))
    QTest.qWait(HOVER_SHOW_DELAY_MS + 120)
    check("松开后重新计时并弹出", shown(), f"松开后等了 {HOVER_SHOW_DELAY_MS + 120}ms")

    # ---------------- 内容刷新：只换文本，不动窗口 ----------------

    light.agg.current = lambda: (core.RED, content(1))
    light.refresh()
    QTest.qWait(60)
    old_pos = light._panel.pos()
    old_text = light._panel._label.text()

    light.agg.current = lambda: (core.RED, content(2))
    light.refresh()
    QTest.qWait(60)
    check("内容变了会换掉", light._panel._label.text() != old_text,
          "两次 refresh 之间内容确实变了")
    check("换内容时窗口不动（不跳位）", light._panel.pos() == old_pos,
          f"{old_pos} -> {light._panel.pos()}")

    settle()
    light.agg.current = lambda: (core.RED, content(3))
    light.refresh()
    QTest.qWait(80)
    check("没在显示时内容变了不弹", not shown(), "内容变了但面板没显示")

    # ---------------- 位置：贴在灯的旁边，不压住灯 ----------------

    light.enterEvent(None)
    QTest.qWait(HOVER_SHOW_DELAY_MS + 80)
    lamp = light.frameGeometry()
    panel = light._panel.frameGeometry()
    check("面板不压住灯", not lamp.intersects(panel),
          f"灯={lamp.getRect()} 面板={panel.getRect()}")
    check("面板就在灯的附近（没有跑到屏幕另一头）",
          abs(panel.center().x() - lamp.center().x()) < 900,
          f"横向距离 {abs(panel.center().x() - lamp.center().x())}px")

    # ---------------- 背景必须真的画出来 ----------------

    # 这条不是"好不好看"。面板开了 WA_TranslucentBackground，而**透明的像素
    # 在 Windows 上等于点击穿透**——整块透明的话，鼠标事件会直接穿过去，
    # 面板收不到 Enter，灯那边发出去的 Leave 就撤销不掉，面板刚显示就消失。
    # 曾经就是这么坏的：QSS 的 background-color 被 WA_NoSystemBackground 跳过，
    # grab() 出来 8132 个像素里只有 255 个不透明（全是文字），背景像素 0 个。
    img = light._panel.grab().toImage()
    total = img.width() * img.height()
    opaque = sum(1 for y in range(img.height()) for x in range(img.width())
                 if img.pixelColor(x, y).alpha() == 255)
    check("面板背景是不透明的（透明就等于整块点击穿透）",
          opaque > total * 0.5,
          f"不透明像素 {opaque}/{total}（{opaque * 100 // total}%）；"
          f"文字本身占不到一半")

    settle()

    # ---------------- 汇总 ----------------

    print(f"{'场景':<46}{'结果':<8}说明")
    print("-" * 78)
    ok = True
    for name, passed, note in results:
        ok = ok and passed
        print(f"{name:<46}{'OK' if passed else 'FAIL':<8}{note}")
    print()
    print("悬停面板:", "全部符合预期" if ok else "有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
