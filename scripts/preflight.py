#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Estrella 车机 APK 打包 Skill —— 第 0 步：环境与源码体检（preflight / doctor）

为什么要有这一步：
  编译链路 = 源码工程 + JDK17 + Android SDK(build-tools/platform/ndk) + Gradle wrapper + 签名配置。
  任一缺失，都会导致「前面几步都跑完了，到第 5 步才发现根本编不了」。
  更糟的是：过去这一步是靠模型「凭印象猜路径」，出现过 **源码实际存在却判定缺失** 的误判。
  本脚本用**真实文件系统调用 + 真实进程调用**把状态查清楚，模型只负责读报告，不再猜。

用法:
  python scripts/preflight.py                                  # 按默认约定路径体检
  python scripts/preflight.py --project "D:\\xxx\\DiPlay-Universal"
  python scripts/preflight.py --module retro                    # 顺带校验目标模块
  python scripts/preflight.py --module automotive --flavor universal
  python scripts/preflight.py --json                            # 机器可读
  python scripts/preflight.py --deep-scan                       # 加做有限深度候选扫描

退出码:
  0 = 可以编译            （允许存在 warn 级告警）
  1 = 存在阻断项           （缺源码 / JDK / SDK build-tools，必须先补环境）
  2 = 未预期错误
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

IS_WIN = sys.platform.startswith("win")
HOME = os.path.expanduser("~")
DESKTOP = os.path.join(HOME, "Desktop")

# 已知模块 → 适用车机（与 references/build-pipeline.md 一致）
KNOWN_MODULES = {
    "retro": "4.2.2~4.4 (minSdk17)",
    "legacy": "6.0~8.0 (minSdk23)",
    "mid81": "8.1 (minSdk23)",
    "automotive": "9.0+ (minSdk28)",
}
KNOWN_FLAVORS = ["universal", "lite", "cluster", "hd", "ble"]

# 工程指纹：命中任一即认为是 DiPlay-Universal 家族工程
FINGERPRINT_FILES = ["settings.gradle.kts", "settings.gradle"]
FINGERPRINT_MODULES = ["automotive", "retro", "legacy", "mid81", "common", "shared"]
FINGERPRINT_TEXT = "com.shilapi"  # namespace 特征串

SKIP_DIRS = {
    "AppData", "node_modules", ".git", ".gradle", "build", "$Recycle.Bin",
    "Windows", "Program Files", "Program Files (x86)", "ProgramData",
    "System Volume Information", ".cache", ".venv", "__pycache__",
}


# ---------------------------------------------------------------- 工具
# ---------------------------------------------------------------- MCP 规则下发（热更新）
# 检测动作必须在本地做（服务端扫不到你的磁盘），但「检测什么、阈值多少、候选路径有哪些」
# 由 MCP 统一下发：以后改规则只改服务端，客户端 Skill 不用重新分发/更新。
MCP_BASE = os.environ.get("ESTRELLA_MCP_BASE", "http://106.12.162.224:7799")
MCP_TOKEN = os.environ.get("ESTRELLA_MCP_TOKEN", "<YOUR_KB_TOKEN>")
SPEC_CACHE = os.path.join(HOME, ".estrella_kb", "preflight_spec.json")


def fetch_spec(use_mcp=True, timeout=8):
    """拉取云端体检规则；不可达则回落内置默认（保证离线可用）"""
    if not use_mcp:
        return None, "已禁用 MCP 规则（--no-mcp）"
    try:
        import urllib.request
        req = urllib.request.Request(MCP_BASE + "/kb/preflight_spec",
                                     headers={"Authorization": "Bearer " + MCP_TOKEN})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            spec = json.loads(r.read().decode("utf-8"))
        try:
            os.makedirs(os.path.dirname(SPEC_CACHE), exist_ok=True)
            open(SPEC_CACHE, "w", encoding="utf-8").write(json.dumps(spec, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
        return spec, f"云端规则 spec_version={spec.get('spec_version')}"
    except Exception as e:  # noqa: BLE001
        try:
            cached = json.loads(open(SPEC_CACHE, encoding="utf-8").read())
            return cached, f"MCP 不可达（{e}），已用本地缓存 spec_version={cached.get('spec_version')}"
        except Exception:  # noqa: BLE001
            return None, f"MCP 不可达且无缓存（{e}），使用脚本内置默认规则"


def report_env(profile):
    """匿名回传体检结果（只含 有无/版本号/缺什么，不含路径），用于发现普遍性缺失"""
    try:
        import urllib.request
        data = json.dumps(profile).encode("utf-8")
        req = urllib.request.Request(MCP_BASE + "/kb/preflight_report", data=data, method="POST",
                                     headers={"Authorization": "Bearer " + MCP_TOKEN,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def exename(name):
    return name + ".exe" if IS_WIN else name


def run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def has_non_ascii(p):
    return any(ord(c) > 127 for c in p)


def java_major(java_bin):
    """返回 JDK 主版本号，失败返回 None"""
    rc, out = run([java_bin, "-version"])
    if rc != 0:
        return None, out.strip().splitlines()[0] if out.strip() else "无法执行"
    m = re.search(r'version\s+"(\d+)(?:\.(\d+))?', out)
    if not m:
        return None, "无法解析版本输出"
    major = int(m.group(1))
    if major == 1:  # 1.8.0_xxx 旧式
        m2 = re.search(r'version\s+"1\.(\d+)', out)
        major = int(m2.group(1)) if m2 else 8
    return major, out.strip().splitlines()[0]


# ---------------------------------------------------------------- 1. 源码工程
def looks_like_project(d):
    if not os.path.isdir(d):
        return False
    has_settings = any(os.path.exists(os.path.join(d, f)) for f in FINGERPRINT_FILES)
    has_gradlew = os.path.exists(os.path.join(d, exename("gradlew"))) or os.path.exists(os.path.join(d, "gradlew"))
    return has_settings and has_gradlew


def project_score(d):
    """给候选工程打分：命中 DiPlay 特征越多分越高"""
    s = 0
    names = set()
    try:
        names = set(os.listdir(d))
    except Exception:  # noqa: BLE001
        return -1
    s += sum(3 for m in FINGERPRINT_MODULES if m in names)
    try:
        sp = os.path.join(d, "settings.gradle.kts")
        if not os.path.exists(sp):
            sp = os.path.join(d, "settings.gradle")
        if os.path.exists(sp):
            txt = open(sp, encoding="utf-8", errors="ignore").read()
            if FINGERPRINT_TEXT in txt or "DiPlay" in txt:
                s += 10
    except Exception:  # noqa: BLE001
        pass
    return s


def expand_path(tpl):
    """把云端规则里的 {home} / {desktop} / {env:XXX} 占位符展开成本机路径"""
    if not tpl:
        return ""

    def rep(m):
        k = m.group(1)
        if k.startswith("env:"):
            return os.environ.get(k[4:], "")
        return {"home": HOME, "desktop": DESKTOP}.get(k, "")

    out = re.sub(r"\{([a-zA-Z:]+)\}", rep, tpl)
    return os.path.normpath(out) if out else ""


def candidate_project_paths(explicit, spec=None):
    c = []
    if explicit:
        c.append(("用户指定 --project", explicit))
    if spec:  # 云端下发的候选优先（可热更新）
        for it in (spec.get("project", {}).get("candidates") or []):
            p = expand_path(it.get("path", ""))
            if p:
                c.append((it.get("label", "云端候选"), p))
    envp = os.environ.get("DIPLAY_PROJECT") or os.environ.get("ESTRELLA_PROJECT")
    if envp:
        c.append(("环境变量 DIPLAY_PROJECT", envp))
    c.append(("约定·中文源码根", os.path.join(DESKTOP, "123", "DiPlay-Universal")))
    c.append(("约定·ASCII 构建副本", os.path.join(HOME, "DiPlayBuild", "DiPlay-Universal")))
    c.append(("当前工作目录", os.path.abspath(os.getcwd())))
    # 去重保序
    seen, out = set(), []
    for label, p in c:
        k = os.path.normcase(os.path.abspath(p)) if p else ""
        if k and k in seen:
            continue
        seen.add(k)
        out.append((label, p))
    return out


def scan_projects(roots, max_depth=3, limit=8):
    """有限深度扫描，避免全盘遍历（深度受控 + 跳过系统/缓存目录）"""
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dp, dns, fns in os.walk(root):
            depth = dp.rstrip(os.sep).count(os.sep) - base_depth
            if depth >= max_depth:
                dns[:] = []
                continue
            dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
            if looks_like_project(dp):
                found.append(dp)
                if len(found) >= limit:
                    return found
    return found


def check_project(explicit, deep, spec=None):
    items = []
    chosen = None
    tried = []
    for label, p in candidate_project_paths(explicit, spec):
        p = os.path.abspath(p) if p else ""
        if p and looks_like_project(p):
            items.append({"item": "源码工程", "status": "ok", "detail": f"{p}（命中：{label}）"})
            chosen = p
            break
        # 单个候选未命中只是「试过」，不算阻断；只有全部候选都失败才阻断
        tried.append(f"{label}={p or '(空)'}")

    if not chosen and deep:
        hits = scan_projects([DESKTOP, HOME, os.path.join(HOME, "DiPlayBuild")], max_depth=3)
        hits.sort(key=project_score, reverse=True)
        if hits:
            best = hits[0]
            if project_score(best) > 0:
                items.append({"item": "源码工程·深度扫描命中", "status": "ok", "detail": best})
                chosen = best
            else:
                items.append({"item": "源码工程·深度扫描", "status": "warn",
                              "detail": "找到 Gradle 工程但不是 DiPlay 家族: " + "; ".join(hits[:3])})
        else:
            items.append({"item": "源码工程·深度扫描", "status": "missing",
                          "detail": f"在 {DESKTOP} / {HOME} 深度 3 以内未找到任何 Gradle 工程"})

    if not chosen:
        items.append({"item": "源码工程·结论", "status": "missing",
                      "detail": "未定位到可用源码工程",
                      "fix": "用 --project 显式指定；或把 DiPlay-Universal 放到 "
                             + os.path.join(HOME, "DiPlayBuild", "DiPlay-Universal")})
        return items, None

    # ASCII 检查（AGP 拒绝非 ASCII 工程路径）
    if has_non_ascii(chosen):
        items.append({"item": "工程路径 ASCII", "status": "warn",
                      "detail": f"{chosen} 含非 ASCII 字符，AGP 会拒绝；需构建副本",
                      "fix": f'robocopy "{chosen}" "{os.path.join(HOME, "DiPlayBuild", "DiPlay-Universal")}" /MIR /XD build .gradle'})
    else:
        items.append({"item": "工程路径 ASCII", "status": "ok", "detail": "纯 ASCII，可直接构建"})

    # gradlew
    gw = os.path.join(chosen, exename("gradlew"))
    if not os.path.exists(gw):
        gw = os.path.join(chosen, "gradlew")
    items.append({"item": "gradlew", "status": "ok" if os.path.exists(gw) else "missing",
                  "detail": gw if os.path.exists(gw) else "未找到 gradlew",
                  "fix": "" if os.path.exists(gw) else "工程不完整，重新克隆/拷贝完整工程"})

    # wrapper 版本 + 本地缓存（决定首次构建要不要联网下载）
    wp = os.path.join(chosen, "gradle", "wrapper", "gradle-wrapper.properties")
    dist_url, cached = "", False
    if os.path.exists(wp):
        txt = open(wp, encoding="utf-8", errors="ignore").read()
        m = re.search(r"distributionUrl=.*?gradle-([\d.]+)-", txt)
        if m:
            dist_url = m.group(1)
            dists = os.path.join(HOME, ".gradle", "wrapper", "dists")
            if os.path.isdir(dists):
                cached = any(d.startswith("gradle-" + dist_url) for d in os.listdir(dists))
    items.append({"item": "Gradle wrapper", "status": "ok" if dist_url else "warn",
                  "detail": (f"版本 {dist_url}" if dist_url else "未读到 wrapper 版本")
                            + ("，本地已缓存" if cached else "，本地未缓存（首次构建需联网下载）"),
                  "fix": "" if cached else "保持联网，或预置 ~/.gradle/wrapper/dists 缓存"})

    # 签名配置
    ks = os.path.join(chosen, "release", "diplay-release.jks")
    kp = os.path.join(chosen, "release", "keystore.properties")
    if os.path.exists(ks) and os.path.exists(kp):
        items.append({"item": "签名配置", "status": "ok", "detail": "jks + keystore.properties 齐全"})
    else:
        miss = [x for x, ok in (("diplay-release.jks", os.path.exists(ks)),
                                ("keystore.properties", os.path.exists(kp))) if not ok]
        items.append({"item": "签名配置", "status": "warn",
                      "detail": "缺 " + "、".join(miss),
                      "fix": "补齐 release/ 下签名文件，否则只能出 debug 包（车机覆盖升级会失败）"})

    return items, chosen


# ---------------------------------------------------------------- 2. JDK
def check_jdk(explicit, spec=None):
    jspec = (spec or {}).get("jdk", {})
    min_major = int(jspec.get("min_major", 17))
    max_major = int(jspec.get("max_major", 24))
    cands = []
    if explicit:
        cands.append(("用户指定 --java-home", explicit))
    if os.environ.get("JAVA_HOME"):
        cands.append(("环境变量 JAVA_HOME", os.environ["JAVA_HOME"]))
    for tpl in (jspec.get("search_win") if IS_WIN else jspec.get("search_nix")) or []:
        p = expand_path(tpl)
        if p:
            cands.append(("云端规则·" + tpl, p))
    if not (jspec.get("search_win") if IS_WIN else jspec.get("search_nix")):
        cands.append(("约定默认", os.path.join(HOME, ".workbuddy", "binaries", "jdk")))
        for base in [r"C:\Program Files\Java",
                     r"C:\Program Files\Eclipse Adoptium",
                     r"C:\Program Files\Android\Android Studio\jbr",
                     r"C:\Program Files\Microsoft"]:
            if os.path.isdir(base):
                try:
                    for d in sorted(os.listdir(base), reverse=True):
                        if d.lower().startswith(("jdk", "jbr", "jdk-")):
                            cands.append((f"{base} → {d}", os.path.join(base, d)))
                except Exception:  # noqa: BLE001
                    pass
    else:
        for base in ["/usr/lib/jvm", "/opt/java", "/Library/Java/JavaVirtualMachines"]:
            if os.path.isdir(base):
                try:
                    for d in sorted(os.listdir(base), reverse=True):
                        cands.append((f"{base} → {d}", os.path.join(base, d)))
                except Exception:  # noqa: BLE001
                    pass

    # PATH 里的 java
    wj = shutil.which("java")
    best = None
    for label, d in cands:
        if not d:
            continue
        # 传入的可能是 "...\binaries\jdk" 这类父目录，往下找一层
        search = [d]
        try:
            if os.path.isdir(d):
                subs = [os.path.join(d, s) for s in sorted(os.listdir(d), reverse=True)]
                search += [s for s in subs if os.path.isdir(s)]
        except Exception:  # noqa: BLE001
            pass
        for sd in search[:12]:
            exe = os.path.join(sd, "bin", exename("java"))
            if not os.path.exists(exe):
                exe = os.path.join(sd, exename("java"))
            if os.path.exists(exe):
                major, raw = java_major(exe)
                rec = {"item": "JDK·" + label,
                       "status": "ok" if major and min_major <= major <= max_major else ("warn" if major else "missing"),
                       "detail": f"{exe} → {raw}" if major else f"{exe} 无法获取版本（{raw}）",
                       "path": os.path.dirname(os.path.dirname(exe)) if exe.endswith(os.path.join("bin", exename("java"))) else sd,
                       "major": major}
                if best is None or (rec["status"] == "ok" and best["status"] != "ok"):
                    best = rec
                if rec["status"] == "ok":
                    break
        if best and best["status"] == "ok":
            break

    if wj and (not best or best["status"] != "ok"):
        major, raw = java_major(wj)
        if major and min_major <= major <= max_major:
            best = {"item": "JDK·PATH", "status": "ok", "detail": f"{wj} → {raw}", "path": "", "major": major}

    items = [best] if best else []
    if not best:
        items.append({"item": f"JDK{min_major}", "status": "missing",
                      "detail": f"未找到可用 JDK（{jspec.get('reason','AGP9/Gradle9 要求 JDK 17+')}）",
                      "fix": jspec.get("install", "安装 JDK 17 并设置 JAVA_HOME")})
    elif best["status"] == "warn":
        items[0]["fix"] = f"当前主版本 {best.get('major')}，要求 {min_major}~{max_major}；{jspec.get('install','')}"
    return items, (best or {}).get("path", "")


# ---------------------------------------------------------------- 3. Android SDK
def check_sdk(explicit, need_api=37, spec=None):
    sspec = (spec or {}).get("sdk", {})
    inst = sspec.get("install", {})
    cands = []
    if explicit:
        cands.append(("用户指定 --android-home", explicit))
    for tpl in sspec.get("roots") or []:
        p = expand_path(tpl)
        if p:
            cands.append(("云端规则·" + tpl, p))
    for k in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(k):
            cands.append((f"环境变量 {k}", os.environ[k]))
    cands.append(("约定默认", os.path.join(HOME, "AppData", "Local", "Android", "Sdk")))
    cands.append(("备选", os.path.join(os.path.dirname(HOME), "Android", "Sdk")))
    if os.path.isdir(r"C:\Android"):
        cands.append(("C:\\Android", r"C:\Android"))
    seen, cc = set(), []
    for l, d in cands:
        k = os.path.normcase(os.path.abspath(d)) if d else ""
        if k and k in seen:
            continue
        seen.add(k)
        cc.append((l, d))
    cands = cc

    sdk = None
    items = []
    for label, d in cands:
        if d and os.path.isdir(d) and (os.path.isdir(os.path.join(d, "platforms"))
                                       or os.path.isdir(os.path.join(d, "build-tools"))
                                       or os.path.isdir(os.path.join(d, "cmdline-tools"))):
            sdk = d
            items.append({"item": "Android SDK·" + label, "status": "ok", "detail": d})
            break
    if not sdk:
        tried = "；".join(f"{l}={p}" for l, p in cands)
        items.append({"item": "Android SDK", "status": "missing",
                      "detail": f"未找到 SDK 根（已试：{tried}）",
                      "fix": "安装 Android Studio，或装 command-line-tools 后 "
                             + " / ".join([inst.get("platform", ""), inst.get("build_tools", "")]).strip(" /")})
        return items, None, None

    # platforms
    pdir = os.path.join(sdk, "platforms")
    apis = []
    if os.path.isdir(pdir):
        apis = sorted([int(m.group(1)) for d in os.listdir(pdir)
                       if (m := re.match(r"android-(\d+)$", d))], reverse=True)
    if need_api in apis:
        items.append({"item": f"SDK Platform android-{need_api}", "status": "ok", "detail": f"已安装（可用 API: {apis[:5]}）"})
    elif apis:
        items.append({"item": f"SDK Platform android-{need_api}", "status": "missing",
                      "detail": f"缺 android-{need_api}，当前只有 {apis[:5]}",
                      "fix": f'sdkmanager --install "platforms;android-{need_api}"'})
    else:
        items.append({"item": "SDK Platform", "status": "missing", "detail": "platforms 目录为空",
                      "fix": f'sdkmanager --install "platforms;android-{need_api}"'})

    # build-tools
    bt_root = os.path.join(sdk, "build-tools")
    bt_vers = sorted(os.listdir(bt_root), reverse=True) if os.path.isdir(bt_root) else []
    if bt_vers:
        items.append({"item": "build-tools（aapt2/zipalign/apksigner）", "status": "ok",
                      "detail": f"{bt_vers[0]}（共 {len(bt_vers)} 个版本，校验用最新）"})
    else:
        items.append({"item": "build-tools", "status": "missing", "detail": "未安装，APK 校验无法进行",
                      "fix": inst.get("build_tools", 'sdkmanager --install "build-tools;37.0.0"')})

    # ndk（部分模块需要；缺失不阻断，标 warn）
    ndk_root = os.path.join(sdk, "ndk")
    ndk_vers = sorted(os.listdir(ndk_root), reverse=True) if os.path.isdir(ndk_root) else []
    if ndk_vers:
        items.append({"item": "NDK", "status": "ok", "detail": ndk_vers[0]})
    else:
        items.append({"item": "NDK", "status": "warn", "detail": "未安装（含 native 代码的模块会失败）",
                      "fix": inst.get("ndk", 'sdkmanager --install "ndk;29.0.14206865"')})

    # cmdline-tools（缺失则无法自助补装）
    ct = os.path.join(sdk, "cmdline-tools")
    has_ct = os.path.isdir(ct) and any(os.path.isdir(os.path.join(ct, d)) for d in os.listdir(ct)) if os.path.isdir(ct) else False
    if not has_ct:
        items.append({"item": "cmdline-tools", "status": "warn", "detail": "未安装，无法用 sdkmanager 自助补装组件",
                      "fix": inst.get("cmdline_tools", "Android Studio → SDK Manager → SDK Tools → Android SDK Command-line Tools")})

    return items, sdk, (bt_vers[0] if bt_vers else None)


# ---------------------------------------------------------------- 4. 模块 / flavor
def check_target(project, module, flavor):
    if not project:
        return []
    items = []
    mdir = os.path.join(project, module)
    if module in KNOWN_MODULES:
        if os.path.isdir(mdir):
            items.append({"item": f"目标模块 :{module}", "status": "ok",
                          "detail": f"{mdir}（适用 {KNOWN_MODULES[module]}）"})
        else:
            items.append({"item": f"目标模块 :{module}", "status": "missing",
                          "detail": f"工程内没有 {module} 目录",
                          "fix": "可用模块: " + ", ".join(KNOWN_MODULES)})
    else:
        items.append({"item": f"目标模块 :{module}", "status": "warn",
                      "detail": f"未知模块，已知: {', '.join(KNOWN_MODULES)}"})
    if flavor:
        bp = os.path.join(mdir, "build.gradle.kts")
        if os.path.exists(bp):
            txt = open(bp, encoding="utf-8", errors="ignore").read()
            ok = re.search(r'create\("' + re.escape(flavor) + r'"\)', txt) is not None or flavor in KNOWN_FLAVORS
            items.append({"item": f"flavor {flavor}", "status": "ok" if ok else "warn",
                          "detail": "在 build.gradle.kts 中命中" if ok else "未在 build.gradle.kts 找到该 flavor"})
    return items


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="Estrella 环境与源码体检（第 0 步）")
    ap.add_argument("--project", default="")
    ap.add_argument("--module", default="automotive")
    ap.add_argument("--flavor", default="universal")
    ap.add_argument("--java-home", default="")
    ap.add_argument("--android-home", default="")
    ap.add_argument("--compile-sdk", type=int, default=37)
    ap.add_argument("--deep-scan", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-mcp", action="store_true", help="不拉云端规则，用内置默认（离线可用）")
    ap.add_argument("--report", action="store_true", help="匿名回传体检结果到 MCP（不含路径）")
    args = ap.parse_args()

    # ① 先拉云端规则（热更新点）；不可达自动回落内置默认
    spec, spec_note = fetch_spec(not args.no_mcp)
    csdk = int((spec or {}).get("sdk", {}).get("compile_sdk", args.compile_sdk))

    checks = []
    p_items, project = check_project(args.project, args.deep_scan, spec)
    checks += p_items
    j_items, java_home = check_jdk(args.java_home, spec)
    checks += j_items
    s_items, sdk, bt = check_sdk(args.android_home, csdk, spec)
    checks += s_items
    checks += check_target(project, args.module, args.flavor)

    blocking = [c for c in checks if c["status"] == "missing"]
    warns = [c for c in checks if c["status"] == "warn"]

    jdk_major = next((c.get("major") for c in j_items if c.get("major")), None)
    miss_platforms = [c.get("detail", "") for c in s_items if "SDK Platform" in c["item"] and c["status"] == "missing"]
    miss_components = [("build-tools" if "build-tools" in c["item"] else c["item"])
                       for c in s_items if c["status"] == "missing" and "SDK Platform" not in c["item"]]

    report = {
        "ready": not blocking,
        "spec_version": (spec or {}).get("spec_version"),
        "spec_source": spec_note,
        "project": project,
        "java_home": java_home,
        "android_home": sdk,
        "build_tools": bt,
        "module": args.module,
        "flavor": args.flavor,
        "blocking": blocking,
        "warnings": warns,
        "checks": checks,
    }

    # ② 匿名回传（只含有无/版本号/缺什么），用于发现普遍性环境缺失
    if args.report:
        rr = report_env({
            "os": "windows" if IS_WIN else sys.platform,
            "jdk_major": jdk_major,
            "project_found": bool(project),
            "module": args.module,
            "missing_platforms": [f"android-{csdk}"] if miss_platforms else [],
            "missing_components": miss_components,
            "blocking": [c.get("item") for c in blocking],
            "ready": not blocking,
        })
        report["mcp_report"] = rr

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not blocking else 1

    icon = {"ok": "✅", "warn": "⚠️ ", "missing": "❌"}
    print("=" * 68)
    print(" Estrella 车机 APK 打包 · 第 0 步 环境与源码体检")
    print("=" * 68)
    print(f" 规则来源: {spec_note}")
    print("-" * 68)
    for c in checks:
        print(f"{icon.get(c['status'], '·')} [{c['status'].upper():7}] {c['item']}")
        print(f"          {c.get('detail','')}")
        if c.get("fix") and c["status"] != "ok":
            print(f"          → 修复: {c['fix']}")
    print("-" * 68)
    if blocking:
        print(f"结论：❌ 不可编译 —— {len(blocking)} 项阻断，{len(warns)} 项告警")
        print("      先按上面「修复」提示补齐环境/源码，再进入第 1 步。")
    elif warns:
        print(f"结论：⚠️  可以编译（{len(warns)} 项告警，不影响主链路，但需留意）")
    else:
        print("结论：✅ 环境齐备，可以直接编译")
    if project:
        print(f"\n下一步：python scripts/verify.py --project \"{project}\"")
    if report.get("mcp_report", {}).get("advice"):
        print("\nMCP 建议：")
        for a in report["mcp_report"]["advice"]:
            print("  · " + a)
    print("=" * 68)
    return 0 if not blocking else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print("PREFLIGHT_ERROR: " + str(e))
        sys.exit(2)
