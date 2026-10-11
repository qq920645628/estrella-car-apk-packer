#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Estrella 车机 APK 打包 Skill —— 车型知识库客户端（已上线 MCP 后端，本地缓存兜底）

纯标准库，无外部依赖。

数据主键: vehicle_key = 车型|年份|车机系统版本|安卓底层版本

【统一数据模型】本地与线上采用完全一致的单文件结构，确保本地缓存可直接导入 MCP、导出格式互通：
  kb.json = { "vehicles": { key: 车型记录(内嵌 pitfalls/solutions/variants) }, "conflicts": [...], "meta": {...} }

【自我进化】服务器为进化大脑：每次任务的成败会被记录，方案按胜率加权排序；同款车不同车机参数
（变体签名不同）会被自动拆分，禁止死板复用历史修复；失败会自动生成“负向教训”坑。

后端:
  --backend mcp   (默认) 调用线上 MCP 服务 (http://106.12.162.224:7799)，不可达时回退本地缓存
  --backend local        仅用本地 JSON 缓存 (<skill_dir>/data/kb.json)

子命令:
  query   --key K            查询车型记录(含坑/方案)
  upsert  --json F           写入/合并脱敏任务记录（冲突进队列，禁止覆盖；自动区分车机变体）
  recommend --json F         进化推荐：传入 vehicle_key + params，返回变体不匹配告警与按成败加权的方案
  report_outcome --json F    反馈闭环：回报某方案成败，更新计数；失败自动生成负向教训坑
  list                     列出全部 vehicle_key
  export  --format csv|json [--out F]   导出全部缓存
  stats                    输出统计看板（含进化维度：教训数/变体拆分/累计成败）
  backup                   全量备份至本地 backups/<时间戳>/
  resolve --conflict-id ID [--action apply_incoming|keep_existing]   消费冲突队列
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

try:
    from desensitize import desensitize_record, ALLOWED_KEYS, SCALAR_FIELDS
except ImportError:  # 允许直接以脚本目录运行
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from desensitize import desensitize_record, ALLOWED_KEYS, SCALAR_FIELDS

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")

# 🔒 锁定 MCP 通信常量（与 references/locked-config.md 一致；环境变量仅用于轮换）
MCP_KB_BASE_URL = os.environ.get("ESTRELLA_KB_URL", "http://106.12.162.224:7799")
MCP_KB_TOKEN = os.environ.get("ESTRELLA_KB_TOKEN", "<YOUR_KB_TOKEN>")
DEFAULT_BACKEND = os.environ.get("ESTRELLA_KB_BACKEND", "mcp")


def data_dir() -> str:
    d = os.environ.get("ESTRELLA_KB_DIR") or DEFAULT_DATA_DIR
    d = os.path.abspath(d)
    os.makedirs(d, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- 进化内核（与 server.js 一致） ----------------
def normalize_key(key):
    """F：车型名归一，防止『逸动PLUS / 逸动 plus / 逸动Plus』分裂建库。仅车型段去全部空白并小写。"""
    if not key:
        return key
    parts = str(key).split("|")
    out = []
    for i, p in enumerate(parts):
        s = str(p).strip().lower()
        if i == 0:
            s = re.sub(r"\s+", "", s)
        out.append(s)
    return "|".join(out)


def major_of(v):
    """G：安卓大版本号提取，『9.0』『Android 11』『11』均映射为 9 / 11。"""
    if v is None:
        return None
    m = re.search(r"\d+", str(v))
    return int(m.group(0)) if m else None


def variant_sig(rec) -> str:
    def norm(v):
        if isinstance(v, list):
            # 与 server.js 一致：数组 sort 后以逗号拼接（JS Array.join 默认逗号）
            return ",".join(sorted(str(x) for x in v))
        return "" if v is None else str(v)
    parts = [
        norm(rec.get("arch")), norm(rec.get("chip")), norm(rec.get("dpi")),
        norm(rec.get("root_status")), norm(rec.get("android_base_version")),
        norm(rec.get("factory_system_version")), norm(rec.get("storage_limits")),
        norm(rec.get("permission_limits")), norm(rec.get("bt_limits")),
        norm(rec.get("usb_limits")), norm(rec.get("known_bugs")),
    ]
    return hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()[:16]


def confidence_of(s) -> float:
    ok = s.get("success_count", 0) or 0
    bad = s.get("fail_count", 0) or 0
    n = ok + bad
    if n == 0:
        return 0.5
    rate = ok / n
    credibility = n / (n + 3)
    return round(rate * credibility, 2)


def similarity(a, b) -> float:
    score = 0.0
    if a.get("android_base_version") and a.get("android_base_version") == b.get("android_base_version"):
        score += 0.35
    if a.get("arch") and a.get("arch") == b.get("arch"):
        score += 0.15
    if a.get("chip") and b.get("chip") and a.get("chip") == b.get("chip"):
        score += 0.15

    def ov(x, y):
        xs, ys = set(x or []), set(y or [])
        if not xs and not ys:
            return 0.0
        inter = sum(1 for v in xs if v in ys)
        return (2 * inter / (len(xs) + len(ys))) if (xs or ys) else 0.0
    score += 0.15 * ov(a.get("storage_limits"), b.get("storage_limits"))
    score += 0.10 * ov(a.get("permission_limits"), b.get("permission_limits"))
    score += 0.10 * ov(a.get("known_bugs"), b.get("known_bugs"))
    return round(min(1.0, score), 2)


def seed_solution(sl, rec=None):
    sl = dict(sl)
    sl.setdefault("success_count", 0)
    sl.setdefault("fail_count", 0)
    sl["last_tried"] = sl.get("last_tried")
    sl["last_outcome"] = sl.get("last_outcome")
    sl["confidence"] = confidence_of(sl)
    sl.setdefault("created_at", _now())
    sl.setdefault("source", (rec or {}).get("source", "unknown"))
    return sl


def _load_kb():
    p = os.path.join(data_dir(), "kb.json")
    if not os.path.exists(p):
        return {"vehicles": {}, "conflicts": [], "meta": {"created_at": _now()}}
    try:
        with open(p, "r", encoding="utf-8") as f:
            kb = json.load(f)
    except Exception:
        return {"vehicles": {}, "conflicts": [], "meta": {"created_at": _now()}}
    kb.setdefault("vehicles", {})
    kb.setdefault("conflicts", [])
    kb.setdefault("meta", {})
    return kb


def _save_kb(kb):
    p = os.path.join(data_dir(), "kb.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _read_json_file(path):
    """--json 既可传 JSON 字符串（以 { 或 [ 开头），也可传文件路径。"""
    s = (path or "").strip()
    if s and (s.startswith("{") or s.startswith("[")):
        try:
            return json.loads(s)
        except Exception:  # noqa: BLE001
            pass
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------- 合并 / 去重 / 冲突 / 变体拆分（本地与线上同一套语义） ----------------
def _merge_into(kb, rec):
    """把一条（已脱敏、白名单校验过）记录合并进 kb。返回回执列表。
    语义：禁止覆盖——只追加缺失的坑/方案/变体；同问题不同步骤进冲突队列；同款车不同车机参数自动拆分变体。"""
    key = normalize_key(rec.get("vehicle_key"))
    sig = variant_sig(rec)
    receipts = []
    existing = kb["vehicles"].get(key)

    if not existing:
        vehicle = dict(rec)
        vehicle.setdefault("pitfalls", [])
        vehicle["solutions"] = [seed_solution(s, rec) for s in (vehicle.get("solutions") or [])]
        vlist = rec.get("variants") or []
        vehicle["variants"] = [
            dict(v, variant_sig=variant_sig({**rec, **v})) for v in vlist
        ] if vlist else [{"variant_sig": sig, "firmware_batch": rec.get("firmware_batch", "默认"), "hardware_detail_delta": rec.get("hardware_detail_delta", {})}]
        vehicle["variant_sigs"] = [v["variant_sig"] for v in vehicle["variants"]]
        vehicle["variant_split"] = len(vehicle["variant_sigs"]) > 1
        vehicle["access_count"] = 1
        vehicle["created_at"] = _now()
        vehicle["updated_at"] = _now()
        kb["vehicles"][key] = vehicle
        receipts.append("成功入库：新增车型记录（变体签名 %s）" % sig)
        return receipts, False

    # 已存在：touch
    existing["access_count"] = existing.get("access_count", 0) + 1
    existing["updated_at"] = _now()

    # 标量字段仅填空（绝不覆盖已有有效值）
    for f in SCALAR_FIELDS:
        if not existing.get(f) and rec.get(f) not in (None, ""):
            existing[f] = rec[f]

    # G. 安卓大版本漂移监控：跨大版本则告警，绝不覆盖基线
    base_v = major_of(existing.get("android_base_version"))
    inc_v = major_of(rec.get("android_base_version"))
    if base_v is not None and inc_v is not None and base_v != inc_v:
        seen = set(existing.get("android_versions_seen") or [])
        if existing.get("android_base_version"):
            seen.add(existing["android_base_version"])
        seen.add(rec["android_base_version"])
        existing["android_versions_seen"] = list(seen)
        existing["android_drift"] = True
        receipts.append("⚠ 安卓底层大版本漂移：历史基线 %s → 本次 %s，旧方案须人工复核后再套用（基线已保留）" % (
            existing.get("android_base_version"), rec.get("android_base_version")))

    # 坑：按 (symptom+root_cause) 去重追加
    for pf in rec.get("pitfalls", []):
        if not any(p.get("symptom") == pf.get("symptom") and p.get("root_cause") == pf.get("root_cause")
                   for p in existing.get("pitfalls", [])):
            pf.setdefault("created_at", _now())
            pf.setdefault("source", rec.get("source", "unknown"))
            existing.setdefault("pitfalls", []).append(pf)
            receipts.append("成功入库：新增坑记录")

    # 方案：按 problem 比对；同问题同步骤跳过；同问题异步骤进冲突队列（禁止覆盖）
    for sl in rec.get("solutions", []):
        ex = [s for s in existing.get("solutions", []) if s.get("problem") == sl.get("problem")]
        if not ex:
            existing.setdefault("solutions", []).append(seed_solution(sl, rec))
            receipts.append("成功入库：新增方案记录")
        else:
            same = any(s.get("steps") == sl.get("steps") for s in ex)
            if same:
                receipts.append("方案已存在（步骤一致），跳过未重复新增")
            else:
                kb["conflicts"].append({
                    "id": str(uuid.uuid4()), "key": key, "existing": ex[0], "incoming": sl,
                    "reason": "同问题不同修复步骤，待人工复核", "at": _now(), "status": "pending"})
                receipts.append("进入冲突复核队列：方案步骤不一致（禁止覆盖）")

    # 变体拆分：同款车但参数签名不同 -> 新增变体，告警“勿死板复用”
    # 修复：部分字段 upsert 不得制造碎片签名——已存在记录时用「历史画像+本次提交（本次优先）」合并视角计算
    SIG_FIELDS = ["arch", "chip", "dpi", "root_status", "android_base_version",
                  "factory_system_version", "storage_limits", "permission_limits",
                  "bt_limits", "usb_limits", "known_bugs"]
    variant_split = False
    if not existing.get("variant_sigs"):
        existing["variant_sigs"] = [v.get("variant_sig", variant_sig({**existing, **v})) for v in existing.get("variants", [])]
    rec_has_sig_field = any(rec.get(f) not in (None, "") for f in SIG_FIELDS)
    merged_view = {f: (rec[f] if rec.get(f) not in (None, "") else existing.get(f)) for f in SIG_FIELDS}
    eff_sig = variant_sig(merged_view) if rec_has_sig_field else sig
    if eff_sig not in existing.get("variant_sigs", []):
        existing.setdefault("variant_sigs", []).append(eff_sig)
        existing.setdefault("variants", []).append({
            "variant_sig": eff_sig, "firmware_batch": rec.get("firmware_batch", "未知批次"),
            "hardware_detail_delta": rec.get("hardware_detail_delta", {})})
        existing["variant_split"] = True
        variant_split = True
        receipts.append("⚠ 检出同款车不同车机变体（签名 %s）：已拆分记录，勿对差异车机机械复用修复" % eff_sig)
    return receipts, variant_split


def arbitrate(c):
    """D：冲突半自动仲裁，与 server.js 完全一致：基于双方证据给建议，证据不足强制人工。"""
    e = c.get("existing") or {}
    inc = c.get("incoming") or {}
    e_ok = e.get("success_count", 0) or 0
    e_bad = e.get("fail_count", 0) or 0
    e_conf = confidence_of(e)
    inc_stable = inc.get("stability_rating", 0) or 0
    if e_bad > 0 and e_ok == 0:
        return {"suggest": "apply_incoming", "reason": "历史方案已有失败记录且零成功，倾向采用新方案（仍建议人工确认）"}
    if e_conf >= 0.8 and e_ok >= 2:
        return {"suggest": "keep_existing", "reason": "历史方案成功样本充足且置信度高，建议保留"}
    if inc_stable >= 4 and e_ok == 0:
        return {"suggest": "apply_incoming", "reason": "新方案稳定评级高且历史无成功样本，建议采用新方案"}
    return {"suggest": "needs_review", "reason": "双方证据均不足，须人工复核（不自动裁决）"}


def _compute_stats(kb):
    vehicles = kb.get("vehicles", {})
    by_result = {"success": 0, "fail": 0, "partial": 0, "unknown": 0}
    total_access = 0
    pitfalls = 0
    solutions = 0
    learned = 0
    variant_splits = 0
    outcomes = 0
    for v in vehicles.values():
        r = (v.get("result") or "unknown").lower()
        by_result[r] = by_result.get(r, 0) + 1
        total_access += v.get("access_count", 0) or 0
        pitfalls += len(v.get("pitfalls", []))
        for p in v.get("pitfalls", []):
            if "negative_lesson" in (p.get("tags") or []):
                learned += 1
        for s in v.get("solutions", []):
            solutions += 1
            outcomes += (s.get("success_count", 0) or 0) + (s.get("fail_count", 0) or 0)
        if v.get("variant_split"):
            variant_splits += 1
    auto_resolvable = sum(1 for c in kb.get("conflicts", []) if arbitrate(c).get("suggest") != "needs_review")
    return {
        "车型总量": len(vehicles),
        "累计访问": total_access,
        "成功案例": by_result.get("success", 0),
        "失败案例": by_result.get("fail", 0),
        "部分修复案例": by_result.get("partial", 0),
        "坑记录数": pitfalls,
        "方案记录数": solutions,
        "进化·负向教训数": learned,
        "进化·同款车变体拆分": variant_splits,
        "进化·累计成败反馈": outcomes,
        "冲突待复核": len(kb.get("conflicts", [])),
        "冲突可自动裁决": auto_resolvable,
    }


# 只读令牌：随 Skill 公开发分发也安全（不能写库、不能上传产物、不能拉含 MFi 的源码快照）
MCP_KB_RO_TOKEN = os.environ.get("ESTRELLA_KB_RO_TOKEN", "")


def _auth_token() -> str:
    """写令牌优先；ESTRELLA_KB_READONLY=1 时强制走只读令牌（对外分发 Skill 时使用）。"""
    if os.environ.get("ESTRELLA_KB_READONLY") == "1":
        return MCP_KB_RO_TOKEN or MCP_KB_TOKEN
    return MCP_KB_TOKEN or MCP_KB_RO_TOKEN


# ---------------- 离线补交队列（MCP 不可达时不丢数据） ----------------
def pending_file() -> str:
    return os.path.join(data_dir(), "pending_queue.jsonl")


def _enqueue_pending(op, payload):
    """MCP 不可达时把写操作落盘，等联网后自动补交（否则离线数据永久滞留本地）。"""
    try:
        with open(pending_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"op": op, "payload": payload, "ts": _now()}, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        print("[warn] 待补交队列写入失败: %s" % e, file=sys.stderr)
        return False


def flush_pending(verbose=True):
    """联网后自动补交：成功出队，失败保留并记 last_error。返回 (sent, remain)。"""
    pf = pending_file()
    if not os.path.exists(pf):
        return 0, 0
    try:
        with open(pf, "r", encoding="utf-8") as f:
            items = [json.loads(line) for line in f if line.strip()]
    except Exception:
        return 0, 0
    if not items:
        return 0, 0
    sent, keep = 0, []
    for it in items:
        try:
            op = it.get("op")
            if op == "upsert":
                cmd_upsert_mcp(it["payload"], silent=True)
            elif op == "report_outcome":
                cmd_report_outcome_mcp(it["payload"], silent=True)
            else:
                continue
            sent += 1
        except Exception as e:
            it["last_error"] = str(e)
            keep.append(it)
    try:
        with open(pf, "w", encoding="utf-8") as f:
            for it in keep:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[warn] 补交队列回写失败: %s" % e, file=sys.stderr)
    if verbose and (sent or keep):
        print("[补交] 已成功补交 %d 条，失败保留 %d 条（下次联网自动重试）" % (sent, len(keep)), file=sys.stderr)
    return sent, len(keep)


# ---------------- MCP 后端 (线上) ----------------
def _mcp_request(method, path, params=None, json_body=None, timeout=15):
    url = MCP_KB_BASE_URL.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    headers = {"Authorization": "Bearer " + _auth_token()}
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _mcp_upload_raw(path, raw, params, timeout=300):
    """裸二进制上传（APK 几十 MB，不能塞 JSON/base64）。"""
    url = MCP_KB_BASE_URL.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, data=raw, method="POST",
        headers={"Authorization": "Bearer " + _auth_token(),
                 "Content-Type": "application/octet-stream"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_query_mcp(key, params=None):
    r = _mcp_request("GET", "/kb/query", params={"key": key})
    r["source_tag"] = "MCP知识库（线上）"
    if not r.get("found"):
        r["note"] = "【MCP暂无该车型变体记录】"
    else:
        # 服务端旧版返回 record，新版返回 vehicle；两者都兼容
        veh = r.get("vehicle") or r.get("record") or {}
        art = veh.get("artifact")
        if art:
            if params:
                # 提供了车机参数 → 先经 recommend 校验变体签名，一致才直接出包（防同 key 不同硬件误出包）
                try:
                    rc = _mcp_request("POST", "/kb/recommend", json_body={"vehicle_key": key, "params": params})
                    if rc.get("variant_mismatch"):
                        r["ready_apk"] = None
                        r["note"] = ("⚠ 记录中有产物引用，但本车机参数与历史变体签名不一致（%s），"
                                     "不建议直接套用现成包，请进入第 1 步分析。" % rc.get("warning", ""))
                        return print(json.dumps(r, ensure_ascii=False, indent=2))
                except Exception as e:
                    r["note_warn"] = "变体校验失败（%s），以下产物引用未经变体验证" % e
            r["ready_apk"] = art
            r["note"] = ("【100% 相同特点车型已有记录，可直接输出现成 APK 产物】"
                         + ("（已通过变体签名校验）" if params else "（未传 --params，未校验变体签名；若本车机硬件与记录不同请补传或先跑 recommend）"))
        print(json.dumps(r, ensure_ascii=False, indent=2))


def cmd_upsert_mcp(rec, silent=False):
    clean = desensitize_record(rec)  # 入库前强制脱敏（值级）
    r = _mcp_request("POST", "/kb/upsert", json_body=clean)
    if silent:
        return r
    r["source_tag"] = "MCP知识库（线上）"
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return r


def cmd_recommend_mcp(params):
    r = _mcp_request("POST", "/kb/recommend", json_body=params)
    r["source_tag"] = "MCP知识库（线上·进化推荐）"
    art = (r.get("vehicle") or {}).get("artifact") or r.get("artifact")
    mismatch = r.get("variant_mismatch")
    # 100% 相同特点 = key 精确命中 且 变体签名一致 且 有产物引用
    if art and not mismatch:
        r["ready_apk"] = art
        r["note"] = "【100% 相同特点车型已有记录，可直接输出现成 APK 产物】"
    print(json.dumps(r, ensure_ascii=False, indent=2))


def cmd_report_outcome_mcp(body, silent=False):
    r = _mcp_request("POST", "/kb/report_outcome", json_body=body)
    if silent:
        return r
    r["source_tag"] = "MCP知识库（线上）"
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return r


def cmd_publish(vehicle_key, apk_path, name=None):
    """产物闭环：把本地编译出的 APK 上传云端并回填 artifact，下次同车型 100% 命中可直接出包。"""
    apk_path = os.path.abspath(apk_path)
    if not os.path.isfile(apk_path):
        print(json.dumps({"error": "APK 不存在", "path": apk_path}, ensure_ascii=False, indent=2))
        return 1
    h = hashlib.sha256()
    size = 0
    with open(apk_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    sha = h.hexdigest()
    params = {
        "vehicle_key": normalize_key(vehicle_key),
        "sha256": sha,
        "name": name or os.path.basename(apk_path),
    }
    with open(apk_path, "rb") as f:
        raw = f.read()
    try:
        r = _mcp_upload_raw("/kb/artifact/upload", raw, params)
    except Exception as e:
        print(json.dumps({"error": "上传失败", "detail": str(e), "sha256": sha, "size": size},
                         ensure_ascii=False, indent=2))
        return 1
    r["local_file"] = apk_path
    r["size_mb"] = round(size / 1048576, 2)
    r["download_url"] = MCP_KB_BASE_URL.rstrip("/") + (r.get("artifact") or "")
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


def cmd_stats_mcp():
    r = _mcp_request("GET", "/kb/stats")
    r["source_tag"] = "MCP知识库（线上）"
    print(json.dumps(r, ensure_ascii=False, indent=2))


def cmd_list_mcp():
    r = _mcp_request("GET", "/kb/list")
    for k in r.get("keys", []):
        print(k)


def cmd_export_mcp(fmt, out):
    r = _mcp_request("GET", "/kb/export", params={"format": fmt})
    text = r.get("csv") if fmt == "csv" else json.dumps(r, ensure_ascii=False, indent=2)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(json.dumps({"status": "exported", "format": fmt, "out": os.path.abspath(out)}, ensure_ascii=False))
    else:
        print(text)


def cmd_resolve_mcp(conflict_id, action):
    r = _mcp_request("POST", "/kb/resolve", json_body={"conflict_id": conflict_id, "action": action})
    print(json.dumps(r, ensure_ascii=False, indent=2))


# ---------------- 本地缓存后端 ----------------
def cmd_query_local(key):
    kb = _load_kb()
    v = kb["vehicles"].get(normalize_key(key))
    if not v:
        print(json.dumps({"found": False, "vehicle_key": key,
                          "note": "【MCP暂无该车型变体记录】", "source_tag": "MCP知识库（本地缓存）"}, ensure_ascii=False))
        return
    out = {"found": True, "vehicle": v, "source_tag": "MCP知识库（本地缓存）"}
    art = v.get("artifact")
    if art:
        out["ready_apk"] = art
        out["note"] = "【100% 相同特点车型已有记录，可直接输出现成 APK 产物】"
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_upsert_local(json_path):
    rec = _read_json_file(json_path)
    key = normalize_key(rec.get("vehicle_key"))
    if not key or key.count("|") != 3:
        print(json.dumps({"status": "invalid", "reason": "vehicle_key 缺失或格式错误(应为 车型|年份|车机系统版本|安卓底层版本)"}, ensure_ascii=False))
        return
    clean = desensitize_record(rec)  # 入库前强制脱敏（值级）
    if not clean.get("vehicle_key"):
        print(json.dumps({"status": "invalid", "reason": "脱敏后丢失 vehicle_key"}, ensure_ascii=False))
        return
    kb = _load_kb()
    receipts, variant_split = _merge_into(kb, clean)
    _save_kb(kb)
    print(json.dumps({"status": "ok", "vehicle_key": key, "receipts": receipts,
                      "variant_split": variant_split,
                      "conflict_count": len(kb["conflicts"]), "source_tag": "MCP知识库（本地缓存）"}, ensure_ascii=False, indent=2))


def cmd_recommend_local(params):
    kb = _load_kb()
    key = normalize_key(params.get("vehicle_key"))
    v = kb["vehicles"].get(key)
    sig = variant_sig(params.get("params", {}))
    if v:
        sigs = v.get("variant_sigs") or [x.get("variant_sig") for x in v.get("variants", [])]
        mismatch = sig not in sigs
        ranked = sorted(v.get("solutions", []), key=lambda s: (confidence_of(s), (s.get("success_count", 0) - s.get("fail_count", 0))))[::-1]
        # G. 安卓大版本漂移提示
        _pp = params.get("params") or {}
        drift_base = major_of(v.get("android_base_version"))
        drift_inc = major_of(_pp.get("android_base_version"))
        android_drift = drift_base is not None and drift_inc is not None and drift_base != drift_inc
        pitfalls = v.get("pitfalls", [])
        warning = ("同款车但车机参数签名与历史成功变体不一致：须先小范围验证再套用" if mismatch else None)
        if android_drift:
            warning = (warning + "；" if warning else "") + ("⚠ 安卓底层大版本漂移：历史基线 %s → 本次 %s，旧方案须人工复核后再套用" % (
                v.get("android_base_version"), _pp.get("android_base_version")))
        art = v.get("artifact")
        out = {
            "matched": "exact", "vehicle_key": key, "variant_mismatch": mismatch,
            "android_drift": android_drift,
            "incoming_sig": sig, "known_sigs": sigs,
            "warning": warning,
            "artifact": art,
            "solutions": [{"problem": s.get("problem"), "steps": s.get("steps"), "risk_level": s.get("risk_level"),
                           "success_count": s.get("success_count", 0), "fail_count": s.get("fail_count", 0),
                           "confidence": confidence_of(s), "last_outcome": s.get("last_outcome")} for s in ranked],
            "pitfalls": [{"symptom": p.get("symptom"), "severity": p.get("severity"), "tags": p.get("tags", [])} for p in pitfalls],
            "learned_lessons": sum(1 for p in pitfalls if "negative_lesson" in (p.get("tags") or [])),
            "source_tag": "MCP知识库（本地缓存·进化推荐）",
        }
        # 100% 相同特点 = key 精确命中 且 变体签名一致 且 有产物引用
        if art and not mismatch:
            out["ready_apk"] = art
            out["note"] = "【100% 相同特点车型已有记录，可直接输出现成 APK 产物】"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    scored = sorted(((o, similarity(params.get("params", {}), o)) for o in kb["vehicles"].values()),
                   key=lambda x: x[1], reverse=True)[:3]
    out = {
        "matched": "similar", "vehicle_key": key, "incoming_sig": sig,
        "warning": "MCP 无该车型精确记录，以下为相似车型参考（非同款，须重新评估，勿直接套用）",
        "similar": [{"vehicle_key": x[0].get("vehicle_key"), "similarity": x[1], "arch": x[0].get("arch"),
                     "android_base_version": x[0].get("android_base_version")} for x in scored if x[1] > 0],
        "source_tag": "MCP知识库（本地缓存·进化推荐）",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_report_outcome_local(body):
    kb = _load_kb()
    key = normalize_key(body.get("vehicle_key"))
    v = kb["vehicles"].get(key)
    if not v:
        print(json.dumps({"status": "not_found", "key": key}, ensure_ascii=False))
        return
    outcome = (body.get("outcome") or "").lower()
    ts = _now()
    touched = False
    if body.get("problem") and v.get("solutions"):
        s = next((x for x in v["solutions"] if x.get("problem") == body["problem"] and (body.get("fix_ref") and x.get("fix_ref") == body["fix_ref"])), None) \
            or next((x for x in v["solutions"] if x.get("problem") == body["problem"]), None)
        if s:
            if outcome == "success":
                s["success_count"] = (s.get("success_count", 0) + 1)
            elif outcome == "fail":
                s["fail_count"] = (s.get("fail_count", 0) + 1)
            s["last_tried"] = ts
            s["last_outcome"] = outcome
            s["confidence"] = confidence_of(s)
            touched = True
    if outcome == "fail":
        symptom = body.get("symptom") or (("修复「" + body["problem"] + "」在该车机变体失败") if body.get("problem") else "未知失败")
        exist = next((p for p in v.get("pitfalls", []) if p.get("symptom") == symptom), None)
        if not exist:
            v.setdefault("pitfalls", []).append({
                "symptom": symptom,
                "root_cause": body.get("root_cause") or "该修复在此车机变体未生效/失败，下次须重新评估，勿直接套用历史方案",
                "severity": "HIGH", "fix_ref": body.get("fix_ref"), "tags": ["negative_lesson", "evolution"],
                "created_at": ts, "source": "evolution-auto"})
        else:
            exist.setdefault("tags", [])
            if "negative_lesson" not in exist["tags"]:
                exist["tags"].append("negative_lesson")
        touched = True
    v["updated_at"] = ts
    _save_kb(kb)
    print(json.dumps({"status": "ok", "key": key, "outcome": outcome, "touched": touched}, ensure_ascii=False))


def cmd_list_local():
    kb = _load_kb()
    for k in sorted(kb["vehicles"].keys()):
        v = kb["vehicles"][k]
        print(f"{k}  arch={v.get('arch','')}  visits={v.get('access_count',0)}  split={v.get('variant_split',False)}")


def cmd_export_local(fmt, out):
    kb = _load_kb()
    if fmt == "json":
        text = json.dumps(kb, ensure_ascii=False, indent=2)
    else:
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["type", "vehicle_key", "field1", "field2", "field3"])
        for k, v in kb["vehicles"].items():
            w.writerow(["vehicle", k, v.get("arch", ""), v.get("chip", ""), v.get("android_base_version", "")])
            for p in v.get("pitfalls", []):
                w.writerow(["pitfall", k, p.get("symptom", ""), p.get("root_cause", ""), p.get("severity", "")])
            for s in v.get("solutions", []):
                w.writerow(["solution", k, s.get("problem", ""), str(s.get("steps", [])), s.get("risk_level", "")])
        text = buf.getvalue()
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(json.dumps({"status": "exported", "format": fmt, "out": os.path.abspath(out)}, ensure_ascii=False))
    else:
        print(text)


def cmd_stats_local():
    kb = _load_kb()
    s = _compute_stats(kb)
    s["source_tag"] = "MCP知识库（本地缓存）"
    print(json.dumps(s, ensure_ascii=False, indent=2))


def cmd_conflicts_mcp():
    r = _mcp_request("GET", "/kb/conflicts")
    r["source_tag"] = "MCP知识库（线上）"
    print(json.dumps(r, ensure_ascii=False, indent=2))


def cmd_conflicts_local():
    kb = _load_kb()
    out = [dict(c, **arbitrate(c)) for c in kb.get("conflicts", [])]
    print(json.dumps({"conflicts": out}, ensure_ascii=False, indent=2))


def cmd_resolve_local(conflict_id, action):
    kb = _load_kb()
    c = next((x for x in kb["conflicts"] if x.get("id") == conflict_id), None)
    if not c:
        print(json.dumps({"status": "not_found", "conflict_id": conflict_id}, ensure_ascii=False))
        return
    auto_reason = None
    if action == "auto":
        a = arbitrate(c)
        if a.get("suggest") == "needs_review":
            print(json.dumps({"status": "needs_review", "conflict_id": conflict_id, "suggest": a}, ensure_ascii=False))
            return
        action = a.get("suggest")
        auto_reason = a.get("reason")
    if action == "apply_incoming":
        # incoming 是一条 solution：替换 vehicle.solutions 中对应问题的方案，而非合并进 vehicle 顶层
        veh = kb["vehicles"].get(c["key"])
        if veh is not None:
            sols = veh.setdefault("solutions", [])
            incoming = dict(c["incoming"])
            incoming = seed_solution(incoming, veh)
            idx = next((i for i, s in enumerate(sols) if s.get("problem") == c["incoming"].get("problem")), None)
            if idx is not None:
                sols[idx] = incoming
            else:
                sols.append(incoming)
            veh["updated_at"] = _now()
    elif action != "keep_existing":
        print(json.dumps({"status": "bad_action"}, ensure_ascii=False))
        return
    c["status"] = "resolved:" + action + (" (auto)" if auto_reason else "")
    _save_kb(kb)
    print(json.dumps({"status": "resolved", "conflict_id": conflict_id, "action": action,
                      "auto": bool(auto_reason), "reason": auto_reason}, ensure_ascii=False))


def cmd_backup():
    src = data_dir()
    dst = os.path.join(src, "backups", datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dst, exist_ok=True)
    sp = os.path.join(src, "kb.json")
    if os.path.exists(sp):
        shutil.copy2(sp, os.path.join(dst, "kb.json"))
    print(json.dumps({"status": "backed_up", "to": dst}, ensure_ascii=False))


# ---------------- 调度（mcp 优先，失败回退本地） ----------------
def main():
    ap = argparse.ArgumentParser(description="Estrella 车型知识库 (local/mcp)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("query"); p.add_argument("--key", required=True)
    p.add_argument("--params", default=None, help="可选：车机参数 JSON（{或文件路径开头）。命中 artifact 时用于变体签名校验")
    p = sub.add_parser("upsert"); p.add_argument("--json", required=True)
    p = sub.add_parser("recommend"); p.add_argument("--json", required=True)
    p = sub.add_parser("report_outcome"); p.add_argument("--json", required=True)
    sub.add_parser("list")
    p = sub.add_parser("export"); p.add_argument("--format", choices=["csv", "json"], default="json"); p.add_argument("--out")
    sub.add_parser("stats")
    p = sub.add_parser("resolve"); p.add_argument("--conflict-id", required=True); p.add_argument("--action", default="keep_existing", choices=["apply_incoming", "keep_existing", "auto"])
    p = sub.add_parser("publish"); p.add_argument("--key", required=True); p.add_argument("--apk", required=True)
    p.add_argument("--name", default=None, help="可选：产物显示名，默认取文件名")
    sub.add_parser("flush", help="手动补交离线队列（正常联网时会自动补交，无需手调）")
    sub.add_parser("conflicts")
    sub.add_parser("backup")
    ap.add_argument("--backend", choices=["mcp", "local"], default=DEFAULT_BACKEND)
    args = ap.parse_args()
    be = args.backend

    def run_local():
        if args.cmd == "query":
            cmd_query_local(args.key)
        elif args.cmd == "upsert":
            cmd_upsert_local(args.json)
        elif args.cmd == "recommend":
            cmd_recommend_local(_read_json_file(args.json))
        elif args.cmd == "report_outcome":
            cmd_report_outcome_local(_read_json_file(args.json))
        elif args.cmd == "list":
            cmd_list_local()
        elif args.cmd == "export":
            cmd_export_local(args.format, args.out)
        elif args.cmd == "stats":
            cmd_stats_local()
        elif args.cmd == "conflicts":
            cmd_conflicts_local()
        elif args.cmd == "resolve":
            cmd_resolve_local(args.conflict_id, args.action)
        elif args.cmd == "backup":
            cmd_backup()

    if be == "local":
        run_local()
        return

    payload = None
    try:
        if args.cmd == "publish":
            sys.exit(cmd_publish(args.key, args.apk, args.name))
        if args.cmd == "flush":
            s, r = flush_pending(verbose=True)
            print(json.dumps({"flushed": s, "remain": r}, ensure_ascii=False))
            return
        # 联网首动作：先补交离线期间积压的写操作（ping 通过才补，避免离线时空等超时）
        try:
            _mcp_request("GET", "/kb/stats", timeout=6)
            flush_pending(verbose=True)
        except Exception:
            pass
        if args.cmd == "query":
            qparams = _read_json_file(args.params) if args.params else None
            cmd_query_mcp(args.key, params=qparams)
        elif args.cmd == "upsert":
            payload = _read_json_file(args.json)
            cmd_upsert_mcp(payload)
        elif args.cmd == "recommend":
            cmd_recommend_mcp(_read_json_file(args.json))
        elif args.cmd == "report_outcome":
            payload = _read_json_file(args.json)
            cmd_report_outcome_mcp(payload)
        elif args.cmd == "list":
            cmd_list_mcp()
        elif args.cmd == "export":
            cmd_export_mcp(args.format, args.out)
        elif args.cmd == "stats":
            cmd_stats_mcp()
        elif args.cmd == "conflicts":
            cmd_conflicts_mcp()
        elif args.cmd == "resolve":
            cmd_resolve_mcp(args.conflict_id, args.action)
        elif args.cmd == "backup":
            print("[info] backup 仅本地模式支持，已执行本地备份", file=sys.stderr)
            cmd_backup()
    except Exception as e:
        print(f"[warn] MCP 调用失败，回退本地缓存: {e}", file=sys.stderr)
        # 离线不丢数据：写操作落待补交队列，联网后自动补交
        if args.cmd in ("upsert", "report_outcome") and payload is not None:
            if _enqueue_pending(args.cmd, payload):
                print("[info] 已写入待补交队列，联网后自动补交", file=sys.stderr)
        run_local()


if __name__ == "__main__":
    main()
