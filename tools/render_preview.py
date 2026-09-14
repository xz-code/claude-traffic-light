#!/usr/bin/env python3
"""离屏渲染四态预览图，用于不改代码就检查外观。

用法：  .venv\\Scripts\\python.exe tools\\render_preview.py [输出路径]
"""

import pathlib
import sys

# 刻意不用 QT_QPA_PLATFORM=offscreen：那个插件在 Windows 上字体库是空的
# （QFontDatabase.families() 返回 0 个），文字会全渲染成方块。
# 改用正常平台但全程不 show()，light.grab() 本就是离屏绘制，不会闪窗口。

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QRect, Qt                      # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication                 # noqa: E402

import core                                                # noqa: E402
from widget import H, W, Config, TrafficLight              # noqa: E402

ORDER = [core.RED, core.YELLOW, core.GREEN, core.DARK]
LABEL = {
    core.RED: "红 / Waiting",
    core.YELLOW: "黄 / Working",
    core.GREEN: "绿 / Done",
    core.DARK: "暗 / Idle",
}


def main():
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "preview.png"

    app = QApplication(sys.argv)
    light = TrafficLight(Config())
    light._poll.stop()          # 预览时不让它被真实会话状态覆盖
    light._blink.stop()
    light._topmost.stop()
    light._blink_on = True      # 定格红灯的"亮"相，不然可能拍到暗的那一帧

    pad_x, pad_top, pad_bottom = 36, 30, 56
    cell_w, cell_h = W + pad_x * 2, H + pad_top + pad_bottom
    canvas = QPixmap(cell_w * len(ORDER), cell_h)
    canvas.fill(QColor(246, 246, 248))

    p = QPainter(canvas)
    p.setRenderHint(QPainter.Antialiasing)
    font = QFont("Microsoft YaHei", 11)
    p.setFont(font)

    for i, state in enumerate(ORDER):
        light.state = state
        light.update()
        x = i * cell_w
        p.drawPixmap(x + pad_x, pad_top, light.grab())
        p.setPen(QColor(90, 90, 95))
        p.drawText(
            QRect(x, cell_h - pad_bottom + 8, cell_w, 30),
            Qt.AlignHCenter | Qt.AlignTop,
            LABEL[state],
        )
    p.end()

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out))
    print(f"已渲染 {canvas.width()}x{canvas.height()} -> {out}")

    light._poll.stop()
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
