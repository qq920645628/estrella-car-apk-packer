#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sync the live Skill into this publish directory and push it to GitHub.

Why this exists: on this machine the `git` binary cannot reach GitHub at all
(local proxy breaks schannel and openssl alike), so every push goes through the
REST API via api_push.py. This script does the whole job in one command:

  copy SKILL.md + scripts/ + references/ + assets/   (data/ is NOT synced)
  scrub the real KB token -> <YOUR_KB_TOKEN>
  drop files that no longer exist in the Skill
  skip the push entirely if nothing changed
  otherwise commit + move refs/heads/main

Usage (CMD):
    set GH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
    python sync_github.py
Usage (PowerShell):
    $env:GH_TOKEN = 'ghp_xxxxxxxxxxxxxxxxxxxx'
    python sync_github.py

Options:
    --src <dir>   Skill source directory (default: ~/.workbuddy/skills/estrella-car-apk-packer)
    --dry         Sync + scrub + diff only, never push
    --force       Push even when nothing changed
"""
import base64
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

OWNER = "qq920645628"
REPO = "estrella-car-apk-packer"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills", "estrella-car-apk-packer")
PLACEHOLDER = "<YOUR_KB_TOKEN>"
SYNC_DIRS = ["scripts", "references", "assets"]
SYNC_FILES = ["SKILL.md"]
SKIP_NAMES = {"__pycache__", ".DS_Store", "*.pyc"}

CTX = ssl.create_default_context()
HEX40 = re.compile(r"\b[0-9a-f]{40}\b")


def api(path, method="GET", payload=None, token=""):
    url = "https://api.github.com/repos/%s/%s%s" % (OWNER, REPO, path)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", "Bearer " + token)
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("User-Agent", "estrella-sync")
    if data:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, context=CTX) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def main():
    args = sys.argv[1:]
    src = DEFAULT_SRC
    dry = "--dry" in args
    force = "--force" in args
    if "--src" in args:
        src = args[args.index("--src") + 1]
    token = os.environ.get("GH_TOKEN", "")
    if not os.path.isdir(src):
        sys.exit("Skill source not found: %s" % src)
    if not dry and not force and not token:
        sys.exit("GH_TOKEN not set (needed to push). Use --dry to preview only.")

    # ---- 1. copy the synced subsets -------------------------------------
    copied, removed = [], []

    def do_copy(rel_src, rel_dst):
        s = os.path.join(src, rel_src)
        d = os.path.join(HERE, rel_dst)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        with open(s, "rb") as f:
            raw = f.read()
        with open(d, "rb") as f:
            old = f.read() if os.path.exists(d) else None
        if old != raw:
            with open(d, "wb") as f:
                f.write(raw)
            copied.append(rel_dst)

    for name in SYNC_FILES:
        if os.path.exists(os.path.join(src, name)):
            do_copy(name, name)
    for sub in SYNC_DIRS:
        sdir = os.path.join(src, sub)
        ddir = os.path.join(HERE, sub)
        if not os.path.isdir(sdir):
            continue
        os.makedirs(ddir, exist_ok=True)
        present = set()
        for root, dirs, names in os.walk(sdir):
            dirs[:] = [x for x in dirs if x not in SKIP_NAMES]
            for n in names:
                if n in SKIP_NAMES or n.endswith(".pyc"):
                    continue
                rel = os.path.relpath(os.path.join(root, n), sdir).replace("\\", "/")
                present.add(rel)
                do_copy(os.path.join(sub, rel), os.path.join(sub, rel))
        for root, dirs, names in os.walk(ddir):
            for n in names:
                rel = os.path.relpath(os.path.join(root, n), ddir).replace("\\", "/")
                if rel not in present:
                    os.remove(os.path.join(root, n))
                    removed.append(os.path.join(sub, rel))

    print("synced: %d changed, %d removed" % (len(copied), len(removed)))
    for c in copied:
        print("   ~ %s" % c.replace("\\", "/"))
    for r in removed:
        print("   - %s" % r.replace("\\", "/"))

    # ---- 2. scrub KB token ----------------------------------------------
    targets = []
    for sub in SYNC_DIRS:
        ddir = os.path.join(HERE, sub)
        for root, dirs, names in os.walk(ddir):
            for n in names:
                if n.endswith((".py", ".md")):
                    targets.append(os.path.join(root, n))
    if os.path.exists(os.path.join(HERE, "SKILL.md")):
        targets.append(os.path.join(HERE, "SKILL.md"))

    freq = {}
    for t in targets:
        with open(t, "r", encoding="utf-8", errors="replace") as f:
            for m in HEX40.findall(f.read()):
                freq[m] = freq.get(m, 0) + 1
    scrubbed = 0
    for tok, hits in sorted(freq.items(), key=lambda kv: -kv[1]):
        if tok == PLACEHOLDER or hits < 1:
            continue
        for t in targets:
            with open(t, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
            if tok in txt:
                with open(t, "w", encoding="utf-8") as f:
                    f.write(txt.replace(tok, PLACEHOLDER))
                scrubbed += 1
        print("scrubbed token %s... -> %s (%d files)" % (tok[:8], PLACEHOLDER, scrubbed))
        break
    if not scrubbed:
        print("scrub: nothing to do")

    # ---- 3. diff against remote -----------------------------------------
    if dry:
        print("dry run: stop before push")
        return 0
    if not token:
        print("no GH_TOKEN, stop before push")
        return 1

    remote = {}
    try:
        tree = api("/git/trees/main?recursive=1", token=token)
        for e in tree.get("tree", []):
            if e["type"] == "blob":
                remote[e["path"]] = e["sha"]
    except urllib.error.HTTPError as e:
        print("cannot read remote tree (%s), will push anyway" % e.code)

    local = {}
    for root, dirs, names in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for n in names:
            p = os.path.join(root, n)
            rel = os.path.relpath(p, HERE).replace("\\", "/")
            with open(p, "rb") as f:
                local[rel] = blob_sha(f.read())

    diff = [p for p, s in local.items() if remote.get(p) != s]
    gone = [p for p in remote if p not in local]
    print("remote diff: %d changed/new, %d deleted" % (len(diff), len(gone)))
    for p in sorted(diff)[:20]:
        print("   %s %s" % ("+" if p not in remote else "~", p))
    for p in sorted(gone)[:20]:
        print("   - %s" % p)

    if not diff and not gone and not force:
        print("nothing to push, GitHub is already up to date")
        return 0

    # ---- 4. push via REST API -------------------------------------------
    print("pushing ...")
    env = dict(os.environ, GH_TOKEN=token)
    rc = subprocess.call([sys.executable, os.path.join(HERE, "api_push.py")], env=env)
    if rc != 0:
        print("push failed")
        return rc
    print("DONE https://github.com/%s/%s" % (OWNER, REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
