#!/usr/bin/env python3
"""点击判定的回归测试。

背景：这里曾经埋过两个叠加的缺陷——

1. 把"没有配对的按下"当成"没移动"，于是判定成点击。鼠标按着左键扫过灯、
   或锁屏解锁时的一次游离 release，都会触发伪点击。
2. 整盏灯外壳都算点击区。对一个"别让我错过告警"的工具来说，置顶悬浮的小窗
   被鼠标扫过太容易了，一次误触就能把绿灯告警静默确认掉。

第 2 条尤其致命，因为它的后果是"告警消失"，而你永远不会知道曾经有过告警。

跑法：  .venv\\Scripts\\python.exe tools\\test_click.py
"""

import pathlib
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt        # noqa: E402
from PySide6.QtGui import QMouseEvent                         # noqa: E402
from PySide6.QtWidgets import QApplication                    # noqa: E402

import core                                                   # noqa: E402
from widget import D, GAP, LAMPS, PAD, W, Config, TrafficLight  # noqa: E402


def lamp_center(index):
    """第 index 颗灯珠的圆心。"""
    return QPoint(int(W / 2), int(PAD + D / 2 + index * (D + GAP)))


def release_event(x, y):
    return QMouseEvent(
        QEvent.MouseButtonRelease,
        QPointF(x, y), QPointF(1000 + x, 1000 + y),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier,
    )


def main():
    app = QApplication(sys.argv)
    light = TrafficLight(Config())
    for t in (light._blink, light._poll, light._topmost):
        t.stop()

    results = []

    # ---------------- 命中判定：只有点亮的灯珠算点击区 ----------------

    light.state = core.GREEN
    green_idx = next(i for i, (k, _, _) in enumerate(LAMPS) if k == core.GREEN)
    results.append((
        "点在点亮的绿灯上",
        light._hit_lamp(lamp_center(green_idx)),
        "应命中",
    ))
    results.append((
        "点在没亮的红灯上",
        not light._hit_lamp(lamp_center(0)),
        "应落空（死区）",
    ))
    results.append((
        "点在外壳左上角",
        not light._hit_lamp(QPoint(2, 2)),
        "应落空（死区）",
    ))
    light.state = core.DARK
    results.append((
        "暗态下点任何位置",
        not light._hit_lamp(lamp_center(green_idx)),
        "应落空（没有点亮的灯）",
    ))

    # ---------------- 事件判定：必须按下+释放配对 ----------------

    calls = []
    moved = []
    real_on_click = light._on_click                   # 留一份真的，后面要还回去
    light._on_click = lambda pos: calls.append(pos)   # 打桩
    light._save_position = lambda: moved.append(1)

    def reset():
        calls.clear()
        moved.clear()
        light._press_pos = None
        light._press_time = None

    light.state = core.GREEN
    center = lamp_center(green_idx)

    reset()
    light.mouseReleaseEvent(release_event(center.x(), center.y()))
    results.append(("无按下直接 release（伪点击场景）",
                    len(calls) == 0, "应忽略"))

    reset()
    light._press_pos = center
    light._press_time = time.monotonic()
    light.mouseReleaseEvent(release_event(center.x(), center.y()))
    results.append(("按下后原地释放", len(calls) == 1, "应算点击"))

    reset()
    origin = light.rect().topLeft()
    light._press_pos = origin
    light._press_time = time.monotonic()
    light.mouseReleaseEvent(release_event(origin.x() + 40, origin.y() + 40))
    results.append(("按下后拖动", len(calls) == 0 and len(moved) == 1,
                    "应算移动而非点击"))

    reset()
    light._press_pos = center
    light._press_time = time.monotonic() - 5.0
    light.mouseReleaseEvent(release_event(center.x(), center.y()))
    results.append(("陈旧按下（超 1.5 秒）", len(calls) == 0, "应忽略"))

    # ---------------- 行为联动：点外壳不该确认绿灯 ----------------

    class FakeSession:
        state = core.GREEN
        cwd = "d:/fake"
        session_id = "SIM-fake"

    light._on_click = real_on_click           # 还回真实实现，否则测的是桩
    light.state = core.GREEN
    light.sessions = [FakeSession()]          # _on_click 会先检查有没有会话
    acked = []
    light.agg.acknowledge = lambda s, by="unknown": acked.append(by)
    light.refresh = lambda: None              # 别让它去读真实状态覆盖掉桩

    light._on_click(QPointF(2, 2))                    # 外壳角落
    results.append(("点外壳不会确认绿灯", len(acked) == 0, "不该确认"))

    light._on_click(QPointF(center))                  # 灯珠上
    results.append(("点灯珠才会确认绿灯",
                    acked == ["click"], f"acked={acked}"))

    print(f"{'场景':<32}{'结果':<8}说明")
    print("-" * 62)
    ok = True
    for name, passed, note in results:
        ok = ok and passed
        print(f"{name:<32}{'OK' if passed else 'FAIL':<8}{note}")
    print()
    print("点击判定:", "全部符合预期" if ok else "有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
