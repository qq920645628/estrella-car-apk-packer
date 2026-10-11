#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Estrella 车机 APK 打包 Skill —— 任务快照与一键回滚
纯标准库。

子命令:
  snapshot <src_root> <tag>    修改前对源码工程做快照（排除 build/.gradle）
  rollback <tag> [--target <dir>]  从快照恢复（排除 build/.gradle）
  list                         列出所有快照
  clean <tag>                  删除指定快照

快照存储: ESTRELLA_SNAPSHOT_DIR 或 <skill_dir>/snapshots/
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SNAP_DIR = os.path.join(SCRIPT_DIR, "..", "snapshots")
IGNORE = shutil.ignore_patterns("build", ".gradle", ".git", "node_modules")


def snap_dir() -> str:
    d = os.environ.get("ESTRELLA_SNAPSHOT_DIR") or DEFAULT_SNAP_DIR
    os.makedirs(d, exist_ok=True)
    return os.path.abspath(d)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_snapshot(src, tag):
    src = os.path.abspath(src)
    if not os.path.isdir(src):
        print(json.dumps({"status": "error", "reason": f"源目录不存在: {src}"}, ensure_ascii=False))
        sys.exit(1)
    dest = os.path.join(snap_dir(), f"snapshot-{tag}")
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=IGNORE)
    meta = {"tag": tag, "src": src, "created_at": _now(),
            "size_files": sum(len(files) for _, _, files in os.walk(dest))}
    with open(os.path.join(snap_dir(), f"snapshot-{tag}.meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps({"status": "snapshotted", "tag": tag, "dest": dest, "meta": meta}, ensure_ascii=False))


def cmd_rollback(tag, target):
    dest = os.path.join(snap_dir(), f"snapshot-{tag}")
    if not os.path.isdir(dest):
        print(json.dumps({"status": "error", "reason": f"快照不存在: {tag}"}, ensure_ascii=False))
        sys.exit(1)
    meta = json.load(open(os.path.join(snap_dir(), f"snapshot-{tag}.meta.json"), encoding="utf-8"))
    tgt = os.path.abspath(target) if target else meta.get("src")
    if not tgt or not os.path.isdir(tgt):
        print(json.dumps({"status": "error", "reason": f"目标目录不存在: {tgt}"}, ensure_ascii=False))
        sys.exit(1)
    shutil.copytree(dest, tgt, ignore=IGNORE, dirs_exist_ok=True)
    print(json.dumps({"status": "rolled_back", "tag": tag, "target": tgt,
                      "note": "已恢复源码(排除 build/.gradle)，未覆盖生成产物"}, ensure_ascii=False))


def cmd_list():
    d = snap_dir()
    for name in sorted(os.listdir(d)):
        if name.endswith(".meta.json"):
            m = json.load(open(os.path.join(d, name), encoding="utf-8"))
            print(f"{m['tag']}  src={m['src']}  created={m['created_at']}  files={m.get('size_files')}")


def cmd_clean(tag):
    dest = os.path.join(snap_dir(), f"snapshot-{tag}")
    meta = os.path.join(snap_dir(), f"snapshot-{tag}.meta.json")
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    if os.path.exists(meta):
        os.remove(meta)
    print(json.dumps({"status": "cleaned", "tag": tag}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description="Estrella 任务快照/回滚")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("snapshot"); p.add_argument("src"); p.add_argument("tag")
    p = sub.add_parser("rollback"); p.add_argument("tag"); p.add_argument("--target")
    sub.add_parser("list")
    p = sub.add_parser("clean"); p.add_argument("tag")
    args = ap.parse_args()
    if args.cmd == "snapshot":
        cmd_snapshot(args.src, args.tag)
    elif args.cmd == "rollback":
        cmd_rollback(args.tag, args.target)
    elif args.cmd == "list":
        cmd_list()
    elif args.cmd == "clean":
        cmd_clean(args.tag)


if __name__ == "__main__":
    main()
