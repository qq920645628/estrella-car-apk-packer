#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_catalog.py — 从内置目录生成服务端 apk_catalog.json

服务端 /kb/apk_catalog_spec 每次请求读 $KB_DIR/apk_catalog.json（无缓存、无需重启），
所以「上传即生效」。以后新增 APK 版本只需：
    1) 改本脚本同目录 apk_catalog.py 的 CATALOG（或直接改服务端 JSON）
    2) python gen_catalog.py --version N+1
    3) 上传到 $KB_DIR（默认 /opt/estrella-kb/data/，不是 /opt/estrella-kb/！）
Skill 侧零改动，客户端 cloud → cache → builtin 三级回落自动生效。

用法：
  python scripts/gen_catalog.py                  # 生成到 ./apk_catalog.json
  python scripts/gen_catalog.py --out <path>     # 指定输出
  python scripts/gen_catalog.py --version 2      # spec_version +1（内容有变时）
"""
import os
import sys
import json
import argparse
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apk_catalog as C  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "apk_catalog.json"))
    ap.add_argument("--version", type=int, default=1, help="spec_version，目录内容有变时 +1")
    a = ap.parse_args()

    items = list(C.CATALOG)
    doc = {
        "spec_version": a.version,
        "updated": datetime.date.today().isoformat(),
        "note": "云端现成包目录；客户端按 id 合并，同 id 云端覆盖内置",
        "items": items,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print("WROTE", a.out)
    print("items =", len(items))
    print("ids   =", ", ".join(i["id"] for i in items))
    print("上传位置 = $KB_DIR/apk_catalog.json（默认 /opt/estrella-kb/data/，注意不是 /opt/estrella-kb/）")


if __name__ == "__main__":
    main()
