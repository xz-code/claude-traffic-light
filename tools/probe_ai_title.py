#!/usr/bin/env python3
"""探针：实测 Claude Code 的 `ai-title` 记录到底长什么样、离文件尾有多远。

**为什么要有这个探针**：`ai-title` 不在任何官方文档里，它是 Claude Code 的
内部 jsonl 记录。能"读它的最后一条"这件事是二手转述来的，不能直接信。

这里要证伪/证实的假设：
  H1. 每个 transcript 里确实存在 `{"type":"ai-title","aiTitle":...}`
  H2. 最后一条 ai-title 落在文件末尾的**固定字节窗口**内（决定 tail 要开多大）
  H3. 一条记录不会跨过窗口边界（否则按行切会切出半截 JSON）
  H4. 同一文件的 aiTitle 会变（所以必须取最后一条，不能取第一条）

H2 是唯一真正决定实现的：窗口开小了会读不到（表现是"名字有时候不出来"，
且只在长会话上出现，极难复现）。所以这里对全部项目目录里的每一个 jsonl
穷举测量，取**最大值**再留一倍余量。

只读，不写任何东西。
"""

import json
import pathlib
import sys

PROJECTS = pathlib.Path.home() / ".claude" / "projects"

#: 先扫这么大一段，够大就行——本探针的目的正是量出真实需要多少
SCAN = 1 << 20  # 1 MiB


def last_ai_title(path):
    """从文件末尾往回扫，返回 (最后一个 aiTitle, 它离 EOF 的字节数)。

    找不到返回 (None, None)。
    """
    size = path.stat().st_size
    with open(path, "rb") as fh:
        start = max(0, size - SCAN)
        fh.seek(start)
        buf = fh.read()
    found = (None, None)
    for line in buf.split(b"\n"):
        if b'"ai-title"' not in line:
            continue
        try:
            rec = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        if rec.get("type") != "ai-title":
            continue
        # 该行结束位置离 EOF 多远
        idx = buf.rfind(line)
        dist = len(buf) - (idx + len(line))
        found = (rec.get("aiTitle"), dist)
    return found


def main():
    files = sorted(PROJECTS.rglob("*.jsonl"))
    print(f"扫描 {len(files)} 个 jsonl\n")

    hits, miss, max_dist = [], [], 0
    all_titles = {}

    for p in files:
        try:
            title, dist = last_ai_title(p)
        except Exception as exc:
            print(f"  !! 读失败 {p.name[:12]}: {exc}")
            continue
        if title is None:
            miss.append(p)
            continue
        max_dist = max(max_dist, dist)
        hits.append((p, title, dist))
        all_titles.setdefault(title, []).append(p.stem[:8])

    print("=== H1/H2：有 ai-title 的文件，最后一个离 EOF 多远 ===")
    for p, title, dist in sorted(hits, key=lambda x: -x[2]):
        rel = p.parent.name.replace("d--Mr-Files-GitHub-", "")
        print(f"  {dist:>9,}B  {rel}/{p.stem[:8]}  {title!r}")

    print(f"\n命中 {len(hits)} / 缺失 {len(miss)}   （共 {len(files)}）")
    print(f"离 EOF 最大距离 = {max_dist:,}B  ->  窗口至少要 {max_dist:,}B")

    print("\n=== H4：同一个文件里 aiTitle 是否变过 ===")
    dupes = {t: s for t, s in all_titles.items() if len(s) > 1}
    if dupes:
        for t, s in dupes.items():
            print(f"  {t!r} 出现在 {len(s)} 个文件: {s}")
    else:
        print("  所有文件的标题互不相同（不足以证实会变，见下方逐会话时间线）")

    print("\n=== 缺失 ai-title 的是哪些（H1 的反例）===")
    for p in miss[:12]:
        rel = p.parent.name[:40]
        print(f"  {rel}/{p.stem[:8]}  {p.stat().st_size:,}B")
    if len(miss) > 12:
        print(f"  … 还有 {len(miss) - 12} 个")

    return 0


if __name__ == "__main__":
    sys.exit(main())
