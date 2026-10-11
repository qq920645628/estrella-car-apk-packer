# Estrella Car APK Packer · Estrella 车机 APK 自动修复打包 Skill

[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-zero%20(stdlib%20only)-brightgreen)]()
[![Platform](https://img.shields.io/badge/platform-Windows%20%2F%20Linux%20%2F%20macOS-lightgrey)]()
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> **English** · [中文](#中文说明) · An AI-agent Skill that turns "my car head unit + Android version + a symptom" into a working CarPlay/head-unit APK — with per-vehicle closed loops, a self-evolving knowledge base, and **builds that always run on your own machine**.
>
> 一个 AI Agent Skill：输入「车型 + 年份 + 车机系统 + 安卓底层版本 + 故障现象」，自动做兼容性判断、方案匹配、本地编译出包与经验回流。**每车独立闭环，出包一律在用户本机，云端只存知识库与源码快照。**

---

## Why this exists · 这个项目解决什么

Car head units are a compatibility minefield: Android 4.2.2 → 13, vendor ROMs with broken storage, no USB debugging, Adversary-in-the-middle Bluetooth stacks, non-ASCII build paths rejected by AGP, MFi/iAP2 handshake quirks… Fixing the same model twice is pure waste.

This Skill packages that hard-won experience into a repeatable workflow:

- **Never re-derive what's already known** — historical fixes, pitfalls and working artefacts are matched per vehicle.
- **Never blindly reuse across hardware** — a *variant signature* prevents applying a fix from one head-unit batch to a different one.
- **Never make the user install a toolchain before they can even see options** — ready-made APK candidates come first; compilation is the last resort.

---

## Key capabilities · 核心功能

| Capability | What it does |
|---|---|
| **Per-vehicle closed loop** 每车独立闭环 | submit → analyse → build → record. Each vehicle gets its own workflow run; nothing is assumed from other models. |
| **100% match → instant APK** 100% 命中直接出包 | If `vehicle_key` matches exactly **and** the variant signature agrees **and** an `artifact` (APK URL) is recorded, the Skill hands over the ready APK and ends — no analysis, no compile. |
| **Zero-setup candidates** 傻瓜式现成包 | `apk_catalog.py --no-source` lists ready-built APKs filtered by Android range / flavour / features, then asks the user to test them in order. No JDK, no SDK, no source tree required. |
| **Ready-made source from cloud** 云端源码快照 | No source locally? The Skill deploys a local build env (`setup_env.py`) then pulls a verified source snapshot (`pull_source.py`, sha256-checked) — **builds stay local, the server never compiles**. |
| **Environment preflight** 环境体检 | `preflight.py` checks JDK 17, Android SDK/NDK, Gradle wrapper, signing config and locates the project — with rules hot-served from the MCP server (update rules without redistributing the Skill). |
| **Self-evolving knowledge base** 自进化知识库 | Solutions ranked by success-rate-weighted confidence; failures auto-generate *negative lessons*; same-model-different-hardware auto-splits into variants; Android major-version drift alerts. |
| **Desensitisation before storage** 脱敏入库 | Whitelist + value-level scrubbing (keys/tokens/paths/`*.jks`) runs before anything is written, local or remote. |
| **Safety rails** 安全护栏 | Snapshot & rollback, pre-build static verification, hard termination on unfixable/high-risk cases, conflict queue that **never overwrites** history. |

---

## Advantages · 相比"直接编译"的优势

1. **Zero barrier to entry** — a normal user gets usable APK candidates without installing JDK/SDK/NDK or owning any source code.
2. **No decision hijacking** — the Skill presents candidates and lets the *user* test and confirm; it never silently picks a package.
3. **Anti-repetition by mechanism** — variant signatures, negative lessons and confidence weighting structurally prevent reusing a fix that failed on different hardware.
4. **Your machine does the building** — the server stores only the KB and a source snapshot (a few MB); no JDK/SDK/NDK on the server, no build load, and your source/signing keys never leave your PC.
5. **Rules hot-update** — preflight rules live on the server; changing detection rules needs no Skill redistribution (offline fallback included).
6. **Degrades gracefully** — MCP unreachable → transparent fallback to a local cache; nothing is lost, it syncs later.
7. **Zero dependencies** — pure Python standard library; runs anywhere Python runs.
8. **Auditable** — append-only audit log, daily KB backups, conflict queue with evidence-based arbitration suggestions.

---

## Architecture · 架构

```
┌──────────────────────── User machine (builds happen HERE) ────────────────────────┐
│  SKILL.md workflow                                                                │
│   Step 0  submit + 100% match check ──► artifact hit? ──► deliver ready APK ──┐    │
│   Step 1  analyse: recommend + pitfalls + ready-APK candidates (no compile)   │    │
│   Step 1.5 no source? setup_env.py (JDK17+SDK+NDK) → pull_source.py → build   │    │
│   Step 2  preflight.py (rules hot-loaded from MCP)                            │    │
│   Step 3-5 patch → verify → build_apk.py → APK                                │    │
│   Step 6-8 desensitise → upsert (with artifact) → deliver → feedback loop ─────┼──┐│
└──────────────────────────────────────────────────────────────────────────────┼──┼┘
                                                                               │  │
┌──────────────────────── MCP server (STORAGE ONLY, never builds) ──────────────┼──┼┐
│  /kb/query  /kb/upsert  /kb/recommend  /kb/report_outcome  /kb/conflicts      │  ││
│  /kb/preflight_spec (hot rules)  /kb/preflight_report (anonymous env profile)  │  ││
│  /kb/source + /kb/source/meta  ── source snapshot (sha256) ───────────────────┼──┘│
│  Token auth · rate limit · audit log · daily backup                            │   │
└───────────────────────────────────────────────────────────────────────────────┼───┘
                                          ▲                                     │
                                          └──────── per-vehicle records ────────┘
```

`server/server.js` is included so you can **self-host** the knowledge base in ~1 minute (Node.js, no database).

---

## Quick start · 快速开始

```bash
# 1) Put the folder under your agent's skills directory (~/.workbuddy/skills/estrella-car-apk-packer)
#    or just run the scripts directly.

# 2) Step 0 — 100% match check (recommended: pass full head-unit params)
python scripts/local_kb.py query \
  --key "长安|2021|逸动plus尊贵版1.4T|9.0" \
  --params '{"arch":"arm64","chip":"未知","android_base_version":"9.0"}'

# 3) No ready APK? List ready-made candidates (no source, no compile, no toolchain)
python scripts/apk_catalog.py --android 9.0 --feature 仪表 --no-source

# 4) Still nothing? Deploy env locally + pull source from cloud + build locally
python scripts/setup_env.py --dry-run          # inspect plan
python scripts/setup_env.py --accept-licenses --yes
python scripts/pull_source.py                  # ~/DiPlayBuild/DiPlay-Universal
python scripts/build_apk.py --project "C:/DiPlayBuild/DiPlay-Universal" --module automotive --flavor universal
```

**Environment variables**

| Variable | Purpose |
|---|---|
| `ESTRELLA_KB_URL` | MCP server base URL (default `http://106.12.162.224:7799`, or your self-hosted one) |
| `ESTRELLA_KB_TOKEN` | Auth token (**required** for the public copy — see [SECURITY.md](SECURITY.md)) |
| `ESTRELLA_KB_BACKEND` | `mcp` (default) or `local` (offline cache only) |
| `ESTRELLA_KB_DIR` | Local cache directory override |

### Self-host the MCP server · 自建知识库服务端

```bash
cd server
cp .env.example .env        # set KB_TOKEN
node server.js              # listens on :7799
curl -H "Authorization: Bearer $KB_TOKEN" http://127.0.0.1:7799/health
```

---

## Repository layout · 目录结构

```
SKILL.md                     Skill entry contract (workflow, hard rules, script index)
scripts/
  local_kb.py                KB client: query / upsert / recommend / report_outcome / conflicts / stats
  apk_catalog.py             Ready-made APK candidate matcher (no source, no compile)
  setup_env.py               Local build-env deployment (JDK17 + SDK + NDK)
  pull_source.py             Pull cloud source snapshot (sha256 verified)
  preflight.py               Environment & source preflight (rules hot-loaded from MCP)
  build_apk.py               Build + artefact verification
  verify.py                  Pre-build static safety checks
  snapshot.py                Snapshot & rollback
  desensitize.py             Desensitisation + whitelist sanitisation
references/                  workflow, pitfalls (27 real cases), schema, security, evolution, locked config
assets/                      vehicle summary & delivery checklist templates
data/kb.json                 local cache template
server/server.js             self-hostable MCP knowledge-base server
sync_github.py               one-command: sync the live Skill -> scrub token -> push to GitHub
api_push.py                  GitHub REST-API pusher (fallback when `git` cannot reach GitHub)
push.ps1 / push.bat          git-based pusher (auto-falls back to api_push.py)
```

---

## Keeping this repo in sync · 更新同步

After any change to the Skill, run one command from this directory:

```bash
# CMD
set GH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
python sync_github.py

# PowerShell
$env:GH_TOKEN = 'ghp_xxxxxxxxxxxxxxxxxxxx'
python sync_github.py
```

`sync_github.py` will:

1. copy `SKILL.md` + `scripts/` + `references/` + `assets/` from the live Skill
   (`~/.workbuddy/skills/estrella-car-apk-packer`, override with `--src`);
   `data/` is **never** synced, so local vehicle records stay private;
2. scrub the real KB token into `<YOUR_KB_TOKEN>` (auto-detected, no manual edit);
3. delete files that no longer exist in the Skill;
4. **skip the push entirely if nothing changed** (compares git blob SHAs against the remote tree);
5. otherwise commit and move `refs/heads/main`.

Useful flags: `--dry` (sync + diff only, never push), `--force` (push anyway).

`push.ps1` tries `git push` first and automatically falls back to `api_push.py`
when git cannot reach GitHub (corporate proxy / TLS interception).

---

## Hard rules (non-negotiable) · 硬性规则

The Skill is designed to be honest under pressure:

1. **No fabrication** — never invent a fix or a compatibility result.
2. **Say it when it can't be auto-fixed** — terminate instead of forcing a build.
3. **No malicious APKs, no bypassing head-unit security.**
4. **Never forge cloud records** — unknown models are labelled as such.
5. **Vehicle summary + risk report first** — user confirms before any modification.
6. **Desensitise on every exit path** (success / partial / failure).
7. **Feedback loop is mandatory** — user reports outcome; failures become negative lessons.
8. **Preflight before compiling** — blocking findings stop the pipeline.

---

## Legal & safety notice · 法律与安全提示

- **MFi**: the DiPlay source snapshot distributed by the upstream server contains an **experimental community MFi identity, not Apple-certified**. Distribution and use are subject to Apple's MFi Program terms — you are responsible for compliance in your jurisdiction.
- **CarPlay / Android Auto**: intended for legitimate interoperability on hardware you own. Do not use to bypass OEM security or DRM.
- **Signing keys**: the source snapshot excludes `*.jks` / `keystore.properties`; pulled sources build **debug** APKs by default. Place your own keystore under `release/` for signed, over-the-air-upgradable builds.
- **Token**: never commit your real token — see [SECURITY.md](SECURITY.md).

---

## 中文说明

### 这是什么

一个给 AI Agent 用的「车机 APK 自动修复打包」技能。用户输入**车型 / 年份 / 车机原厂系统版本 / 安卓底层版本 / 故障现象**，Skill 按固定流程完成：兼容性判断 → 历史方案匹配 →（必要时）本地编译出包 → 脱敏入库 → 成败回流自进化。

### 核心范式

- **每车独立闭环**：提交 → 分析 → 编译 → 记录。每个车型单独走一遍，绝不拿别的车型的结论套用。
- **出包一律在用户本地**：云端只存「车型知识库 + 源码快照」，**服务器不装 JDK/SDK/NDK、不参与编译**（避免服务器压力，也避免你的源码/签名私钥外泄）。
- **100% 相同特点才直接出包**：`vehicle_key` 精确命中 + 变体签名一致 + 记录里有 `artifact` 三个条件同时满足，才直接给现成 APK；否则进分析/编译。
- **不替用户拍板**：现成包给的是候选清单，按序号让用户自己测试确认。

### 功能一览

| 功能 | 说明 |
|---|---|
| 现成包候选（零门槛） | `apk_catalog.py --no-source`，按安卓区间/flavor/特性过滤，列出候选让用户依次试；不需要 JDK/SDK/源码 |
| 无源码也能出包 | `setup_env.py` 本机部署 JDK17+SDK+NDK → `pull_source.py` 从云端拉源码（sha256 校验、解包到 ASCII 路径）→ 本地 `build_apk.py` 编译 |
| 环境与源码体检 | `preflight.py` 真文件系统/真进程检测；检测规则由 MCP 云端下发，改规则无需重新分发 Skill，离线回落内置 |
| 变体签名防误套 | 同款车不同车机硬件（芯片/分辨率/权限限制等）自动算签名并拆分记录，`recommend` 在签名不一致时强制告警，禁止机械复用 |
| 成败加权置信度 | 方案按 `成功/(成功+失败) × 样本可信度` 排序；`confidence<0.6` 标【需验证】 |
| 自动吸取教训 | 用户回报失败 → 方案失败计数 +1，并自动生成「负向教训」坑，后续同类任务拉低置信度并高亮警示 |
| 安卓大版本漂移 | 跨大版本（如 9 → 11）告警并保留历史基线，绝不覆盖 |
| 冲突不覆盖 | 同问题不同修复步骤进冲突复核队列，附仲裁建议（keep_existing / apply_incoming / needs_review），证据不足强制人工 |
| 脱敏入库 | 白名单 + 值级扫描（密钥/令牌/用户路径/`*.jks`），写入前强制脱敏 |
| 快照回滚 / 预编译校验 / 审计备份 | 改前快照可回滚；打包前静态校验；写操作落审计日志，kb.json 每日备份保留 30 天 |

### 优点

1. **零门槛**：普通用户不用装工具链、不用有源码，先拿到能试的现成包。
2. **不拍板**：候选清单交给用户测，不做"我替你决定"。
3. **机制防重复犯错**：变体签名 + 负向教训 + 置信度加权，从机制上阻断"死板复用失败方案"。
4. **本地出包**：服务器零编译压力，源码与签名私钥不外泄。
5. **规则热更新**：体检规则云端下发，改规则不用重发 Skill 包；离线可用。
6. **优雅降级**：MCP 不可达自动回退本地缓存，数据不丢，恢复后补交。
7. **零依赖**：纯 Python 标准库。
8. **可审计**：审计日志 + 每日备份 + 冲突队列。

### 快速开始

```bash
# 第 0 步：车型提交 + 100% 命中检测（建议带完整车机参数）
python scripts/local_kb.py query --key "长安|2021|逸动plus尊贵版1.4T|9.0" \
  --params '{"arch":"arm64","chip":"未知","android_base_version":"9.0"}'

# 第 1 步：现成包候选（无需源码/编译）
python scripts/apk_catalog.py --android 9.0 --feature 仪表 --no-source

# 第 1.5 步：无源码 → 本机部署环境 + 云端拉源码 + 本地编译
python scripts/setup_env.py --accept-licenses --yes
python scripts/pull_source.py
python scripts/build_apk.py --project "C:/DiPlayBuild/DiPlay-Universal" --module automotive --flavor universal
```

自建服务端：`cd server && cp .env.example .env && node server.js`（监听 :7799）。

### 更新同步到 GitHub

Skill 每次改动后，在本目录执行一条命令即可同步到本仓库：

```bash
# CMD
set GH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
python sync_github.py

# PowerShell
$env:GH_TOKEN = 'ghp_xxxxxxxxxxxxxxxxxxxx'
python sync_github.py
```

它会：复制 `SKILL.md` + `scripts/` + `references/` + `assets/`（`data/` 不同步，本地车型记录不外泄）
→ 自动把真实 token 替换为 `<YOUR_KB_TOKEN>` → 删除 Skill 里已不存在的文件
→ **无变化则不推送**（与远端比对 blob SHA）→ 有变化才提交并移动 `main`。
可选参数：`--dry`（只同步+比对，不推送）、`--force`（强制推送）。

### 注意事项

- 公网副本中的 token 已替换为 `<YOUR_KB_TOKEN>` 占位符，请通过环境变量 `ESTRELLA_KB_TOKEN` 注入，不要把真实 token 提交到仓库。
- 源码快照含**社区实验性 MFi 身份（非 Apple 认证）**，分发与使用须遵守 Apple MFi 计划条款。
- 快照已排除签名密钥，拉到的源码默认出 debug 包；需要同签名覆盖升级请自备 keystore 放到 `release/`。

---

## License

MIT — see [LICENSE](LICENSE). Copyright (c) Estrella.
Core configuration constants (MCP endpoints, KB schema, desensitisation and verification rules) are intentionally locked; see `references/locked-config.md`.
