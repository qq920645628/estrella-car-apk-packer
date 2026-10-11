#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Push a local directory to a GitHub repo using only the REST API (no git binary).

Needed here because the machine sits behind a local HTTP proxy that git's
schannel/openssl backends cannot negotiate, while curl/urllib work fine.
"""
import base64
import json
import os
import ssl
import sys
import urllib.request

OWNER = "qq920645628"
REPO = "estrella-car-apk-packer"
TOKEN = os.environ.get("GH_TOKEN", "")
ROOT = r"C:\Users\Administrator\estrella-publish"
SKIP = {".git", "__pycache__", ".workbuddy"}
EXCLUDE_FILES = set()

if not TOKEN:
    sys.exit("GH_TOKEN not set")

BASE = "https://api.github.com/repos/%s/%s" % (OWNER, REPO)
CTX = ssl.create_default_context()


def req(method, path, payload=None):
    url = BASE + path if path.startswith("/") else path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", "Bearer " + TOKEN)
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("User-Agent", "estrella-publish")
    r.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, context=CTX) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


# 1) collect files
files = []
for root, dirs, names in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP]
    for n in names:
        if n in EXCLUDE_FILES:
            continue
        p = os.path.join(root, n)
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        files.append((rel, p))
files.sort()
print("files: %d" % len(files))

# 1.5) GitHub's git-data API refuses to run on an empty repo ("Git Repository is empty").
# Create the initial commit through the contents API first (README), then switch to
# the tree API so the remaining files land in a single clean commit.
def bootstrap():
    with open(os.path.join(ROOT, "README.md"), "rb") as f:
        content = base64.b64encode(f.read()).decode("ascii")
    try:
        out = req("PUT", "/contents/README.md", {
            "message": "Initial commit",
            "content": content,
            "branch": "main",
        })
        return out["commit"]["sha"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 422 or "already exists" in body or "sha" in body.lower():
            r = req("GET", "/commits/main")
            return r["sha"]
        raise

base_sha = bootstrap()
print("bootstrap commit: %s" % base_sha)

# 2) blobs
tree = []
for rel, p in files:
    with open(p, "rb") as f:
        raw = f.read()
    is_text = True
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        is_text = False
    if is_text:
        blob = req("POST", "/git/blobs", {"content": raw.decode("utf-8"), "encoding": "utf-8"})
    else:
        blob = req("POST", "/git/blobs", {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"})
    mode = "100755" if rel.endswith((".sh", ".bat")) else "100644"
    tree.append({"path": rel, "mode": mode, "type": "blob", "sha": blob["sha"]})
    print("  blob %-45s %s" % (rel, blob["sha"][:8]))

# 3) tree (nested paths are expanded by GitHub automatically)
t = req("POST", "/git/trees", {"tree": tree})
print("tree: %s" % t["sha"])

# 4) commit (no parent: repo was created empty)
c = req("POST", "/git/commits", {
    "message": (
        "Estrella Car APK Packer: per-vehicle CarPlay/head-unit APK skill (bilingual README, sanitized)\n\n"
        "2026-10-11:\n"
        "- self-owned device: all permissions open by default (root/su/firmware/system-partition "
        "are NOT blocked); only boundary = devices you don't own\n"
        "- verify.py: su/Runtime.exec/ProcessBuilder/mount scan downgraded WARNING -> INFO (never blocks)\n"
        "- new references/root-adapt-without-injection.md (system-side script / shim / call-site swap)\n"
        "- scripts/gen_catalog.py: generate $KB_DIR/apk_catalog.json for cloud-served catalogue\n"
    ),
    "tree": t["sha"],
    "parents": [base_sha],
})
print("commit: %s" % c["sha"])

# 5) point main at it
try:
    req("POST", "/git/refs", {"ref": "refs/heads/main", "sha": c["sha"]})
    print("ref created: refs/heads/main")
except urllib.error.HTTPError as e:
    if e.code == 422:  # ref exists -> update instead
        req("PATCH", "/git/refs/heads/main", {"sha": c["sha"], "force": True})
        print("ref updated: refs/heads/main")
    else:
        raise

print("DONE https://github.com/%s/%s" % (OWNER, REPO))
