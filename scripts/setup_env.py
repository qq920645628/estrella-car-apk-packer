#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup_env.py —— 在用户本机部署 DiPlay 本地编译环境（出包在本地，云端不编译）

职责（仅在本机执行，绝不触碰服务器编译链）：
  1. 检测 JDK（需 17+）；缺失则装 Temurin JDK17（优先 winget，回退便携 ZIP）。
  2. 检测 Android SDK；缺失则下载 commandline-tools 并落到 SDK_ROOT。
  3. 用 sdkmanager 安装所需组件：platform-tools、platforms;android-34/36/37、
     build-tools;37.0.0、ndk;29（项目 shared/legacy/mid81 需要 NDK）。
  4. 接受 SDK licenses（--accept-licenses 时自动 y）。
  5. 写入用户级环境变量 JAVA_HOME / ANDROID_HOME（便于后续 build_apk 直接可用）。

注意：
  - 需要联网（dl.google.com / adoptium.net / winget 源），约需数 GB 磁盘与下载时间。
  - 仅 Windows 路径逻辑经过设计；macOS/Linux 仅做 JDK/SDK 检测提示，不自动装。
  - 本机若已具备环境，脚本走快速跳过路径，不重复下载。

用法：
  python3 setup_env.py                 # 交互式：先检测，打印方案，确认后安装
  python3 setup_env.py --dry-run       # 只检测并打印方案，不下载/不安装
  python3 setup_env.py --accept-licenses --yes   # 全自动（y 自动确认下载）
  python3 setup_env.py --sdk-root D:/Android/Sdk
"""
import os
import sys
import json
import shutil
import argparse
import subprocess
import urllib.request
import zipfile
import tempfile

# ----------------------- 目标组件（与 server.js PREFLIGHT_SPEC 对齐） -----------------------
JDK_MIN_MAJOR = 17
SDK_COMPILE = "37"
BUILD_TOOLS = "37.0.0"
NDK_VERSION = "29.0.14206865"   # 优先；项目也兼容 28.0.13004108
PLATFORMS = ["34", "36", "37"]
CMDLINE_TOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
TEMURIN_URL = "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/zip"


def log(s):
    print(s, flush=True)


def run(cmd, **kw):
    log("  $ " + " ".join(cmd) if isinstance(cmd, list) else "  $ " + str(cmd))
    return subprocess.run(cmd, **kw)


def detect_jdk():
    """返回 (major, java_home) 或 (None, None)"""
    # 1) JAVA_HOME
    jh = os.environ.get("JAVA_HOME")
    cand = []
    if jh:
        cand.append(jh)
    # 2) 常见位置
    for base in [
        os.path.expanduser("~/.workbuddy/binaries/jdk"),
        r"C:\Program Files\Java",
        r"C:\Program Files\Eclipse Adoptium",
        r"C:\Program Files\Microsoft",
        os.path.expanduser("~/jdk"),
    ]:
        if os.path.isdir(base):
            for root, dirs, files in os.walk(base):
                if os.path.basename(root) in ("bin",) and os.path.exists(os.path.join(root, "java.exe")):
                    cand.append(os.path.dirname(root))
                if "java.exe" in files and os.path.basename(root) != "bin":
                    cand.append(root)
    for d in cand:
        java = os.path.join(d, "bin", "java.exe") if os.path.isdir(os.path.join(d, "bin")) else os.path.join(d, "java.exe")
        if not os.path.exists(java):
            continue
        try:
            out = subprocess.run([java, "-version"], capture_output=True, text=True).stderr
            m = __import__("re").search(r'version "(\d+)', out)
            if m:
                return int(m.group(1)), d
        except Exception:
            pass
    # 3) PATH 里直接 java
    p = shutil.which("java") or shutil.which("java.exe")
    if p:
        try:
            out = subprocess.run([p, "-version"], capture_output=True, text=True).stderr
            m = __import__("re").search(r'version "(\d+)', out)
            if m:
                home = os.path.dirname(os.path.dirname(p))
                return int(m.group(1)), home
        except Exception:
            pass
    return None, None


def detect_sdk():
    for v in [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
              os.path.expanduser(r"~/AppData/Local/Android/Sdk"), r"C:\Android\Sdk"]:
        if v and os.path.isdir(v):
            return v
    return None


def sdkmanager(sdk_root, java_home, args, accept=False):
    sm = os.path.join(sdk_root, "cmdline-tools", "latest", "bin", "sdkmanager.bat")
    if not os.path.exists(sm):
        sm = os.path.join(sdk_root, "cmdline-tools", "latest", "bin", "sdkmanager")
    env = dict(os.environ)
    if java_home:
        env["JAVA_HOME"] = java_home
        env["PATH"] = os.path.join(java_home, "bin") + os.pathsep + env.get("PATH", "")
    if accept:
        p = subprocess.run([sm] + args, input="y\n", text=True, env=env, capture_output=True)
    else:
        p = subprocess.run([sm] + args, env=env, capture_output=True, text=True)
    if p.returncode != 0:
        log("  ⚠️ sdkmanager 返回非零：\n" + (p.stderr or p.stdout)[-800:])
    return p.returncode == 0


def install_jdk_winget():
    if shutil.which("winget"):
        return run(["winget", "install", "-e", "--id", "Microsoft.OpenJDK.17", "-s", "winget", "--accept-package-agreements", "--accept-source-agreements"]).returncode == 0
    return False


def install_jdk_portable(dest):
    os.makedirs(dest, exist_ok=True)
    zip_path = os.path.join(tempfile.gettempdir(), "temurin17.zip")
    log("  下载 Temurin JDK17（便携）...")
    urllib.request.urlretrieve(TEMURIN_URL, zip_path)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    # 目录名形如 jdk-17.x.x+xx；把 JAVA_HOME 指向它
    for d in os.listdir(dest):
        if d.startswith("jdk-17"):
            return os.path.join(dest, d)
    return None


def install_cmdline_tools(sdk_root):
    os.makedirs(sdk_root, exist_ok=True)
    zip_path = os.path.join(tempfile.gettempdir(), "cmdline-tools.zip")
    log("  下载 Android commandline-tools...")
    urllib.request.urlretrieve(CMDLINE_TOOLS_URL, zip_path)
    # 解压出 cmdline-tools/ 目录，重命名为 latest 落到 sdk_root/cmdline-tools/latest
    tmp = os.path.join(tempfile.gettempdir(), "cmdline_tools_extract")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp)
    latest = os.path.join(sdk_root, "cmdline-tools", "latest")
    os.makedirs(os.path.dirname(latest), exist_ok=True)
    if os.path.isdir(latest):
        shutil.rmtree(latest)
    shutil.move(os.path.join(tmp, "cmdline-tools"), latest)
    shutil.rmtree(tmp, ignore_errors=True)
    return os.path.exists(os.path.join(latest, "bin", "sdkmanager.bat")) or os.path.exists(os.path.join(latest, "bin", "sdkmanager"))


def set_user_env(**kv):
    # Windows：写入用户级环境变量（无需管理员）
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes
            # 用 setx 更简单稳妥
            for k, v in kv.items():
                run(["setx", k, v])
            log("  ✓ 已写入用户环境变量：" + ", ".join(kv.keys()) + "（新开终端生效）")
        except Exception as e:
            log("  ⚠️ 写环境变量失败（%s），请手动设置：%s" % (e, kv))
    else:
        log("  ⚠️ 非 Windows：请手动将以下变量加入 shell profile：%s" % kv)


def main():
    ap = argparse.ArgumentParser(description="在用户本机部署 DiPlay 本地编译环境")
    ap.add_argument("--dry-run", action="store_true", help="只检测并打印方案，不安装")
    ap.add_argument("--yes", action="store_true", help="自动确认下载/安装（不交互）")
    ap.add_argument("--accept-licenses", action="store_true", help="自动接受 SDK licenses")
    ap.add_argument("--sdk-root", default=os.path.expanduser(r"~/AppData/Local/Android/Sdk"), help="Android SDK 根目录")
    ap.add_argument("--jdk-dest", default=os.path.expanduser(r"~/jdk"), help="便携 JDK 安装目录（winget 不可用时）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    plan = {"jdk": None, "sdk": None, "actions": []}
    major, jdk_home = detect_jdk()
    sdk_root = detect_sdk()

    log("=" * 60)
    log(" DiPlay 本地编译环境检测")
    log("=" * 60)
    if major:
        log("  ✓ JDK 已安装：%s（JAVA_HOME=%s）" % (major, jdk_home))
    else:
        log("  ✗ 未检测到 JDK17+ → 需要安装")
        plan["actions"].append("install_jdk")
    if sdk_root:
        log("  ✓ Android SDK 已存在：%s" % sdk_root)
    else:
        log("  ✗ 未检测到 Android SDK → 需要安装（将装到 %s）" % args.sdk_root)
        plan["actions"].append("install_sdk")

    plan["jdk"] = {"major": major, "home": jdk_home}
    plan["sdk"] = {"root": sdk_root}

    if args.dry_run:
        log("  [dry-run] 不执行安装。方案动作：" + (", ".join(plan["actions"]) or "无需安装"))
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    if plan["actions"] and not args.yes:
        ans = input("  将执行上述安装（需联网下载数 GB），确认？(y/N) ").strip().lower()
        if ans != "y":
            log("  已取消。")
            return 0

    ok = True
    # ---- 1) JDK ----
    if "install_jdk" in plan["actions"]:
        log("  [1/3] 安装 JDK17 ...")
        if install_jdk_winget():
            major, jdk_home = detect_jdk()
        if not major:
            jdk_home = install_jdk_portable(args.jdk_dest)
            if jdk_home:
                major = 17
        if major:
            set_user_env(JAVA_HOME=jdk_home)
            log("  ✓ JDK 就绪：%s" % jdk_home)
        else:
            log("  ✗ JDK 安装失败，请手动安装 JDK17 后重试"); ok = False

    # ---- 2) cmdline-tools ----
    if "install_sdk" in plan["actions"] and ok:
        log("  [2/3] 安装 Android commandline-tools ...")
        if install_cmdline_tools(args.sdk_root):
            sdk_root = args.sdk_root
            log("  ✓ cmdline-tools 已安装")
        else:
            log("  ✗ cmdline-tools 安装失败"); ok = False

    # ---- 3) 组件 + licenses ----
    if sdk_root and ok:
        log("  [3/3] 安装 SDK 组件（platform-tools / platforms %s / build-tools %s / ndk %s）..." %
            (",".join(PLATFORMS), BUILD_TOOLS, NDK_VERSION))
        comps = ["platform-tools"]
        comps += ["platforms;android-%s" % p for p in PLATFORMS]
        comps += ["build-tools;%s" % BUILD_TOOLS, "ndk;%s" % NDK_VERSION]
        sdkmanager(sdk_root, jdk_home, comps, accept=args.accept_licenses)
        if args.accept_licenses:
            sdkmanager(sdk_root, jdk_home, ["--licenses"], accept=True)
        set_user_env(ANDROID_HOME=sdk_root)
        log("  ✓ 组件安装完成（如需 NDK 28 可再跑：sdkmanager \"ndk;28.0.13004108\"）")

    if ok:
        log("=" * 60)
        log(" 本地编译环境就绪 ✅")
        log("   JDK17  : %s" % jdk_home)
        log("   SDK     : %s" % (sdk_root or args.sdk_root))
        log("  下一步  ：pull_source 拉源码 → build_apk 本地出包")
        log("=" * 60)
        if args.json:
            print(json.dumps({"ok": True, "jdk_home": jdk_home, "sdk_root": sdk_root}, ensure_ascii=False, indent=2))
        return 0
    log("  ✗ 环境部署未完成，请查看上方错误。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
