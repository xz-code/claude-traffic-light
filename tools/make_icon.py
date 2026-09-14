#!/usr/bin/env python3
"""生成应用图标 build_cache/app.ico，供 PyInstaller 使用。

图标直接用信号灯自己的绘制逻辑，保证和界面上看到的观感一致。
和 widget.render_icon() 的区别：那个是托盘图标，反映**当前灯态**；
应用图标要的是"一眼认出这是红绿灯"，所以三颗灯珠全亮。

用法：  .venv\\Scripts\\python.exe tools\\make_icon.py
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QRectF, Qt                        # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap          # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

from widget import HOUSING, LAMPS                            # noqa: E402

SIZE = 256


def draw(size=SIZE):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)

    # 外壳：略窄于正方形，才像一杆红绿灯而不是个方块
    margin_x = size * 0.22
    p.setBrush(HOUSING)
    p.drawRoundedRect(
        QRectF(margin_x, size * 0.04, size - margin_x * 2, size * 0.92),
        size * 0.16, size * 0.16,
    )

    # 三颗灯珠全部点亮 —— 图标要的是辨识度，不是当前状态
    r = size * 0.115
    cx = size / 2
    for i, (_key, lit, _off) in enumerate(LAMPS):
        cy = size * 0.21 + i * size * 0.29
        p.setBrush(QColor(lit))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        # 左上角一点高光，和界面上灯珠的质感一致
        p.setBrush(QColor(255, 255, 255, 90))
        hr = r * 0.34
        p.drawEllipse(QRectF(cx - r * 0.45 - hr, cy - r * 0.45 - hr,
                             hr * 2, hr * 2))
    p.end()
    return pm


def main():
    app = QApplication(sys.argv)
    out = ROOT / "build_cache" / "app.ico"
    out.parent.mkdir(parents=True, exist_ok=True)

    pm = draw()
    if not pm.save(str(out), "ICO"):
        print("Qt 写不了 ICO，改存 PNG（PyInstaller 也接受，但建议手动转成 ico）")
        out = out.with_suffix(".png")
        if not pm.save(str(out), "PNG"):
            print("PNG 也写不了")
            return 1

    print(f"已生成 {out}（{out.stat().st_size / 1024:.1f} KB, {SIZE}x{SIZE}）")
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
