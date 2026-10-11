#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Estrella 车机 APK 打包 Skill —— 编译打包 + 产物校验
依赖: JDK17, Android SDK(build-tools), Gradle 工程(DiPlay-Universal 参考实现)。

参数:
  --project <根>                源码工程根（可含中文，会自动复制到 ASCII 副本构建）
  --module <模块>                retro|legacy|mid81|automotive（默认 automotive）
  --flavor <风味>                universal|lite|cluster|hd|ble（默认 universal）
  --arch <abi>                  目标架构(用于 so 校验与命名)，如 arm64
  --ascii-build-dir <dir>        ASCII 构建副本目录（默认 C:\\Users\\Administrator\\DiPlayBuild\\DiPlay-Universal）
  --java-home <dir>             JDK17 路径
  --android-home <dir>          Android SDK 路径
  --version-code <int>          可选：升 versionCode（仅作用于目标 flavor 块）
  --version-name <str>          可选：设 versionName（仅作用于目标 flavor 块）
  --out <dir>                   最终 APK 输出目录
  --skip-build                  跳过 gradle 构建，仅校验 --apk 指定的现有 APK
  --apk <file>                 配合 --skip-build，直接校验该 APK

说明:
  - 签名由工程 Gradle 的 release 配置负责（见 references/build-pipeline.md 与 locked-config.md），
    本脚本不重签 APK，仅做产物校验（MD5、zipalign、apksigner 验签）。移除此前误导性的 --sign-* 死参数。
  - 版本号升版仅在该 flavor 块内生效，不会误改 defaultConfig。

输出: JSON 报告（versionCode/Name/package、MD5、签名 scheme、对齐、路径）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


def is_windows():
    return sys.platform.startswith("win")


def run(cmd, cwd=None, env=None):
    print(f"[run] {' '.join(cmd) if isinstance(cmd, list) else cmd}", flush=True)
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if r.stdout:
        print(r.stdout[-4000:] if len(r.stdout) > 4000 else r.stdout)
    if r.stderr:
        print(r.stderr[-4000:] if len(r.stderr) > 4000 else r.stderr)
    return r.returncode == 0, r.stdout + r.stderr


def copy_to_ascii(src, dst):
    os.makedirs(dst, exist_ok=True)
    if is_windows():
        cmd = ["robocopy", src, dst, "/MIR", "/XD", "build", ".gradle", "/NFL", "/NDL", "/NP"]
        subprocess.run(cmd, capture_output=True)
    else:
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("build", ".gradle"), dirs_exist_ok=True)


def bump_version(module_dir, flavor, vc, vn):
    # 仅在该 flavor 块内替换版本号，避免误改 defaultConfig
    bp = os.path.join(module_dir, "build.gradle.kts")
    if not os.path.exists(bp):
        return False, "build.gradle.kts 不存在"
    txt = open(bp, encoding="utf-8").read()

    flv = re.escape(flavor)
    if vc:
        def repl_vc(m):
            block = m.group(0)
            if re.search(r"versionCode\s+\d+", block):
                return re.sub(r"versionCode\s+\d+", f"versionCode {vc}", block, count=1)
            return block.rstrip("}") + f" versionCode {vc} }}"
        txt = re.sub(r"(?s)productFlavors\s*\{.*?" + flv + r"\s*\{.*?\}", repl_vc, txt, count=1)
    if vn:
        def repl_vn(m):
            block = m.group(0)
            if re.search(r'versionName\s+"[^"]*"', block):
                return re.sub(r'versionName\s+"[^"]*"', f'versionName "{vn}"', block, count=1)
            return block.rstrip("}") + f' versionName "{vn}" }}'
        txt = re.sub(r"(?s)productFlavors\s*\{.*?" + flv + r"\s*\{.*?\}", repl_vn, txt, count=1)
    open(bp, "w", encoding="utf-8").write(txt)
    return True, "已更新 flavor 版本号（请人工确认 flavor 块）"


def find_build_tools(android_home):
    bt = os.path.join(android_home, "build-tools")
    if not os.path.isdir(bt):
        return None
    vers = sorted(os.listdir(bt), reverse=True)
    return os.path.join(bt, vers[0]) if vers else None


def verify_apk(apk, build_tools):
    report = {}
    aapt2 = os.path.join(build_tools, "aapt2.exe" if is_windows() else "aapt2")
    rc, out = run([aapt2, "dump", "badging", apk])
    for line in out.splitlines():
        if line.startswith("package:"):
            m = re.search(r"versionCode='(\d+)'", line)
            if m: report["versionCode"] = int(m.group(1))
            m = re.search(r"versionName='([^']*)'", line)
            if m: report["versionName"] = m.group(1)
            m = re.search(r"name='([^']*)'", line)
            if m: report["package"] = m.group(1)
    za = os.path.join(build_tools, "zipalign.exe" if is_windows() else "zipalign")
    rc, out = run([za, "-c", "-v", "4", apk])
    report["zipalign_ok"] = ("Verification successful" in out)
    ak = os.path.join(build_tools, "apksigner.bat" if is_windows() else "apksigner")
    if is_windows():
        rc, out = run(["cmd", "/c", ak, "verify", "--verbose", apk])
    else:
        rc, out = run([ak, "verify", "--verbose", apk])
    schemes = {}
    for line in out.splitlines():
        for s in ["v1", "v2", "v3"]:
            if f"Verified using {s} scheme" in line:
                schemes[s] = ("true" in line)
    report["sign_schemes"] = schemes
    report["signed"] = any(v for v in schemes.values())
    if is_windows():
        rc, out = run(["cmd", "/c", "certutil", "-hashfile", apk, "MD5"])
        mm = re.search(r"([0-9a-fA-F]{32})", out)
        if mm: report["md5"] = mm.group(1)
    else:
        rc, out = run(["md5sum", apk])
        mm = re.search(r"([0-9a-fA-F]{32})", out)
        if mm: report["md5"] = mm.group(1)
    report["size_bytes"] = os.path.getsize(apk)
    return report


# 产物命名规范：<模块>-<flavor>-a<minSdk>-v<版本名>-vc<versionCode>-<体积>MB.apk
# 例：automotive-universal-a28-v1.3.0-vc1301-24.7MB.apk
MODULE_MIN_SDK = {"retro": 17, "legacy": 23, "mid81": 23, "automotive": 28}


def _min_sdk(module_dir, module):
    try:
        for fn in ("build.gradle", "build.gradle.kts"):
            p = os.path.join(module_dir, fn)
            if os.path.exists(p):
                with open(p, encoding="utf-8", errors="ignore") as f:
                    m = re.search(r"minSdk\s*(?:=|\s)\s*(\d+)", f.read())
                if m:
                    return m.group(1)
    except Exception:
        pass
    return MODULE_MIN_SDK.get(module, "x")


def canonical_name(args, apk, vrep, module_dir):
    def safe(s):
        return re.sub(r"[^\w.\-]+", "-", str(s)).strip("-") or "x"
    try:
        size_mb = round(os.path.getsize(apk) / 1048576, 1)
    except Exception:
        size_mb = 0
    ver = vrep.get("versionName") or args.version_name or "unknown"
    vc = vrep.get("versionCode") or args.version_code or 0
    sdk = _min_sdk(module_dir, args.module)
    return "%s-%s-a%s-v%s-vc%s-%sMB.apk" % (
        safe(args.module), safe(args.flavor), safe(sdk), safe(ver), safe(vc), size_mb)


def main():
    ap = argparse.ArgumentParser(description="Estrella 编译打包")
    ap.add_argument("--project", required=True)
    ap.add_argument("--module", default="automotive")
    ap.add_argument("--flavor", default="universal")
    ap.add_argument("--arch", default="")
    ap.add_argument("--ascii-build-dir", default=r"C:\Users\Administrator\DiPlayBuild\DiPlay-Universal")
    ap.add_argument("--java-home", default=r"C:\Users\Administrator\.workbuddy\binaries\jdk\jdk-17.0.20.1+1")
    ap.add_argument("--android-home", default=os.environ.get("ANDROID_HOME", r"C:\Users\Administrator\AppData\Local\Android\Sdk"))
    ap.add_argument("--version-code", type=int, default=0)
    ap.add_argument("--version-name", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--apk", default="")
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    result = {"steps": []}

    if not (args.skip_build and args.apk):
        # 真正构建路径：源码工程与 JDK 必须存在，否则给友好报错（而非底层复制失败）
        if not os.path.isdir(project):
            print(json.dumps({"status": "error", "msg": f"源码工程不存在: {project}"}, ensure_ascii=False, indent=2))
            sys.exit(2)
        if not os.path.isdir(args.java_home):
            print(json.dumps({"status": "error", "msg": f"JAVA_HOME 不存在: {args.java_home}"}, ensure_ascii=False, indent=2))
            sys.exit(2)

    if args.skip_build and args.apk:
        build_dir = os.path.dirname(os.path.abspath(args.apk))
        apk = os.path.abspath(args.apk)
        result["steps"].append("skip-build: 直接校验现有 APK")
    else:
        if any(ord(c) > 127 for c in project):
            build_dir = os.path.abspath(args.ascii_build_dir)
            copy_to_ascii(project, build_dir)
            result["steps"].append(f"中文路径→ASCII 副本: {build_dir}")
        else:
            build_dir = project
        module_dir = os.path.join(build_dir, args.module)
        if args.version_code or args.version_name:
            ok, msg = bump_version(module_dir, args.flavor, args.version_code, args.version_name)
            result["steps"].append(f"版本号: {msg}")
        env = dict(os.environ)
        env["JAVA_HOME"] = args.java_home
        env["ANDROID_HOME"] = args.android_home
        gradlew = os.path.join(build_dir, "gradlew.bat" if is_windows() else "gradlew")
        target = f":{args.module}:assemble{args.flavor.capitalize()}Release"
        rc, _ = run([gradlew, target], cwd=build_dir, env=env)
        if not rc:
            result["steps"].append(f"构建失败: {target}")
            print(json.dumps({"status": "build_failed", **result}, ensure_ascii=False, indent=2))
            sys.exit(1)
        apk = os.path.join(build_dir, args.module, "build", "outputs", "apk", args.flavor, "release",
                           f"{args.module}-{args.flavor}-release.apk")
        if not os.path.exists(apk):
            for dp, _, fs in os.walk(os.path.join(build_dir, args.module, "build", "outputs", "apk")):
                for f in fs:
                    if f.endswith(".apk"):
                        apk = os.path.join(dp, f)
        result["steps"].append(f"构建产物: {apk}")

    build_tools = find_build_tools(args.android_home)
    if not build_tools:
        print(json.dumps({"status": "error", "reason": "未找到 build-tools"}, ensure_ascii=False))
        sys.exit(1)
    tmp = tempfile.gettempdir()
    safe_apk = os.path.join(tmp, os.path.basename(apk))
    if any(ord(c) > 127 for c in apk):
        shutil.copy2(apk, safe_apk)
        apk_verify = safe_apk
    else:
        apk_verify = apk
    vrep = verify_apk(apk_verify, build_tools)
    result["verify"] = vrep

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        final = os.path.join(args.out, canonical_name(args, apk, vrep, os.path.join(build_dir, args.module)))
        shutil.copy2(apk, final)
        vrep["final_path"] = final
        result["steps"].append(f"交付: {final}")

    ok = vrep.get("signed") and vrep.get("zipalign_ok") and vrep.get("versionCode")
    result["status"] = "ok" if ok else "verify_failed"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
