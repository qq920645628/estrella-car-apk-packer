#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Estrella 车机 APK 打包 Skill —— 脱敏与入库准备
纯标准库。

参数:
  --task-json <file>           原始任务记录（可能含敏感字段）
  --out <file>                 脱敏后输出路径（默认打印到 stdout）
  --commit                     脱敏后直接调用 local_kb.py upsert 入库

【值级脱敏】不仅按键名剔除，还扫描所有字符串值中的明文凭据（password=xxx、token、密钥文件等）
与用户绝对路径，彻底阻断凭据/路径泄露入库（修复审查 P0-4）。

对外导出:
  desensitize_record(rec) -> 已脱敏、白名单校验过的记录
  ALLOWED_KEYS / SCALAR_FIELDS -> 供 local_kb.py 合并逻辑复用，保证本地与线上同构
"""
import argparse
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# ---------- 白名单（车型知识库允许入场的字段，杜绝未知字段灌库） ----------
SCALAR_FIELDS = [
    "make", "model", "year", "factory_system_version", "android_base_version",
    "arch", "chip", "dpi", "screen", "root_status",
    "known_bugs", "permission_limits", "storage_limits", "bt_limits", "usb_limits", "other_limits",
    "result", "source", "artifact",
]
ALLOWED_KEYS = set(SCALAR_FIELDS + ["vehicle_key", "pitfalls", "solutions", "variants", "logs"])

PITFALL_KEYS = {"symptom", "root_cause", "severity", "fix_ref", "tags", "created_at", "source"}
SOLUTION_KEYS = {"problem", "steps", "stability_rating", "risk_level", "verified_count", "tags", "created_at", "source"}
VARIANT_KEYS = {"firmware_batch", "hardware_detail_delta", "source", "created_at"}

# 键名级别命中即剔除（签名/私钥/令牌相关）
SENSITIVE_KEYS = re.compile(
    r"(jks|keystore|password|passwd|private|\.pk8|\.p7b|secret|token|api[_-]?key|sign(?:[_-]?pass)?|alias|signature|auth)",
    re.I)

# 值级别：用户绝对路径 -> 占位（兼容 / 与 \ 两种分隔）
USER_PATH = re.compile(r"(?:[A-Za-z]:[\\/])?(?:Users|home|root)[\\/][^\\/\s:;,]{1,80}")
# 值级别：明文凭据 key=value 捕获值 -> 红框
SECRET_PAIR = re.compile(
    r"(?i)(\b(?:store[_-]?)?password\b|\bpasswd\b|\bsecret\b|\btoken\b|\bapi[_-]?key\b|"
    r"\bprivate[_-]?key\b|\bsign(?:[_-]?pass)?\b)\s*[:=]\s*['\"]?([^\s'\"<>]{3,})")
# 值级别：密钥文件名 -> 红框
SECRET_FILE = re.compile(r"(?i)\b[\w.\-/\\]{0,60}\.(jks|keystore|pk8|p7b)\b")


def _redact_str(s: str) -> str:
    out = USER_PATH.sub("<USER_HOME>", s)
    out = SECRET_PAIR.sub(lambda m: f"{m.group(1)}=<REDACTED>", out)
    out = SECRET_FILE.sub("<REDACTED_FILE>", out)
    # 若整个值就是敏感键名本身，整体红框
    if SENSITIVE_KEYS.search(out) and len(out) < 64:
        return "<REDACTED>"
    return out


def sanitize_value(v):
    if isinstance(v, str):
        return _redact_str(v)
    if isinstance(v, list):
        return [sanitize_value(x) for x in v]
    if isinstance(v, dict):
        return {k: sanitize_value(val) for k, val in v.items()}
    return v


def _clean_list(items, allowed):
    cleaned = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        cleaned.append({k: sanitize_value(v) for k, v in it.items() if k in allowed})
    return cleaned


def desensitize_record(rec: dict) -> dict:
    """白名单 + 值级脱敏，返回可安全入库的记录。"""
    out = {}
    for k, v in (rec or {}).items():
        if k not in ALLOWED_KEYS:
            continue
        if SENSITIVE_KEYS.search(k):
            continue
        out[k] = sanitize_value(v)
    # 结构化子表同样走白名单+值级
    out["pitfalls"] = _clean_list(out.get("pitfalls"), PITFALL_KEYS)
    out["solutions"] = _clean_list(out.get("solutions"), SOLUTION_KEYS)
    out["variants"] = _clean_list(out.get("variants"), VARIANT_KEYS)
    # 顶层 variant 字段（firmware_batch/hardware_detail_delta）收编进 variants
    if ("firmware_batch" in out or "hardware_detail_delta" in out) and not out.get("variants"):
        out["variants"] = [{
            "firmware_batch": out.get("firmware_batch"),
            "hardware_detail_delta": out.get("hardware_detail_delta"),
        }]
    out.setdefault("source", "unknown")
    return out


def commit(record):
    import subprocess
    tmp = os.path.join(SCRIPT_DIR, "_desensitized_tmp.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "local_kb.py"), "upsert", "--json", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout)
    if r.stderr:
        print(r.stderr, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Estrella 脱敏与入库准备")
    ap.add_argument("--task-json", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    with open(args.task_json, "r", encoding="utf-8") as f:
        rec = json.load(f)
    clean = desensitize_record(rec)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        print(json.dumps({"status": "desensitized", "out": os.path.abspath(args.out),
                          "vehicle_key": clean.get("vehicle_key")}, ensure_ascii=False))
    else:
        print(json.dumps(clean, ensure_ascii=False, indent=2))

    if args.commit:
        commit(clean)


if __name__ == "__main__":
    main()
