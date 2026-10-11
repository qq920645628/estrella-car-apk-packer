#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apk_catalog.py — DiPlay 现成包匹配层（傻瓜式入口）

设计目标：用户只说车型 / 安卓版本 / 想要的特性，Skill 直接给出
「无需编译」的现成 APK 路径与安装指引；只有现成包覆盖不到时才
提示进入编译分支（云端构建兜底，见 SKILL.md）。

零依赖（仅标准库）：os / json / glob / argparse / sys

用法示例：
  python scripts/apk_catalog.py --android 4.2
  python scripts/apk_catalog.py --android 9.0 --feature 仪表
  python scripts/apk_catalog.py --vehicle "本田 思铂睿 2015"
  python scripts/apk_catalog.py --brand 长安 --model 逸动plus --year 2021
  python scripts/apk_catalog.py --list            # 列出全部已知包
"""
import os
import sys
import json
import glob
import argparse

# ----------------------------------------------------------------------------
# 1) 现成包目录（按安卓版本分档 + flavor + 特性）
#    file: 用于运行时 glob 定位真实文件的文件名特征
#    android_min/max: 适用安卓大版本区间（含）
#    flavor: 对应构建 flavor（retro/legacy/mid81/universal/cluster/lite/hd/ble）
#    features: 关键词，用于 --feature 匹配
#    priority: 同档内优先级（越小越优先推荐）
# ----------------------------------------------------------------------------
CATALOG = [
    # ---- 安卓 4.2~4.4 老车机（retro / 4.x）----
    {
        "id": "retro-4x-nousb-v23",
        "name": "DiPlay 4.x 无调试版 v2.3",
        "file": "DiPlay-4.x无调试版_v2.3_无USB调试可用.apk",
        "android_min": 4.2, "android_max": 4.4,
        "flavor": "retro", "module": "retro",
        "size_mb": 4.7, "version": "2.3",
        "features": ["无USB调试", "老车机", "CarPlay接收端", "最稳"],
        "priority": 1,
        "notes": "安卓 4.2~4.4 老车机首选；无需 USB 调试，插上即用最稳",
    },
    {
        "id": "retro-4x-nousb-v22",
        "name": "DiPlay 4.x 无调试版 v2.2（旧版）",
        "file": "DiPlay-4.x无调试版_v2.2_无USB调试可用_旧版.apk",
        "android_min": 4.2, "android_max": 4.4,
        "flavor": "retro", "module": "retro",
        "size_mb": 4.7, "version": "2.2",
        "features": ["无USB调试", "老车机", "CarPlay接收端"],
        "priority": 2,
        "notes": "同上旧版，仅当 v2.3 在个别车机异常时回退",
    },
    # ---- 安卓 6.0~8.1 中控车机（legacy / mid81）----
    {
        "id": "legacy-6.0-v22",
        "name": "DiPlay 6.0 车机版 v2.2",
        "file": "DiPlay-6.0车机版_v2.2_5.8MB.apk",
        "android_min": 6.0, "android_max": 7.1,
        "flavor": "legacy", "module": "legacy",
        "size_mb": 5.9, "version": "2.2",
        "features": ["中控", "CarPlay接收端"],
        "priority": 1,
        "notes": "安卓 6.0~7.1 中控车机",
    },
    {
        "id": "mid81-8.1-v15",
        "name": "DiPlay 8.1 车机版 v1.5（带方向盘）",
        "file": "DiPlay-8.1车机版_v1.5_带方向盘_5.8MB.apk",
        "android_min": 8.0, "android_max": 8.1,
        "flavor": "mid81", "module": "mid81",
        "size_mb": 5.9, "version": "1.5",
        "features": ["中控", "方向盘控制", "CarPlay接收端"],
        "priority": 1,
        "notes": "安卓 8.0~8.1 中控，带方控注入",
    },
    # ---- 安卓 9.0+ 通用车机（automotive 5 flavor）----
    {
        "id": "auto-universal-152",
        "name": "DiPlay 车机通用版 v1.5.2",
        "file": "DiPlay-车机通用版_v1.5.2_39MB.apk",
        "android_min": 9.0, "android_max": 99,
        "flavor": "universal", "module": "automotive",
        "size_mb": 39, "version": "1.5.2",
        "features": ["通用", "CarPlay接收端", "BLE前台广播", "仪表HEVC"],
        "priority": 1,
        "notes": "安卓 9.0+ 通用车机首选；含 BLE 持久广播 + 仪表投屏",
    },
    {
        "id": "auto-cluster-150",
        "name": "DiPlay 车机仪表版 v1.5.0",
        "file": "DiPlay-车机仪表版_v1.5.0_39MB.apk",
        "android_min": 9.0, "android_max": 99,
        "flavor": "cluster", "module": "automotive",
        "size_mb": 39, "version": "1.5.0",
        "features": ["仪表", "仪表盘投屏", "CarPlay接收端"],
        "priority": 2,
        "notes": "安卓 9.0+ 需要仪表/中控双屏投屏时选",
    },
    {
        "id": "auto-lite-150",
        "name": "DiPlay 车机精简版 v1.5.0",
        "file": "DiPlay-车机精简版_v1.5.0_39MB.apk",
        "android_min": 9.0, "android_max": 99,
        "flavor": "lite", "module": "automotive",
        "size_mb": 39, "version": "1.5.0",
        "features": ["精简", "通用", "CarPlay接收端"],
        "priority": 3,
        "notes": "关掉仪表 HEVC 的轻量版，老一点 9.0 车机更流畅",
    },
    {
        "id": "auto-hd-150",
        "name": "DiPlay 车机高帧版 v1.5.0",
        "file": "DiPlay-车机高帧版_v1.5.0_39MB.apk",
        "android_min": 9.0, "android_max": 99,
        "flavor": "hd", "module": "automotive",
        "size_mb": 39, "version": "1.5.0",
        "features": ["高帧", "60fps", "130%缩放", "CarPlay接收端"],
        "priority": 4,
        "notes": "高分屏/高刷车机选，60fps + 130% 缩放",
    },
    {
        "id": "auto-ble-152",
        "name": "DiPlay 车机 BLE 版 v1.5.2",
        "file": "DiPlay-车机BLE版_v1.5.2_39MB.apk",
        "android_min": 9.0, "android_max": 99,
        "flavor": "ble", "module": "automotive",
        "size_mb": 39, "version": "1.5.2",
        "features": ["BLE", "无线CarPlay", "CarPlay接收端"],
        "priority": 5,
        "notes": "需要无线 CarPlay（BLE 前台广播）时选",
    },
    # ---- 车型定制版 ----
    {
        "id": "changan-yidong-155",
        "name": "DiPlay 长安逸动plus 定制版 v1.5.5",
        "file": "DiPlay-长安逸动plus定制版_v1.5.5_39MB.apk",
        "android_min": 9.0, "android_max": 99,
        "flavor": "universal", "module": "automotive",
        "size_mb": 39, "version": "1.5.5",
        "features": ["长安", "逸动plus", "定制", "设置持久化修复"],
        "priority": 1,
        "notes": "长安逸动 plus 2021（安卓 9.0）专用，已修设置不落盘",
        "vehicles": ["长安 逸动plus", "长安 逸动 plus 2021"],
    },
]

# 常见车型 → 安卓版本（用于 --vehicle / --brand --model 软匹配）
# 仅收录高频已知车型；命中后作为 android 区间输入，仍提示用户确认
VEHICLE_MAP = {
    "本田 思铂睿": 4.2,
    "本田 凌派": 4.4,
    "长安 逸动plus": 9.0,
    "长安 逸动 plus 2021": 9.0,
    "北京 x7": 9.0,
    "北京 x7 2022": 9.0,
}

# 搜索根（运行时 glob）。优先桌面「DiPlay-全部版本」，其次桌面、下载站待扩展
# ----------------------------------------------------------------------------
# 云端目录：新增 APK 版本只需在服务端改 apk_catalog.json，无需改本脚本重分发。
# 不可达时回落本地缓存，再回落内置 CATALOG。
# ----------------------------------------------------------------------------
ACTIVE_CATALOG = list(CATALOG)
CATALOG_SOURCE = "builtin"
_SPEC_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", ".apk_catalog_spec.json")


def _kb_token():
    t = os.environ.get("ESTRELLA_KB_TOKEN") or os.environ.get("ESTRELLA_KB_RO_TOKEN")
    if t:
        return t
    try:  # 复用 local_kb 的令牌常量，避免在本脚本再硬编码一份
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from local_kb import _auth_token
        return _auth_token()
    except Exception:
        return ""


def refresh_catalog(timeout=6):
    """拉取云端目录并合并（同 id 云端覆盖内置）。返回 (source, cloud_count)。"""
    global ACTIVE_CATALOG, CATALOG_SOURCE
    items, src = [], "builtin"
    if os.environ.get("ESTRELLA_NO_CLOUD") == "1":
        ACTIVE_CATALOG, CATALOG_SOURCE = list(CATALOG), "builtin"
        return CATALOG_SOURCE, 0
    try:
        import urllib.request
        base = os.environ.get("ESTRELLA_KB_URL", "http://106.12.162.224:7799")
        req = urllib.request.Request(
            base.rstrip("/") + "/kb/apk_catalog_spec",
            headers={"Authorization": "Bearer " + _kb_token()})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8"))
        items = j.get("items") or []
        src = "cloud" if items else "builtin"
        if items:
            try:
                with open(_SPEC_CACHE, "w", encoding="utf-8") as f:
                    json.dump(j, f, ensure_ascii=False)
            except Exception:
                pass
    except Exception:
        try:
            with open(_SPEC_CACHE, "r", encoding="utf-8") as f:
                items = json.load(f).get("items") or []
                src = "cache"
        except Exception:
            items, src = [], "builtin"
    merged = list(CATALOG)
    ids = {i.get("id") for i in merged}
    for c in items:
        if not isinstance(c, dict):
            continue
        if c.get("id") in ids:
            merged = [c if i.get("id") == c.get("id") else i for i in merged]
        else:
            merged.append(c)
    ACTIVE_CATALOG, CATALOG_SOURCE = merged, src
    return CATALOG_SOURCE, len(items)


SEARCH_ROOTS = [
    os.path.join(os.path.expanduser("~"), "Desktop", "DiPlay-全部版本"),
    os.path.join(os.path.expanduser("~"), "Desktop"),
    os.path.join(os.path.expanduser("~"), "Downloads"),
    os.path.join(os.path.expanduser("~"), "Desktop", "123", "DiPlay-Universal",
                 "automotive", "build", "outputs", "apk"),
]


def parse_android(text):
    """把各种写法归一为 float 安卓版本：
       '4.2' / '安卓4.2' / 'Android 4.2' / 'API17'(→4.2) / '老车机'(→4.2) /
       '9' / '9.0+' / '9.0以上'
       返回 float 或 None。
    """
    if not text:
        return None
    t = str(text).strip().lower()
    api_map = {17: 4.2, 19: 4.4, 21: 5.0, 23: 6.0, 24: 7.0, 25: 7.1,
               26: 8.0, 27: 8.1, 28: 9.0, 29: 10.0, 30: 11.0, 31: 12.0}
    if t in ("老车机", "old", "4.x"):
        return 4.2
    if "api" in t:
        digits = "".join(ch for ch in t if ch.isdigit())
        if digits:
            return api_map.get(int(digits))
    # 提取第一个 X.Y 或 X
    import re
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if m:
        v = float(m.group(1))
        if v >= 10 and v < 20:      # 形如 "17" 当成 API
            return api_map.get(int(v))
        return v
    return None


def match(android=None, features=None, vehicle=None, brand=None, model=None, year=None):
    """返回 (best, alternatives) 两个 list[dict]，按优先级排序。"""
    # 车型软匹配 → 推导安卓版本
    if android is None and (vehicle or brand or model):
        key = " ".join(filter(None, [brand or "", model or ""])).strip()
        for k, v in VEHICLE_MAP.items():
            # key 命中：品牌+车型 包含于 已知关键词，或反之
            if key and (key in k or k in key):
                android = v
                break
            # vehicle 命中：已知关键词 包含于 用户输入，或反之
            if vehicle and (vehicle in k or k in vehicle):
                android = v
                break

    feats = [f.lower() for f in (features or [])]
    cands = []
    for item in ACTIVE_CATALOG:
        # 安卓区间过滤
        if android is not None:
            if not (item["android_min"] <= android <= item["android_max"]):
                continue
        # 特性评分：精确命中(底层 kw 完全等于需求) +3，子串/包含 +1
        score = 0
        if feats:
            for f in feats:
                best_hit = 0
                for kw in item["features"]:
                    kw_l = kw.lower()
                    if f == kw_l:
                        best_hit = max(best_hit, 3)
                    elif f in kw_l or kw_l in f:
                        best_hit = max(best_hit, 1)
                score += best_hit
            if score == 0:
                # 特性不匹配则降权但仍可能作为备选（同安卓档）
                score = -1
        # 车型定制命中
        if (brand or model or vehicle) and item.get("vehicles"):
            label = " ".join(filter(None, [brand or "", model or ""])).strip()
            if any(label and label in v or (vehicle and vehicle in v)
                   for v in item["vehicles"]):
                score += 5
        item["_score"] = score
        cands.append(item)

    if not cands and android is not None:
        # 放宽：返回同档但特性不匹配的，作为“需确认”备选
        for item in ACTIVE_CATALOG:
            if item["android_min"] <= android <= item["android_max"]:
                item["_score"] = -1
                cands.append(item)

    # 排序：分数降序，同分按 priority 升序
    cands.sort(key=lambda x: (-x["_score"], x["priority"]))
    return cands


def locate(item, extra_dirs=None):
    """在 SEARCH_ROOTS + extra_dirs 中 glob 定位真实文件，返回绝对路径或 None。"""
    roots = list(SEARCH_ROOTS)
    if extra_dirs:
        roots = [extra_dirs] + roots
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        hits = glob.glob(os.path.join(root, "**", item["file"]), recursive=True)
        if hits:
            return os.path.abspath(hits[0])
        # 退一步：按 id/flavor 关键词模糊找
        kw = item["flavor"]
        fuzzy = glob.glob(os.path.join(root, "**", f"*{kw}*.apk"), recursive=True)
        if fuzzy:
            return os.path.abspath(fuzzy[0])
    return None


def cmd_list():
    for it in ACTIVE_CATALOG:
        print(f"[{it['android_min']}~{it['android_max']}] {it['name']} "
              f"({it['size_mb']}MB) features={','.join(it['features'])}")
        print(f"      file: {it['file']}")


def format_vehicle_label(args, android):
    parts = []
    if args.vehicle:
        parts.append(args.vehicle)
    elif args.brand or args.model:
        parts.append(" ".join(filter(None, [args.brand or "", args.model or ""])))
    if args.year:
        parts.append(args.year + "款")
    if android:
        parts.append(f"Android {android}")
    return " / ".join(parts) if parts else "未指定车型"


def make_plan(cands_with_path, no_source=False):
    ready = [c for c in cands_with_path if c["_path"]]
    need_download = [c for c in cands_with_path if not c["_path"]]
    return {
        "ready": ready,
        "need_download": need_download,
        "total": len(cands_with_path),
        "can_install_now": len(ready),
        "next_source_branch": (not no_source),
    }


def main():
    ap = argparse.ArgumentParser(description="DiPlay 现成包匹配（傻瓜式入口，候选清单+用户确认）")
    ap.add_argument("--android", help="安卓版本，如 4.2 / 9.0 / API17 / 老车机")
    ap.add_argument("--feature", action="append", help="特性，可重复：无USB调试/BLE/仪表/高帧/通用/长安...")
    ap.add_argument("--vehicle", help="车型，如 '本田 思铂睿 2015'")
    ap.add_argument("--brand", help="品牌，如 长安")
    ap.add_argument("--model", help="车型，如 逸动plus")
    ap.add_argument("--year", help="年份")
    ap.add_argument("--dir", help="额外搜索目录")
    ap.add_argument("--list", action="store_true", help="列出全部已知包")
    ap.add_argument("--no-source", action="store_true", help="无源码模式：只走现成包，不提示编译分支")
    ap.add_argument("--no-cloud", action="store_true", help="不拉云端目录，仅用内置 CATALOG")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    if args.no_cloud:
        os.environ["ESTRELLA_NO_CLOUD"] = "1"
    src, cloud_n = refresh_catalog()

    if args.list:
        cmd_list()
        return 0

    android = parse_android(args.android) if args.android else None
    cands = match(android=android, features=args.feature,
                  vehicle=args.vehicle, brand=args.brand, model=args.model, year=args.year)

    if not cands:
        msg = ("❌ 未匹配到现成包。请确认安卓版本（如 4.2 / 9.0），"
               "或提供车型让我帮你推导。")
        if args.no_source:
            msg += (" 出包一律在本地：本机无源码时，可先 `setup_env.py` 部署 JDK17+SDK+NDK，"
                    "再 `pull_source.py` 从云端拉取源码，然后本地 `build_apk.py` 编译出包。")
        else:
            msg += " 如确实需要定制编译，请用 setup_env + pull_source 在本地部署环境并拉取源码后编译。"
        if args.json:
            print(json.dumps({"ok": False, "message": msg}, ensure_ascii=False, indent=2))
        else:
            print(msg)
        return 1

    # 定位真实文件
    items = []
    for it in cands:
        path = locate(it, args.dir)
        rec = dict(it)
        rec["_path"] = path
        items.append(rec)

    plan = make_plan(items, no_source=args.no_source)

    if args.json:
        out = {
            "ok": True,
            "android": android,
            "target": format_vehicle_label(args, android),
            "no_source": args.no_source,
            "candidates": items[:6],
            "plan": plan,
            "need_user_confirm": True,
            "note": "请用户按顺序测试候选APK，反馈哪个能用/什么现象，再确定最终方案。",
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # 文本输出：候选清单 + 测试计划 + 用户确认
    print("=" * 64)
    print(" DiPlay 现成包候选清单（无需源码、无需编译）")
    print("=" * 64)
    print(f" 目标车型/系统：{format_vehicle_label(args, android)}")
    print()
    print(f" 以下 {len(items)} 个 APK 理论上都能安装到这个环境，")
    print(" 请按顺序测试，并告诉我结果（安装是否成功 / 能否打开 / CarPlay 是否连上）。")
    print()

    for idx, it in enumerate(items[:6], 1):
        tag = "【先试】" if idx == 1 else "【备选】"
        print(f" {tag}{idx}. {it['name']}")
        print(f"     适用：Android {it['android_min']}~{it['android_max']}，flavor={it['flavor']}，{it['size_mb']}MB")
        print(f"     特点：{', '.join(it['features'])}")
        print(f"     说明：{it['notes']}")
        if it["_path"]:
            print(f"     📦 本机已找到：{it['_path']}")
            print(f"     安装：U盘拷到车机 → 文件管理器点击安装；")
            print(f"           或电脑 ADB：adb install \"{it['_path']}\"")
        else:
            print(f"     ⚠️ 本机未找到该文件（{it['file']}）")
            print(f"     获取：从下载站 http://120.48.51.57/ 下载对应版本")
        print()

    print("=" * 64)
    print(" 请用户确认流程：")
    print(f" 1. 先试 ① {items[0]['name']}，装完告诉我结果。")
    if len(items) > 1:
        print(" 2. 如果 ① 不行，再按编号 ②③… 逐个试。")
    if args.no_source:
        print(" 3. 出包只在本地：若全部候选都不行，在本机跑 `setup_env.py` 部署环境 + "
              "`pull_source.py` 从云端拉源码，再 `build_apk.py` 本地编译出包。")
    else:
        print(" 3. 若全部现成候选都不行，再进入本地编译分支（setup_env + pull_source 拉源码后本地出包）。")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
