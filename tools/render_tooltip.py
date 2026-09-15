#!/usr/bin/env python3
"""悬停提示的离屏预览。

干两件事：

1. **看一眼**提示框长什么样，不用真的去悬停那盏灯。改文案/配色时这是最快的回路。
2. **量一下**右列到底对没对齐——不靠眼睛，数像素。

主路径是"诚实"的：把**同一份 HTML** 喂给 QTextDocument。QLabel/QTipLabel 的富文本
内部就是 QTextDocument，同引擎、同字体度量，所以表格列宽、对齐、换行都与真机一致——
这不是近似。唯一要复刻的是外框（底色/边框/内边距），那些取自 tooltip.py 的常量，
真机那边由 tooltip.PANEL_QSS 决定。

刻意**不**用 QT_QPA_PLATFORM=offscreen：Windows 上那样字体库是空的，文字会全变方块。

用法：
    python tools\\render_tooltip.py                  渲染固定样例到临时目录
    python tools\\render_tooltip.py --align          量右列对齐（每种状态色的最右 x）
    python tools\\render_tooltip.py --live           读真实 sessions\\ 目录，渲染此刻的提示
    python tools\\render_tooltip.py --live --out docs\\tooltip-preview.png
"""

import argparse
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import core          # noqa: E402
import aggregator    # noqa: E402
import tooltip       # noqa: E402

from PySide6.QtCore import Qt, QRectF                   # noqa: E402
from PySide6.QtGui import (QColor, QPainter, QPen, QPixmap,       # noqa: E402
                           QTextDocument)
from PySide6.QtWidgets import QApplication                        # noqa: E402

DEFAULT_OUT = pathlib.Path.home() / "AppData" / "Local" / "Temp"

#: 与 tooltip.PANEL_QSS / widget.PANEL_INSET 对齐的外框参数
BORDER = 1
PADDING = 6
INSET = BORDER + PADDING


# ---------------------------------------------------------------- 样例

def fake(project, title, state, reason="", sid=None):
    """造一个 Session。走真的 Session 类，省得预览和真机用两套形状。"""
    sid = sid or (project[:4].ljust(4, "0") + "0" * 28)
    return aggregator.Session(
        {"session_id": sid, "cwd": "d:/x/" + project, "project": project,
         "state": state, "reason": reason, "title": title},
        core.SESSIONS_DIR / f"{sid}.json",
    )


def samples():
    """固定样例：一屏看全所有分支。"""
    return {
        "01-单红灯带原因": [
            fake("lkw", "优化悬浮提示", core.RED, "请求授权：AskUserQuestion"),
        ],
        "02-单绿灯": [
            fake("lkw", "重构 hook 安装", core.GREEN, "本轮结束，等你验收"),
        ],
        "03-同项目两会话不同标题": [
            fake("lkw", "优化悬浮提示", core.RED, "Claude 在向你提问"),
            fake("lkw", "改登录页", core.GREEN, ""),
        ],
        "04-同项目两会话无标题": [
            fake("lkw", "", core.RED, "计划待你批准", sid="a3f2" + "0" * 28),
            fake("lkw", "", core.YELLOW, "", sid="7c19" + "0" * 28),
        ],
        "05-九个会话看溢出折叠": [
            fake("lkw", "优化悬浮提示", core.RED, "Claude 在向你提问"),
        ] + [
            fake(f"proj-{i}", f"第 {i} 件事", core.YELLOW)
            for i in range(2, 10)
        ],
        "06-空": [],
    }


# ---------------------------------------------------------------- 渲染

def render_honest(html_text, font=None):
    """把 HTML 渲染成一张带外框的图。返回 QPixmap。"""
    doc = QTextDocument()
    doc.setDocumentMargin(0)          # QLabel 内部就是 0；不设会多出 4px 白边
    doc.setDefaultFont(font or QApplication.font())
    doc.setHtml(html_text)

    size = doc.size()
    w = int(size.width()) + INSET * 2
    h = int(size.height()) + INSET * 2
    pm = QPixmap(max(w, 1), max(h, 1))
    pm.fill(Qt.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(tooltip.SEP), BORDER))
    p.setBrush(QColor(tooltip.HOUSING_BG))
    # 圆角半径与真机同源（tooltip.PANEL_RADIUS），别在这里写死数字
    p.drawRoundedRect(QRectF(0.5, 0.5, pm.width() - 1, pm.height() - 1),
                      tooltip.PANEL_RADIUS, tooltip.PANEL_RADIUS)
    p.translate(INSET, INSET)
    doc.drawContents(p)
    p.end()
    return pm


#: 参与对齐测量的颜色。**不含 DARK**：那个灰色同时用在原因行和底部提示上，
#: 它们跨列、位置本来就靠左，混进来会得到一个假的"没对齐"
#: （第一版就是这么误判的）。而 tooltip_html 会把暗态会话整个滤掉，
#: 所以状态列里根本不会出现 DARK 色。
_MEASURE = (core.RED, core.YELLOW, core.GREEN)


def right_edges(pm):
    """每种状态色在图上出现的最右 x —— 用来判断右列有没有对齐。

    右列是按状态着色的，所以"每种颜色的最右像素"就是那一行状态文字的右边缘。
    （左侧的圆点同色但更靠左，取 max 自然取到右列。）
    对齐的话，所有颜色的最右 x 应该相等。
    """
    img = pm.toImage()
    targets = {s: QColor(tooltip.STATE_COLOR[s]).rgb() for s in _MEASURE}
    best = {name: -1 for name in targets}
    for y in range(img.height()):
        for x in range(img.width()):
            px = img.pixel(x, y)
            for name, rgb in targets.items():
                if px == rgb and x > best[name]:
                    best[name] = x
    return {k: v for k, v in best.items() if v >= 0}


# ---------------------------------------------------------------- 模式

def run_align(app):
    """量每一份样例的右列右边缘，确认取值唯一（= 真的对齐）。

    历史背景：这条测量当初是用来在"固定宽度"和"自适应"之间做决定的。结论是
    两者**一样对齐**，但固定宽度白扔了一百多像素的空白，所以选了自适应。
    那个对比不用再跑；留下这条常设的测量，是为了让"对齐"这件事随时可查、
    并且能变成 test_tooltip.py 里的永久回归。
    """
    print("=" * 74)
    print("右列对齐测量（每种状态色的最右 x 应该只有一个取值）")
    print("=" * 74)
    all_ok = True
    for name, sessions in samples().items():
        pm = render_honest(tooltip.tooltip_html(sessions))
        edges = right_edges(pm)
        distinct = sorted(set(edges.values()))
        ok = len(distinct) <= 1          # 单会话时只有一种色，也该是"齐"的
        all_ok = all_ok and ok
        mark = "OK" if ok else "FAIL"
        print(f"  {name:<28}{pm.width():>4}x{pm.height():<4} {mark:<6}"
              f"最右 x = {distinct if distinct else '（没有状态文字）'}")
    print()
    print("对齐测量:", "全部对齐" if all_ok else "有不对齐的样例")
    return 0 if all_ok else 1


def run_samples(app):
    outdir = DEFAULT_OUT
    print("=" * 74)
    print(f"固定样例 → {outdir}")
    print("=" * 74)
    for name, sessions in samples().items():
        pm = render_honest(tooltip.tooltip_html(sessions))
        path = outdir / f"tooltip-{name}.png"
        pm.save(str(path))
        print(f"  {name:<28}{pm.width():>4}x{pm.height():<4} {path.name}")
    return 0


def run_live(app, out):
    """读真实状态目录，渲染"此刻我的提示长什么样"——替代悬停。"""
    _, sessions = aggregator.Aggregator().current()
    pm = render_honest(tooltip.tooltip_html(sessions))
    out = pathlib.Path(out) if out else DEFAULT_OUT / "tooltip-live.png"
    pm.save(str(out))
    print(f"当前 {len(sessions)} 个会话 → {out}  ({pm.width()}x{pm.height()})")
    for s in sessions:
        print(f"  {s.state:<8}{tooltip.session_name(s)}"
              f"{'  原因：' + s.reason if s.reason else ''}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="悬停提示离屏预览")
    ap.add_argument("--align", action="store_true",
                    help="量每一份样例的右列右边缘，确认对齐")
    ap.add_argument("--live", action="store_true",
                    help="读真实状态目录，渲染此刻的提示")
    ap.add_argument("--out", default=None, help="输出路径（配合 --live）")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    # 真机的外框由这条样式表决定；这里只是为了取它的字体度量
    app.setStyleSheet(tooltip.PANEL_QSS)

    if args.align:
        return run_align(app)
    if args.live:
        return run_live(app, args.out)
    return run_samples(app)


if __name__ == "__main__":
    sys.exit(main())
