#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Estrella Skill 冒烟测试（纯标准库，离线可跑）

改动脚本后先跑这个，再同步 GitHub。检查项：
  1. 全部脚本语法可编译
  2. 全部脚本 --help 可正常退出
  3. local_kb 本地后端：upsert -> query -> stats 往返一致
  4. 离线补交队列：写盘 -> flush 出队
  5. apk_catalog 候选匹配（内置目录，不联网）
  6. 产物命名规范
  7. 版本一致性：VERSION / SKILL.md 版本行 / CHANGELOG.md（当前版本行 + 该版本条目）三处必须一致
用法： python scripts/smoke_test.py
"""
import json
import os
import py_compile
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
results = []


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, "EXC: %s" % e
    results.append((name, ok, detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" — " + detail) if detail else ""))


def run(args, env=None, cwd=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run([PY] + args, capture_output=True, cwd=cwd or HERE, env=e, timeout=180)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


# 1) 语法
def t_compile():
    bad = []
    for f in sorted(os.listdir(HERE)):
        if f.endswith(".py"):
            try:
                py_compile.compile(os.path.join(HERE, f), doraise=True)
            except Exception as e:
                bad.append("%s: %s" % (f, e))
    return (not bad), ("; ".join(bad) if bad else "%d 个脚本" % len([f for f in os.listdir(HERE) if f.endswith('.py')]))


# 2) --help
def t_help():
    bad = []
    for f in ["local_kb.py", "apk_catalog.py", "preflight.py", "build_apk.py",
              "setup_env.py", "pull_source.py", "snapshot.py", "verify.py", "desensitize.py"]:
        rc, out, err = run([f, "--help"])
        if rc != 0:
            bad.append(f)
    return (not bad), ("失败: " + ",".join(bad) if bad else "9 个脚本 help 正常")


# 3) local_kb 本地往返
def t_roundtrip():
    d = tempfile.mkdtemp(prefix="kb-smoke-")
    env = {"ESTRELLA_KB_DIR": d}
    rec = {"vehicle_key": "冒烟测试车|2026|1.0|9.0", "model": "冒烟测试车", "year": "2026",
           "android_base_version": "9.0", "arch": "arm64", "result": "success",
           "solutions": [{"problem": "冒烟", "steps": "无"}]}
    rc, out, err = run(["local_kb.py", "--backend", "local", "upsert", "--json", json.dumps(rec, ensure_ascii=False)], env)
    if rc != 0:
        return False, "upsert rc=%d %s" % (rc, err[:200])
    rc, out, err = run(["local_kb.py", "--backend", "local", "query", "--key", "冒烟测试车|2026|1.0|9.0"], env)
    if rc != 0 or "冒烟测试车" not in out:
        return False, "query 未命中 %s" % err[:200]
    rc, out, err = run(["local_kb.py", "--backend", "local", "stats"], env)
    if rc != 0 or "车型总量" not in out:
        return False, "stats 无输出"
    return True, "upsert/query/stats 往返一致"


# 4) 离线补交队列
def t_pending():
    d = tempfile.mkdtemp(prefix="kb-pend-")
    env = {"ESTRELLA_KB_DIR": d, "ESTRELLA_KB_URL": "http://127.0.0.1:1"}  # 指向死端口模拟离线
    rec = {"vehicle_key": "离线测试车|2026|1.0|9.0", "model": "离线测试车", "android_base_version": "9.0"}
    rc, out, err = run(["local_kb.py", "upsert", "--json", json.dumps(rec, ensure_ascii=False)], env)
    pf = os.path.join(d, "pending_queue.jsonl")
    if not os.path.exists(pf):
        return False, "离线未落待补交队列"
    n = len([l for l in open(pf, encoding="utf-8") if l.strip()])
    return True, "离线落队 %d 条（联网后自动补交）" % n


# 5) apk_catalog
def t_catalog():
    env = {"ESTRELLA_NO_CLOUD": "1"}
    rc, out, err = run(["apk_catalog.py", "--android", "9.0", "--no-source"], env)
    if rc != 0:
        return False, "rc=%d %s" % (rc, err[:200])
    return True, "安卓9.0 候选匹配正常"


# 6) 产物命名
def t_naming():
    sys.path.insert(0, HERE)
    import build_apk as b
    import types
    tmp = os.path.join(tempfile.gettempdir(), "_smoke.apk")
    with open(tmp, "wb") as f:
        f.write(b"0" * (12 * 1048576))
    try:
        name = b.canonical_name(
            types.SimpleNamespace(module="automotive", flavor="universal",
                                  version_code=1301, version_name="1.3.0"),
            tmp, {"versionCode": "1301", "versionName": "1.3.0"}, "C:/nonexistent")
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    ok = name.startswith("automotive-universal-a28-v1.3.0-vc1301-") and name.endswith(".apk")
    return ok, name


# 7) 版本一致性（版本树：每版都要有说明）
def t_version():
    root = os.path.dirname(HERE)
    vfp = os.path.join(root, "VERSION")
    if not os.path.exists(vfp):
        return False, "缺少 VERSION 文件"
    ver = open(vfp, encoding="utf-8").read().strip()
    import re
    if not re.fullmatch(r"\d+\.\d+\.\d+", ver):
        return False, "VERSION 格式非 x.y.z: %r" % ver
    skill = os.path.join(root, "SKILL.md")
    log = os.path.join(root, "CHANGELOG.md")
    bad = []
    if os.path.exists(skill):
        if not re.search(r"\*\*版本：v%s（" % re.escape(ver), open(skill, encoding="utf-8").read()):
            bad.append("SKILL.md 版本行")
    else:
        bad.append("缺 SKILL.md")
    if os.path.exists(log):
        t = open(log, encoding="utf-8").read()
        if not re.search(r"\*\*当前版本：v%s（" % re.escape(ver), t):
            bad.append("CHANGELOG 当前版本行")
        if not re.search(r"^## v%s · " % re.escape(ver), t, re.M):
            bad.append("CHANGELOG 缺 v%s 说明条目" % ver)
    else:
        bad.append("缺 CHANGELOG.md")
    return (not bad), ("不一致: " + ", ".join(bad) if bad else "v%s 三处一致且有说明" % ver)


for n, f in [("脚本语法编译", t_compile), ("脚本 --help", t_help),
             ("local_kb 本地往返", t_roundtrip), ("离线补交队列", t_pending),
             ("现成包目录匹配", t_catalog), ("产物命名规范", t_naming),
             ("版本一致性", t_version)]:
    check(n, f)

failed = [r for r in results if not r[1]]
print("\n%d/%d 通过" % (len(results) - len(failed), len(results)))
sys.exit(1 if failed else 0)
