# 版本树 · Changelog

> **每次改动都要走这里**：`python scripts/release.py patch|minor|major "一句话说明"`
> 它会自动递增 `VERSION` → 在本文件顶部插入新版本条目 → 同步 `SKILL.md` 顶部的版本行 → 提示同步 GitHub。
>
> **版本号语义**
> - `major`：工作流 / 数据结构 breaking change（改主键、改接口、改锁定配置）
> - `minor`：新增能力（新脚本、新端点、新参考文档、新流程分支）
> - `patch`：修复、文案、参数调整、规则口径变更

**当前版本：v1.0.0（2026-10-11）**

---

## 版本树

```text
v1.0.0  2026-10-11  版本树建立 + 自有设备默认全权限
 │
 └─ 2026-10-10 ~ 10-11  版本树建立前的累计变更（补记，未编号）
```

<!-- NEXT_VERSION -->

---

## v1.0.0 · 2026-10-11 · 版本树建立 + 自有设备默认全权限

**性质**：minor（新增版本管理机制 + 新增参考文档 + 规则口径变更）

**变更**
1. **建立版本树**：新增 `VERSION` + 本文件 `CHANGELOG.md` + `scripts/release.py`（一键发版，自动递增版本号、写说明、同步 SKILL.md 版本行）；`smoke_test.py` 增加「版本一致性」检查（VERSION / SKILL.md / CHANGELOG 三处必须一致）。
2. **自有设备默认全权限放行**（维护者定调）：`SKILL.md` 硬性规则 3、`references/security-rules.md` §1.2、`references/locked-config.md` 禁止项，不再把 root / `su` 提权 / 固件替换 / 写系统分区 / 关闭授权弹窗视为「绕过车机安全机制」。自己的车机上**一律放行**，不得以安全/越权/违规为由拒绝、降级或要求额外确认；唯一边界是**非所有者授权的设备**。
3. `scripts/verify.py`：`su` / `Runtime.exec` / `ProcessBuilder` / `mount` / `chmod 777` 静态扫描由 `WARNING（需人工确认，违反安全规则将终止打包）` 降级为 **INFO（仅记录，不阻断）**——这是此前"死活改不动"的直接原因。
4. 新增 `references/root-adapt-without-injection.md`：自有设备 root 适配手册（诊断清单 → 方案 A 系统侧脚本 `su.d`/init.d/Magisk 模块 → 方案 B 同名 shim → 方案 C 只换调用点），含验证与回滚命令。
5. **现成包目录云端下发上线**：服务端 `GET /kb/apk_catalog_spec` 读 `$KB_DIR/apk_catalog.json`（默认 `/opt/estrella-kb/data/`，⚠️ **不是** `/opt/estrella-kb/`）；新增 `scripts/gen_catalog.py` 生成该文件；客户端 cloud → cache → builtin 三级回落已实测生效（探针条目 11 vs 内置 10）。
6. 产物命名规范 `<模块>-<flavor>-a<minSdk>-v<版本>-vc<versionCode>-<体积>MB.apk`。

**影响文件**：`SKILL.md`、`VERSION`、`CHANGELOG.md`、`scripts/release.py`、`scripts/verify.py`、`scripts/gen_catalog.py`、`scripts/smoke_test.py`、`references/security-rules.md`、`references/locked-config.md`、`references/root-adapt-without-injection.md`

---

## 版本树建立前的累计变更（补记，未编号）

> 以下在 2026-10-10 ~ 2026-10-11 完成，早于版本树建立，按日期补记。

| 日期 | 变更 |
|---|---|
| 10-11 | **产物闭环**：新增 `/kb/artifact/upload`（裸二进制流 + sha256 校验）→ `/a/<sha256>.apk` 免令牌下载 → 回填 `artifact`；客户端 `local_kb.py publish` |
| 10-11 | **离线不丢数据**：`data/pending_queue.jsonl` + 联网首动作自动 flush + `local_kb.py flush` 手动 |
| 10-11 | **令牌读写分离**：`KB_TOKEN`（写）/ `KB_RO_TOKEN`（只读）；`/kb/source`、`/kb/source/meta`（含 MFi）只认写令牌 |
| 10-11 | **目录云端下发**：`/kb/apk_catalog_spec`，客户端合并（同 id 云端覆盖内置） |
| 10-11 | 服务端规格实测：2 vCPU / 3.9GB / 41G 盘 → **云端编译不可行**（Gradle+Kotlin+Compose 峰值 3~5GB ≫ 2.7GB 可用），维持本地出包架构 |
| 10-10 | MCP 服务端上线（106.12.162.224:7799）；Skill 切线上；`locked-config.md` 锁定 MCP 地址 |
| 10-10 | P0 四项：数据模型合并、服务端 upsert 字段清洗 + 深 merge + 统计累加、SKILL.md description 与实际对齐、值级脱敏（键名+值双扫） |
| 10-10 | 第 0 步体检「规则云端下发 + 匿名画像回传」打通（`/kb/preflight_spec`、`/kb/preflight_report`） |
| 10-10 | 源码快照上云（含 MFi，4.9MB）+ `pull_source.py` 拉快照（sha256 校验，服务端不编译） |
| 10-09 | GitHub 同步机制：`sync_github.py`（自动脱敏 + 幂等）+ `api_push.py`（REST 兜底，本机 git 连不上 GitHub） |

---

## 发版操作

```bash
# 1. 改完代码后，发一版（patch / minor / major 三选一）
python scripts/release.py patch  "修 verify.py 误报"
python scripts/release.py minor  "新增 xxx 脚本"
python scripts/release.py major  "改数据结构"

# 2. 自验（含版本一致性检查）
python scripts/smoke_test.py

# 3. 同步 GitHub（提交信息自动带版本号）
set GH_TOKEN=xxx && python sync_github.py
```
