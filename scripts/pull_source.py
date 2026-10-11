#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pull_source.py —— 从云端拉取 DiPlay-Universal 源码到本机（出包在本地完成）

适用场景：用户本机没有源码工程时，由 Skill 调本脚本从 MCP 服务端下载源码快照，
解包到 ASCII 路径（避开 AGP 拒绝非 ASCII 工程路径的老坑），供后续本地编译出包。

设计原则：
- 零第三方依赖（仅标准库），可在任意用户机器直接跑。
- 服务器只「存」源码、不编译；出包（gradle assemble）必须在本机。
- 下载后做 sha256 完整性校验（与 /kb/source/meta 返回的哈希比对）。
- 不下载签名密钥（服务端打包时已排除 *.jks / keystore.properties），
  因此本机拉到的源码默认只能出 debug 包；如需同签名覆盖升级，用户自行把 keystore 放到 release/ 目录。

用法：
  python3 pull_source.py                       # 默认拉到 ~/DiPlayBuild/DiPlay-Universal
  python3 pull_source.py --dir D:/DiPlayBuild  # 指定父目录（会解出 DiPlay-Universal 子目录）
  python3 pull_source.py --no-verify           # 跳过 sha256 校验（不推荐）
  python3 pull_source.py --url http://host:7799 --token xxxx
环境变量：
  ESTRELLA_KB_URL     默认 http://106.12.162.224:7799
  ESTRELLA_KB_TOKEN   鉴权令牌
"""
import os
import sys
import json
import hashlib
import tarfile
import argparse
import urllib.request
import urllib.error

DEFAULT_URL = os.environ.get("ESTRELLA_KB_URL", "http://106.12.162.224:7799")
DEFAULT_TOKEN = os.environ.get("ESTRELLA_KB_TOKEN", "<YOUR_KB_TOKEN>")
SNAP_NAME = "diplay-src.tar.gz"

_JSON_MODE = False  # --json 时人类可读信息走 stderr，stdout 仅输出 JSON（机器可解析）


def say(msg):
    if _JSON_MODE:
        print(msg, file=sys.stderr)
    else:
        print(msg)


def _http_get(url, token, stream=False):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    return urllib.request.urlopen(req, timeout=120)  # noqa: S310 - 自有服务


def fetch_meta(base, token):
    try:
        with _http_get(base.rstrip("/") + "/kb/source/meta", token) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # 元数据取不到不致命，仅无法校验
        print("⚠️ 无法获取源码元数据（%s），将跳过哈希校验提示（仍会下载并尽力校验）" % e)
        return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, token, dest):
    with _http_get(url, token) as r:
        total = int(r.headers.get("Content-Length", "0") or "0")
        written = 0
        with open(dest, "wb") as f:
            while True:
                buf = r.read(1 << 20)
                if not buf:
                    break
                f.write(buf)
                written += len(buf)
                if total and not _JSON_MODE:
                    pct = written * 100 // total
                    sys.stdout.write("\r    下载中 %d/%d (%d%%)" % (written, total, pct))
                    sys.stdout.flush()
        if total and not _JSON_MODE:
            sys.stdout.write("\n")
    return written


def extract(tar_path, parent_dir):
    out = os.path.join(parent_dir, "DiPlay-Universal")
    os.makedirs(parent_dir, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tf:
        # 防路径穿越：只接受常规条目
        members = [m for m in tf.getmembers() if m.name.replace("\\", "/").lstrip("/").split("/")[0] == "DiPlay-Universal"]
        tf.extractall(parent_dir, members=members, filter="data")  # noqa: S202 - 已过滤
    return out


def main():
    ap = argparse.ArgumentParser(description="从云端拉取 DiPlay 源码到本机（本地出包用）")
    ap.add_argument("--dir", help="目标父目录（会解出 DiPlay-Universal 子目录），默认 ~/DiPlayBuild")
    ap.add_argument("--url", default=DEFAULT_URL, help="MCP 服务地址")
    ap.add_argument("--token", default=DEFAULT_TOKEN, help="鉴权令牌")
    ap.add_argument("--no-verify", action="store_true", help="跳过 sha256 校验")
    ap.add_argument("--keep-tar", action="store_true", help="保留 tarball（默认解包后删除）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    parent = os.path.abspath(os.path.expanduser(args.dir)) if args.dir else \
        os.path.abspath(os.path.join(os.path.expanduser("~"), "DiPlayBuild"))
    os.makedirs(parent, exist_ok=True)  # 先确保父目录存在，避免下载写文件失败
    tar_path = os.path.join(parent, SNAP_NAME)

    try:
        global _JSON_MODE
        _JSON_MODE = args.json
        meta = fetch_meta(args.url, args.token)
        url = args.url.rstrip("/") + "/kb/source"
        say("=" * 60)
        say(" 从云端拉取 DiPlay 源码（出包请在本地完成）")
        say("=" * 60)
        if meta:
            say("  快照：%s  (%d 字节)" % (meta.get("name"), meta.get("size", 0)))
            say("  说明：%s" % meta.get("note", ""))
        say("  目标：%s" % parent)
        say("  下载：%s" % url)

        downloaded = download(url, args.token, tar_path)
        say("  ✓ 下载完成：%s (%d 字节)" % (tar_path, downloaded))

        if not args.no_verify:
            calc = sha256_file(tar_path)
            expect = (meta or {}).get("sha256")
            if expect:
                if calc.lower() == expect.lower():
                    say("  ✓ sha256 校验一致：%s" % calc)
                else:
                    say("  ✗ sha256 不一致！期望 %s 实得 %s" % (expect, calc))
                    say("    源码可能被篡改或下载损坏，已中止解包。删除 %s 后重试。" % tar_path)
                    return 1
            else:
                say("  ⚠️ 无期望哈希可比对，本地 sha256=%s（已下载但无法确认完整性）" % calc)

        project = extract(tar_path, parent)
        if not args.keep_tar:
            try:
                os.remove(tar_path)
            except OSError:
                pass
        say("  ✓ 已解包到：%s" % project)
        say("  ➡️ 下一步：确保本机已部署 JDK17+SDK+NDK（setup_env），再执行 build_apk 本地出包。")
        if args.json:
            print(json.dumps({"ok": True, "project": project, "size": downloaded}, ensure_ascii=False, indent=2))
        return 0
    except urllib.error.HTTPError as e:
        msg = "HTTP %d：%s" % (e.code, e.read().decode("utf-8", "replace")[:200])
        say("  ✗ 拉取失败：%s" % msg)
        if args.json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False, indent=2))
        return 1
    except Exception as e:
        say("  ✗ 拉取失败：%s" % e)
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
