#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
release.py — 一键发版：递增版本号 + 写版本说明 + 同步各处版本标记

每次改动 Skill 后都走这一步，保证「版本树」上每一版都有说明（见 CHANGELOG.md）。

用法：
  python scripts/release.py patch "修 verify.py 误报 su"
  python scripts/release.py minor "新增 xxx 脚本"
  python scripts/release.py major "改数据结构"
  python scripts/release.py patch "..." --files "verify.py,smoke_test.py"
  python scripts/release.py --show            # 只看当前版本，不改任何文件

它会：
  1. 读 Skill 根目录 VERSION，按 patch/minor/major 递增；
  2. 在 CHANGELOG.md 的 <!-- NEXT_VERSION --> 处插入新版本条目（含日期、说明、影响文件）；
  3. 重新生成 CHANGELOG.md 顶部的 ASCII 版本树；
  4. 更新 CHANGELOG.md「当前版本」行与 SKILL.md 顶部的「版本：vX.Y.Z（日期）」行；
  5. 打印后续动作（跑 smoke_test.py 自验 → sync_github.py 同步 GitHub）。

纯标准库，零依赖。
"""
import os
import re
import sys
import argparse
import datetime

MARK = "<!-- NEXT_VERSION -->"
VER_FILE = "VERSION"
LOG_FILE = "CHANGELOG.md"
SKILL_FILE = "SKILL.md"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def p(*a):
    print(*a, flush=True)


def read_version():
    fp = os.path.join(ROOT, VER_FILE)
    if not os.path.exists(fp):
        return "0.0.0"
    return open(fp, encoding="utf-8").read().strip() or "0.0.0"


def bump(ver, level):
    parts = (ver.split(".") + ["0", "0", "0"])[:3]
    try:
        ma, mi, pa = [int(x) for x in parts]
    except ValueError:
        ma, mi, pa = 0, 0, 0
    if level == "major":
        ma, mi, pa = ma + 1, 0, 0
    elif level == "minor":
        mi, pa = mi + 1, 0
    else:
        pa += 1
    return "%d.%d.%d" % (ma, mi, pa)


def rebuild_tree(text):
    """扫描所有 '## vX.Y.Z · 日期 · 说明' 标题，重新生成倒序 ASCII 版本树。"""
    entries = re.findall(r"^## (v[\d.]+) · ([\d-]+) · (.+?)\s*$", text, re.M)
    seen, uniq = set(), []
    for v, d, t in entries:
        if v in seen:
            continue
        seen.add(v)
        uniq.append((v, d, t))
    lines = ["```text"]
    for i, (v, d, t) in enumerate(uniq):
        head = "v%s  %s  %s" % (v[1:], d, t)
        if i == 0:
            lines.append(head)
        else:
            lines.append("%s└─ %s" % (" " * 2, head))
    if len(uniq) > 1:
        lines.append("")
        lines.append("（倒序：最新在上；完整说明见下方各版本条目）")
    lines.append("```")
    tree = "\n".join(lines)
    # 替换现存的树块（"## 版本树" 之后的第一个 ``` 块）
    m = re.search(r"(## 版本树\s*\n+)```.*?```", text, re.S)
    if m:
        return text[:m.start()] + m.group(1) + tree + text[m.end():]
    return text


def main():
    ap = argparse.ArgumentParser(description="Estrella Skill 发版工具")
    ap.add_argument("level", nargs="?", choices=["patch", "minor", "major"], help="版本递增级别")
    ap.add_argument("note", nargs="?", help="一句话版本说明（必填）")
    ap.add_argument("--files", default="", help="影响文件，逗号分隔")
    ap.add_argument("--show", action="store_true", help="只看当前版本")
    a = ap.parse_args()

    old = read_version()
    if a.show or not a.level:
        p("current version: v%s" % old)
        if not a.level:
            p("用法: python scripts/release.py patch|minor|major \"说明\"")
        return 0
    if not a.note:
        p("ERROR: 缺少版本说明（每版必须有说明）")
        return 2

    new = bump(old, a.level)
    today = datetime.date.today().isoformat()

    # 1) VERSION
    with open(os.path.join(ROOT, VER_FILE), "w", encoding="utf-8") as f:
        f.write(new + "\n")
    p("VERSION: v%s -> v%s" % (old, new))

    # 2) CHANGELOG 新条目
    fp = os.path.join(ROOT, LOG_FILE)
    text = open(fp, encoding="utf-8").read()
    files = [x.strip() for x in a.files.split(",") if x.strip()]
    block = [
        "## v%s · %s · %s" % (new, today, a.note),
        "",
        "**性质**：%s" % a.level,
        "",
        "**变更**",
        "1. %s" % a.note,
    ]
    if files:
        block += ["", "**影响文件**：" + "、".join(files)]
    block += ["", MARK, ""]
    entry = "\n".join(block)

    if MARK in text:
        text = text.replace(MARK, entry, 1)
    else:
        m = re.search(r"^## v[\d.]+ · ", text, re.M)
        text = text[:m.start()] + entry + "\n---\n\n" + text[m.start():] if m else entry + "\n" + text

    # 3) 重建 ASCII 树 + 当前版本行
    text = rebuild_tree(text)
    text = re.sub(r"\*\*当前版本：v[\d.]+（[\d-]+）\*\*",
                  "**当前版本：v%s（%s）**" % (new, today), text, count=1)

    with open(fp, "w", encoding="utf-8") as f:
        f.write(text)
    p("CHANGELOG: 已插入 v%s 条目并重建版本树" % new)

    # 4) SKILL.md 版本行
    sp = os.path.join(ROOT, SKILL_FILE)
    s = open(sp, encoding="utf-8").read()
    s2 = re.sub(r"\*\*版本：v[\d.]+（[\d-]+）\*\*",
                "**版本：v%s（%s）**" % (new, today), s, count=1)
    if s2 != s:
        with open(sp, "w", encoding="utf-8") as f:
            f.write(s2)
        p("SKILL.md: 版本行已更新")
    else:
        p("WARN: SKILL.md 未找到版本行，请手动加「**版本：v%s（%s）**」" % (new, today))

    p("")
    p("下一步：")
    p("  python scripts/smoke_test.py        # 含「版本一致性」检查")
    p("  set GH_TOKEN=xxx && python sync_github.py   # 提交信息自动带 v%s" % new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
