#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Estrella 车机 APK 打包 Skill —— 预编译安全校验
纯标准库（仅用 xml.etree 解析 Manifest）。exit code: 0=无 ERROR, 1=存在 ERROR。

子命令/参数:
  --project <根> [--arch arm64] [--verbose]

校验项（不可减项，见 security-rules.md）:
  1. 配置语法: 工程含 settings.gradle(.kts) 与至少一个模块 build 文件
  2. 权限冲突: 同权限不同 protectionLevel / 重复声明 <uses-permission>
  3. 重复组件: 同 activity/service/receiver/provider 全限定名
  4. 导出合规: targetSdk>=31 时组件必须声明 android:exported（缺失即编译失败）
  5. so 架构匹配: 若模块含 jniLibs/libs，目标 arch 目录须存在(缺失时 ERROR)
  6. 静态高危扫描: 提权/越权调用(su/Runtime.exec/ProcessBuilder/mount/chmod 777/adb/ioctl) → WARNING 需人工确认
"""
import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

NAMESPACE = "{http://schemas.android.com/apk/res/android}"
ATTR_NAME = NAMESPACE + "name"
ATTR_PROTECT = NAMESPACE + "protectionLevel"

ERRORS = []
WARNINGS = []
INFOS = []


def err(msg):
    ERRORS.append(msg)


def warn(msg):
    WARNINGS.append(msg)


def info(msg):
    INFOS.append(msg)


def find_modules(root):
    mods = []
    for dirpath, dirs, files in os.walk(root):
        if "src" in dirs and ("build.gradle.kts" in files or "build.gradle" in files):
            mods.append(dirpath)
    return mods


def check_config_syntax(root):
    has_settings = any(os.path.exists(os.path.join(root, n)) for n in ["settings.gradle", "settings.gradle.kts"])
    if not has_settings:
        warn("未找到 settings.gradle(.kts)，工程结构可能异常")
    mods = find_modules(root)
    if not mods:
        err("未找到任何含 build 文件的模块")
    else:
        info(f"发现模块: {len(mods)} 个")
    return mods


def check_manifest(manifest_path):
    try:
        tree = ET.parse(manifest_path)
    except Exception as e:
        err(f"Manifest 解析失败 {manifest_path}: {e}")
        return
    root = tree.getroot()
    # 解析 targetSdkVersion（用于导出合规判定）
    target_sdk = 0
    for sdk in root.iter(NAMESPACE + "uses-sdk"):
        tv = sdk.get(NAMESPACE + "targetSdkVersion")
        if tv and str(tv).isdigit():
            target_sdk = int(tv)
    # 重复组件
    seen = {}
    for tag in ["activity", "service", "receiver", "provider"]:
        full = NAMESPACE + tag
        for el in root.iter(full):
            name = el.get(ATTR_NAME)
            if name:
                seen.setdefault(tag, {}).setdefault(name, 0)
                seen[tag][name] += 1
    for tag, names in seen.items():
        for n, c in names.items():
            if c > 1:
                err(f"重复组件 <{tag}> 全限定名: {n} (出现 {c} 次)")
    # 权限冲突 / 重复声明
    perms = {}
    perm_counts = {}
    for el in root.iter(NAMESPACE + "permission"):
        pn = el.get(ATTR_NAME)
        pl = el.get(ATTR_PROTECT)
        if pn:
            perms.setdefault(pn, set()).add(pl)
    for pn, levels in perms.items():
        if len(levels) > 1:
            err(f"权限冲突: {pn} 声明了不同 protectionLevel {levels}")
    for el in root.iter(NAMESPACE + "uses-permission"):
        pn = el.get(ATTR_NAME)
        if pn:
            perm_counts[pn] = perm_counts.get(pn, 0) + 1
    for pn, c in perm_counts.items():
        if c > 1:
            err(f"重复声明 <uses-permission> {pn} (出现 {c} 次)")
    # 导出合规：targetSdk>=31 必须声明 android:exported
    if target_sdk >= 31:
        for tag in ["activity", "service", "receiver"]:
            for el in root.iter(NAMESPACE + tag):
                if el.get(ATTR_NAME) and el.get(NAMESPACE + "exported") is None:
                    err(f"组件 <{tag}> {el.get(ATTR_NAME)} 缺少 android:exported（targetSdk={target_sdk} 编译会失败）")
    elif target_sdk == 0:
        info("未解析到 targetSdkVersion，跳过 exported 合规检查")


def check_so_arch(module_dir, arch):
    libs_dirs = []
    for base in ["src/main/jniLibs", "src/main/libs", "libs"]:
        d = os.path.join(module_dir, base)
        if os.path.isdir(d):
            libs_dirs.append(d)
    if not libs_dirs:
        info(f"模块 {os.path.basename(module_dir)} 无原生库目录")
        return
    all_abis = set()
    for d in libs_dirs:
        for sub in os.listdir(d):
            if os.path.isdir(os.path.join(d, sub)):
                all_abis.add(sub)
    info(f"模块 {os.path.basename(module_dir)} 含 ABI: {sorted(all_abis)}")
    if arch and arch not in all_abis:
        # 缺失目标架构: 对 app 模块是 ERROR(装不上)，对纯库模块可放宽
        err(f"so 架构不匹配: 目标 {arch} 不在 {sorted(all_abis)}（将导致安装/运行失败）")
    if arch and arch in all_abis:
        info(f"so 架构匹配: 目标 {arch} 存在")


def check_static_scan(module_dir):
    src = os.path.join(module_dir, "src")
    if not os.path.isdir(src):
        return
    patterns = [
        (re.compile(r"Runtime\.getRuntime\(\)\.exec\(", re.I), "Runtime.exec 调用（需确认是否提权/越权）"),
        (re.compile(r"ProcessBuilder\(", re.I), "ProcessBuilder 调用（需确认是否提权/越权）"),
        (re.compile(r"[\"']su[\"'\s]", re.I), "疑似 su 提权调用"),
        (re.compile(r"mount\s+-?o?\s*remount|mount\s+/system", re.I), "疑似 remount/system 挂载"),
        (re.compile(r"chmod\s+777", re.I), "疑似 chmod 777 越权赋权"),
        (re.compile(r"adb\s+shell|adb\b", re.I), "疑似 adb 调用"),
        (re.compile(r"ioctl\s*\(", re.I), "疑似 ioctl 底层调用"),
        (re.compile(r"Settings\.Global\.put|Settings\.Secure\.put|content\s+call", re.I), "疑似系统设置越权写入"),
    ]
    hits = []
    for dp, _, files in os.walk(src):
        for fn in files:
            if fn.endswith((".kt", ".java")):
                fp = os.path.join(dp, fn)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            for pat, desc in patterns:
                                if pat.search(line):
                                    hits.append(f"{fp}:{i}: {desc}")
                except Exception:
                    pass
    for h in hits:
        warn(f"⚠️ {h}（需人工确认是否提权用途，违反安全规则将终止打包）")


def main():
    ap = argparse.ArgumentParser(description="Estrella 预编译安全校验")
    ap.add_argument("--project", required=True)
    ap.add_argument("--arch", default="")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    root = os.path.abspath(args.project)
    if not os.path.isdir(root):
        err(f"工程根不存在: {root}")
    else:
        mods = check_config_syntax(root)
        for m in mods:
            mf = None
            for cand in ["src/main/AndroidManifest.xml", "src/AndroidManifest.xml"]:
                if os.path.exists(os.path.join(m, cand)):
                    mf = os.path.join(m, cand)
                    break
            if mf:
                check_manifest(mf)
            else:
                warn(f"模块 {os.path.basename(m)} 未找到 AndroidManifest.xml")
            check_so_arch(m, args.arch)
            check_static_scan(m)

    report = {"ERROR": ERRORS, "WARNING": WARNINGS, "INFO": INFOS,
              "has_error": len(ERRORS) > 0}
    print(__import__("json").dumps(report, ensure_ascii=False, indent=2))
    if args.verbose:
        for e in ERRORS:
            print(f"ERROR: {e}")
        for w in WARNINGS:
            print(f"WARN: {w}")
    sys.exit(1 if ERRORS else 0)


if __name__ == "__main__":
    main()
