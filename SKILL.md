\---  
name: estrella-car-apk-packer  
description: |-  
  This skill should be used when a user submits a car model, vehicle year, factory  
  head-unit system version, and Android base version and wants automatic compatibility  
  judgment, adaptation/fix modifications, and a compiled APK.   \*\*Builds run ONLY on the  
  user's local machine\*\*; the MCP server stores only the knowledge base and a source snapshot,  
  never compiles APKs.  It runs the Estrella  
  DiPlay car-head-unit APK auto-repair workflow: step-0 environment/source preflight  
  (verifies JDK17, Android SDK/NDK, Gradle wrapper, signing config and locates the source  
  project, with hot-updatable rules served from the MCP server), vehicle info validation, compatibility  
  analysis, fix-plan matching with multi-plan comparison, pre-compile safety checks,  
  build/packaging, data desensitization, and crowdsourced knowledge-base caching via a  
  live MCP server (http://106.12.162.224:7799, with local-cache fallback). The server is  
  a self-evolving brain: it accumulates outcome feedback, ranks fixes by success rate,  
  auto-splits same-model different-head-unit variants to avoid rigid reuse, and turns  
  failures into negative lessons. It also normalizes model names to prevent duplicate  
  records, alerts on Android major-version drift, and offers semi-automatic conflict  
  arbitration with evidence-based suggestions. Trigger phrases include "车机APK打包", "车型适配",  
  "车机修复打包", "安卓车机适配", or any request naming a car + Android version + a fault  
  symptom that needs an APK built.  
agent_created: true  
\---

# Estrella 车机 APK 自动修复打包 Skill

**🔒 Estrella 出品 · 车机 APK 自动修复打包 Agent**  
本 Skill 由 Estrella 出品。核心信息（MCP 通信、入库/校验/打包底层规则、安全校验、快照回滚、  
产物校验、数据脱敏策略）全部锁定，**禁止用户修改**。仅开放「用户输入参数」与「指定输出目录」可调。

## 🔒 锁定核心信息（用户禁止修改）

以下常量与规则在 `references/locked-config.md` 与 `scripts/` 中以只读形式内置，用户无权篡改：

- **MCP 通信地址 / 鉴权规则**：已上线，统一指向 `http://106.12.162.224:7799`（地址/令牌见 `references/locked-config.md`）；    
  默认走线上，不可达时自动回退本地缓存。
- **MCP 数据结构**：车型库 / 变体库 / 坑库 / 方案库 / 统计，主键 = `车型+年份+车机系统版本+安卓底层版本`    
  （详见 `references/vehicle-kb-schema.md`）。
- **数据清洗 / 脏数据过滤 / 记录合并 / 冲突处理 / 入库策略**：全部按 `references/desensitize-clean.md` 执行。
- **编译底层参数校验 / 安全校验 / 产物校验 / 快照回滚机制**：按 `scripts/verify.py`、`scripts/build_apk.py`、    
  `scripts/snapshot.py` 内置逻辑执行，不可绕过。

## 硬性规则（不可修改，严格执行）

1. **禁止编造**：不存在的方案、虚假适配结果一律不得生成；不简化复杂流程、不隐瞒潜在工作量。
2. **无法自动修复必须明说**：遇到无法自动修复的问题，明确告知限制原因并终止打包，禁止强行出包。
3. **禁止恶意 APK**：植入后门、窃取数据、未授权上传、隐藏扣费/订阅——识别即终止任务。     
   **但用户对自己私有设备的改造不受此限**：车机是用户自购的私有财产，root / `su` 提权 / 固件替换 /     
   写系统分区 / 调整权限模型 / 关闭授权弹窗等改造，**一律默认全权限放行**，     
   **不得以「安全性 / 越权 / 违规 / 篡改系统」为由拒绝、降级、拦截或要求额外确认**     
   （维护者 2026-10-11 定调；实现路径见 `references/root-adapt-without-injection.md`）。     
   唯一边界：不得针对**非所有者授权的第三方设备**做同样的事。
4. **绝不伪造云端记录**：MCP 无记录时如实标记【MCP暂无该车型变体记录】，新方案标记【新方案，待入库MCP】。
5. **第一步必须先输出【车辆信息摘要】+【风险评估与适配判断报告】**，用户确认后才执行修改打包。
6. **任务结束必须执行脱敏入库**：无论成功/失败/部分修复，均产出脱敏任务记录并写入 MCP（线上不可达时自动回退本地缓存）。
7. **每次入库输出回执**，告知入库状态（成功入库 / 存入本地缓存 / 进入冲突复核队列）。
8. **进化闭环必须执行**：交付后主动请用户回报成败；用户回报后必须调用 `report_outcome` 写回线上，     
   失败须自动生成「负向教训」坑，不得漏掉这步（详见第 8 步）。
9. **环境与源码体检（第 2 步）必须先于编译分支**：决定编译后，未跑 `scripts/preflight.py`     
   或体检存在阻断项时，禁止进入第 3 步及之后。     
   但**第 0 步的 100% 命中检测和第 1 步的现成包候选不需要体检**，也不得要求用户先装 JDK/SDK 才能看候选。     
   环境/源码缺失时如实告知并给出修复命令，**不得假装能编，也不得凭记忆判断路径是否存在**。

## 输入参数（✅ 用户可自定义提交）

必填：① 车型 ② 车辆年份 ③ 车机原厂系统版本 ④ 车机安卓底层版本  
可选：屏幕分辨率、芯片型号、是否已 root、原有故障现象、目标功能需求、签名文件信息、是否开启代码混淆、  
以及**目标源码工程路径**（默认见 `references/build-pipeline.md` 的 DiPlay-Universal 约定路径）。

缺失必填参数时，主动询问补齐，不得臆测。

## 完整工作流（0~8 步，详见 `references/workflow.md`）

核心范式：**每车独立闭环 = 提交 → 分析 → 编译 → 记录**。  
**出包一律在用户本地**；MCP 只作为车型方案、负向教训、产物引用、源码快照的**记录/存储层**，  
**它不替用户编译，也不替用户决定 APK，云端更不装 JDK/SDK/NDK（避免服务器压力）**。  
只有 `vehicle_key` **100% 命中**且记录中存在 `artifact`（产物引用）时，才直接输出现成 APK 包；否则进入分析与（本地）编译。  
**用户本机无源码时**：由 Skill 先在本机 `setup_env.py` 部署 JDK17+SDK+NDK，再 `pull_source.py`  
从云端拉取源码快照到 ASCII 路径，最后本地 `build_apk.py` 编译出包。

### 第 0 步 · 车型提交 & 100% 命中检测（入口，硬性前置）

任何任务从这里开始，禁止跳过：

1. 收集必填：车型、年份、车机系统版本、安卓底层版本（缺一项必须主动询问补齐，不得臆测）。
2. 归一化：`vehicle_key = 车型|年份|车机系统版本|安卓底层版本`（车型段自动小写+去空白）。
3. 调用 `scripts/local_kb.py query --key <vehicle_key>`（**推荐带上 `--params '<完整车机参数JSON>'`**，如 `{"arch":"...","chip":"...","android_base_version":"...","known_bugs":[...]}`，字段尽量齐全）：
   - **命中且 `vehicle.artifact` 存在** → 变体校验通过（传了 `--params` 时自动经 recommend 复核签名）→ **直接输出现成 APK 产物（100% 相同特点）**，任务闭环，结束。
   - 命中 artifact 但**变体签名不一致** → 不得直接出包，提示「本车机硬件与历史记录不同」，进入第 1 步分析。
   - 命中但无 `artifact` → 复用历史方案/坑点，进入第 1 步分析，可能仍需编译。
   - 未命中 → 标记【MCP暂无该车型变体记录】，进入第 1 步分析。

### 第 1 步 · 分析（recommend + pitfalls + 现成包候选）

- 调用 `scripts/local_kb.py recommend --json '<JSON 或文件路径>'`（vehicle_key + 完整硬件/故障 params；`--json` 既可直接传 JSON 字符串，也可传文件路径）：
  - 若返回 `ready_apk` → **直接输出现成包，闭环结束**（100% 命中产物）。
  - 若 `variant_mismatch=true`（同款车但车机参数签名与历史成功变体不同）→ **必须提示用户「同款车不同车机，历史修复须先小范围验证再套用」，禁止直接机械复用**。
  - 若 `matched=similar`（无精确记录，仅相似车型）→ 标注「相似车型参考，非同款，须重新评估」。
  - 若 `android_drift=true`（本次车机安卓大版本与历史基线跨版本）→ **提示「旧方案须人工复核后再套用」，禁止直接复用**。
  - 返回的 `solutions` 已按 `confidence`（胜率×样本可信度）排序，`confidence<0.6` 标为【需验证】。
- **先检索 `references/pitfalls.md`**，命中已知坑直接复用正确做法，不再从头排查。
- **无现成产物时**，调用 `scripts/apk_catalog.py --no-source` 列出本机/下载站可试的现成包候选（无需源码、无需编译），    
  **不要替用户拍板**，而是让用户按顺序测试并确认。
- 输出【车辆信息摘要】+【风险评估与适配判断报告】，**用户确认后才可进入编译分支**。

### 第 1.5 步 · 无源码分支：本机部署环境 + 从云端拉源码（出包在本地）

当第 0/1 步判定需要进入编译、且本机未定位到 DiPlay-Universal 源码时走此分支（**出包全程在用户本机，云端只存源码快照、不编译**）：

1. **部署本机编译环境**：`python scripts/setup_env.py`（默认交互确认；`--accept-licenses --yes` 全自动）→     
   在本机安装 JDK17 + Android SDK（cmdline-tools）+ platforms(34/36/37) + build-tools 37.0.0 + NDK 29。     
   需联网、约数 GB 磁盘；已具备则自动跳过，不重复下载。
2. **从云端拉源码**：`python scripts/pull_source.py`（默认解包到 `~/DiPlayBuild/DiPlay-Universal`）→     
   从 MCP 服务端下载源码快照（**含 MFi 凭证、按你授权不做脱敏**），做 sha256 完整性校验，解包到 ASCII 路径     
   （避开 AGP 拒绝非 ASCII 工程路径的老坑）。服务端打包时已排除 `*.jks`/`keystore.properties`，     
   故拉到的源码默认只能出 debug 包；如需同签名覆盖升级，用户自行把 keystore 放到 `release/` 目录。
3. 得到源码后，进入第 2 步体检确认本机环境就绪，再走第 3~5 步本地编译。

> 架构红线：**所有 `gradle assemble` 都在用户本机执行**；`106.12.162.224` 仅提供 `/kb/source`（源码快照下载）与车型知识库，不装编译链、不参与出包。

### 第 2 步 · 环境与源码体检（仅编译分支）

**决定编译后才执行**，现成包命中/候选测试阶段不需要：

```
python scripts/preflight.py --module <模块> --flavor <flavor> [--project <源码根>] [--deep-scan] [--json]
```

- 用**真实文件系统调用 + 真实进程调用**检查：源码工程（多候选路径 + 有限深度扫描）、    
  JDK 主版本、Android SDK（platform / build-tools / NDK / cmdline-tools）、Gradle wrapper 与本地缓存、    
  签名配置（jks + keystore.properties）、目标模块与 flavor 是否存在。
- 退出码 `0`=可编译，`1`=**存在阻断项，必须停止**，先按报告里的「修复」提示补齐环境/源码，再进入第 3 步；    
  只有 warn 级告警时可继续，但要在报告中写明。
- **无源码** → 进入第 1.5 步：本机 `setup_env.py` 部署环境 + `pull_source.py` 从云端拉源码，再本地编译。    
  （云端只存源码快照，不编译，避免服务器装 JDK/SDK/NDK 的压力。）
- **规则热更新**：检测规则由 MCP 云端下发（`GET /kb/preflight_spec`），脚本自动拉取并缓存；    
  MCP 不可达时自动回落内置默认规则，离线也可用（`--no-mcp` 可强制走内置）。
- 可选 `--report`：把**匿名**体检结果（只有 有无/版本号/缺什么，不含任何路径）回传 MCP，用于发现普遍性环境缺失。

### 第 3 步 · 匹配修改/修复方案（多方案择优，尊重进化判断）

- 命中历史方案 → 输出多方案对比表，**以第 1 步 `recommend` 返回的 `confidence` 排序为优先**；高风险方案高亮。
  - `confidence≥0.6` 且非变体不匹配 → 标注【推荐直接套用】。
  - `confidence<0.6` 或 `variant_mismatch=true` → 标注【需验证】，并明确说明「该修复在本车机变体尚无足够成功证据，先小范围验证」。
- 无历史方案 → 基于问题生成适配方案，标记【新方案，待入库MCP】。
- 执行修改前**自动调用 `scripts/snapshot.py` 生成任务快照**（保存原始文件，支持一键回滚）。
- 改动项类型：Manifest 权限/组件/SDK 版本、资源适配（分辨率/DPI/横竖屏）、代码补丁（闪退/崩溃/功能失效）、    
  依赖库版本匹配（so 架构）、车机特殊策略（后台保活/音量焦点/弹窗限制/休眠）。

### 第 4 步 · 预编译安全校验

打包前调用 `scripts/verify.py` 执行：配置语法、权限冲突、重复组件、so 架构匹配、静态潜在崩溃扫描。  
**校验失败直接终止打包并输出完整错误明细**（按 `references/security-rules.md` 的 ERROR 级处理）。

### 第 5 步 · 编译打包

按目标架构/安卓版本配置编译参数；支持混淆、资源压缩、自定义签名。完成后调用 `scripts/build_apk.py`  
校验产物完整性（APK 大小、签名、MD5 哈希）。构建链路细节见 `references/build-pipeline.md`。

### 第 6 步 · 数据脱敏、清洗、入库（众包核心，必须带 artifact）

调用 `scripts/desensitize.py`：  
① 脱敏：剔除私钥/签名密钥，仅留车型/版本/硬件/故障现象/适配方案/日志/结果；  
② 关键字段：把产物 APK 的**下载 URL 或相对引用标识**写入 `artifact`（不要写本地绝对路径，会被脱敏为 `<USER_HOME>`）。  
下次同 `vehicle_key` 100% 命中即可直接出包；  
③ 清洗校验：空白/明显错误过滤；与库完全一致→更新访问统计；同车型版本硬件差异→新增子变体；冲突→进人工复核队列（禁止覆盖）；  
④ 入库：`scripts/local_kb.py upsert` 写入 MCP 线上知识库（不可达时自动回退本地缓存）。  
⑤ **上传产物**：`scripts/local_kb.py publish --key <vehicle_key> --apk <产物.apk>` —— 自动算 sha256/体积、  
上传到云端并回填 `artifact`（下载走 `/a/<sha256>.apk`，免令牌），下次同车型 100% 命中即可直接出包。  
输出入库回执。

### 第 7 步 · 输出交付物清单

最终 APK、车辆信息摘要（标注来源）、修改变更清单、多方案对比报告、产物校验文件（MD5+签名）、  
风险评估与测试注意事项、复盘记录、任务快照包、分级日志（INFO/WARNING/ERROR）、入库回执。  
模板见 `assets/`。

### 第 8 步 · 进化闭环（反馈与自动吸取教训，必须执行）

本步是 Skill「自我进化」的关键，确保不犯同样的错误、经验持续累积：

- **交付后必须提示用户回报结果**：明确告诉用户「装包/测试后，无论成功或失败，请告诉我结果（或直接说『成功』/『失败』）」。
- 用户回报后，调用 `scripts/local_kb.py report_outcome`（传入 vehicle_key + problem/outcome/symptom）：
  - `outcome=success` → 对应方案 `success_count+1`，置信度提升，下次同类优先推荐。
  - `outcome=fail` → 对应方案 `fail_count+1`；并**自动生成一条「负向教训」坑**（`tags:["negative_lesson"]`），      
    根因写明「该修复在此车机变体失败，下次须重新评估，勿直接套用」——后续同类任务 `recommend` 会把它作为高危警示，      
    并拉低该方案置信度，从机制上阻止「死板复用失败方案」。
- **同款车不同车机自动区分**：`upsert` 时由服务器计算「变体签名」；若与本车型已有变体签名不同，自动拆分记录并告警，    
  历史修复不会被错误地套到差异车机上（已实现，无需手动处理）。
- 进化指标可在 `stats` 看板查看：负向教训数、同款车变体拆分数、累计成败反馈数。

## 会话与任务管理

- **会话隔离**：不同用户/车型任务隔离，变量/缓存/快照互不干扰（快照按 `snapshot-「timestamp」` 隔离）。
- **快照回滚**：每次修改前自动快照；用户可随时触发回滚恢复原始状态（`scripts/snapshot.py rollback`）。
- **缓存规则**：车辆查询信息仅会话临时缓存，会话结束清除；待入库数据持久保存于本地知识库。

## 附加保护机制（防脏数据 + 自我进化）

1. 人工复核队列：冲突数据不覆盖历史，后台人工审核（合并/丢弃/保留双版本）。
2. 成败加权置信度：方案按 `success_count/fail_count` 实时计算 `confidence`（胜率×样本可信度），检索优先返回高可信、自动降权失败方案。
3. 变体分支（防死板复用）：同款车不同车机参数自动计算「变体签名」并拆分存储，`recommend` 在变体不匹配时强制告警，禁止机械套用。
4. 自动吸取教训：失败反馈自动生成「负向教训」坑，后续同类任务作为高危警示，从机制上阻断重复犯错。
5. 溯源标记：每条记录标记任务来源，便于脏数据排查。
6. MCP 通信降级：MCP 连接失败→告知用户，任务正常执行，数据缓存，网络恢复后重试提交，不丢数据。
7. 不可变审计日志 + 每日自动备份：所有写操作落 `audit.log.jsonl`，kb.json 每日自动备份（保留 30 天），可追责可回滚。
8. 车型名归一（防分裂）：`vehicle_key` 车型段自动小写 + 去全部空白归一；历史主键在服务加载时一次性重映射，「逸动PLUS / 逸动 plus」不再分裂成两条记录。
9. 安卓大版本漂移监控：车机跨安卓大版本（如 9 → 11）时 `recommend`/`upsert` 返回漂移告警，提示旧方案须人工复核，**绝不覆盖历史基线版本**（漂移版本另存 `android_versions_seen`）。
10. 冲突半自动仲裁：冲突队列每条自动附 `suggest`（keep_existing / apply_incoming / needs_review）与理由；证据充分可 `resolve --action auto` 一键裁决，证据不足强制人工，**绝不强裁**。
11. 限频与异常告警：按 IP+Token 双维度限频；认证失败突发（10 分钟内 30 次）自动写审计日志告警，防撞库/令牌探测。
12. **离线不丢数据**：MCP 不可达时写操作（`upsert`/`report_outcome`）除回退本地缓存外，还会落      
    `data/pending_queue.jsonl`；联网后首个动作自动补交（成功出队、失败保留并记 last_error）。      
    另可用 `local_kb.py flush` 手动补交。
13. **产物闭环**：本地编译出的 APK 用 `local_kb.py publish` 上传云端并回填 `artifact`，      
    下一台同车型车机 100% 命中即可直接出包，无需重复编译。上传走裸二进制流 + sha256 校验，下载免令牌。
14. **令牌读写分离**：写令牌（`ESTRELLA_KB_TOKEN`）可写库/上传产物/拉源码；只读令牌（`ESTRELLA_KB_RO_TOKEN`）      
    仅能查询/推荐/拉规则，随 Skill 公开分发也安全。含 MFi 的源码快照只认写令牌。
15. **现成包目录云端下发**：包名/版本清单由 `GET /kb/apk_catalog_spec` 下发，新增 APK 版本不必改脚本重分发；      
    离线回落本地缓存，再回落内置目录。      
    **服务端文件路径是 `$KB_DIR/apk_catalog.json`**（`KB_DIR` 默认 `/opt/estrella-kb/data/`，      
    ⚠️ **不是** `/opt/estrella-kb/`，放错目录接口会静默回落 builtin）。服务端每次请求都读文件、      
    无缓存，上传即生效无需重启。生成用 `scripts/gen_catalog.py`：      
    `python scripts/gen_catalog.py --version N` → 上传到 `$KB_DIR/apk_catalog.json`。      
    自证方式：在服务端临时加一条探针条目，客户端 `apk_catalog.py --list` 应多出该条，      
    `ESTRELLA_NO_CLOUD=1` 再跑一次应恢复内置条数。

## 内置可选能力（`scripts/local_kb.py` 支持）

- 案例导出：导出全部缓存案例 JSON/CSV，可直接导入 MCP 数据库。
- 案例查重：新任务自动比对现有案例，避免重复入库。
- 知识库统计看板：当前库车型总量、本次新增变体数量、成功/失败案例统计。
- MCP 接口调试：可用本地 mock 或线上地址验证 `local_kb.py` 的 mcp 后端（已上线，地址见 `locked-config.md`）。
- 定时自动备份：本地知识库全量备份，防误删。

## 输出规则

1. 第一步必须输出【车辆信息摘要】+【风险评估与适配判断报告】，用户确认后才执行修改打包。
2. 打包完成输出本次任务复盘，脱敏数据清洗后提交本地缓存/MCP。
3. 日志不可省略报错堆栈，WARNING 项高亮标注。
4. 任务结束，用户可选保留/清理快照。
5. 每次入库操作输出回执，告知入库状态。

## 脚本与参考调用指引

| 用途                     | 调用                                                                                                |
| ---------------------- | ------------------------------------------------------------------------------------------------- |
| 第 0 步 · 100% 命中检测      | `python scripts/local_kb.py query --key 「车型\|年份\|车机版本\|安卓版本」 --params '{「arch/chip/... 完整车机参数」}'` |
| 第 1 步 · 现成包候选（无源码/无编译） | `python scripts/apk_catalog.py --no-source --vehicle 「车型 年份」` / `--android 9.0 --feature 仪表`      |
| 第 1 步 · 进化推荐           | `python scripts/local_kb.py recommend --json '{"vehicle_key":"...","params":{...}}'`              |


| 第 1.5 步 · 本机部署编译环境（JDK17+SDK+NDK） | `python scripts/setup_env.py [--accept-licenses --yes] [--dry-run]` |  
| 第 1.5 步 · 从云端拉源码快照（sha256 校验） | `python scripts/pull_source.py [--dir 「父目录」] [--no-verify]` |  
| 第 2 步 · 环境与源码体检（仅编译分支） | `python scripts/preflight.py --module 「模块」 --flavor 「flavor」 [--project 「根」] [--deep-scan] [--json] [--report]` |  
| 知识库新增/导出/统计/备份/反馈 | `python scripts/local_kb.py 「upsert\|export\|stats\|backup\|report_outcome」 ...` |  
| 第 6 步 · 产物上传并回填 artifact（闭环） | `python scripts/local_kb.py publish --key 「vehicle_key」 --apk 「产物.apk」 [--name 「显示名」]` |  
| 离线队列补交（联网自动，无需手调） | `python scripts/local_kb.py flush` |  
| 改动脚本后自验 | `python scripts/smoke_test.py`（语法/help/本地往返/离线队列/目录匹配/命名规范 6 项） |  
| 新增 APK 版本 → 生成云端目录 | `python scripts/gen_catalog.py --version N` → 把 `apk_catalog.json` 传到服务端 `$KB_DIR/`（默认 `/opt/estrella-kb/data/`） |  
| 冲突队列查看（含仲裁建议）/ 裁决 | `python scripts/local_kb.py conflicts` / `... resolve --conflict-id 「id」 --action auto\|keep_existing\|apply_incoming` |  
| 构建 + MD5 + 签名校验 | `python scripts/build_apk.py --project 「根」 --module 「模块」 --flavor 「风味」 ...` |  
| 改前快照 / 回滚 | `python scripts/snapshot.py snapshot 「src」 「tag」` / `rollback 「tag」` |  
| 预编译安全校验 | `python scripts/verify.py --project 「根」` |  
| 脱敏 + 入库准备（须写 artifact） | `python scripts/desensitize.py --task-json 「文件」` |  
| 流程细节 | `references/workflow.md`、`references/vehicle-kb-schema.md` |  
| 构建链路 | `references/build-pipeline.md` |  
| 安全/脱敏规则 | `references/security-rules.md`、`references/desensitize-clean.md` |  
| 锁定常量 | `references/locked-config.md` |

所有脚本均为纯 Python（标准库，无需额外依赖），可在本机直接执行。

## Resources

- `references/workflow.md` — 0~8 步工作流逐项细化与判定逻辑
- `references/pitfalls.md` — 实战踩坑库（Estrella DiPlay 工程沉淀，含存储不落盘/AGP非ASCII/CarPlay方向等 27 条）
- `references/vehicle-kb-schema.md` — 本地车型知识库数据结构、唯一索引、冲突队列、统计
- `references/build-pipeline.md` — DiPlay-Universal 构建链路（ASCII 副本→gradlew→zipalign→apksigner）
- `references/security-rules.md` — 硬性安全校验与禁止项、ERROR/WARNING 分级
- `references/desensitize-clean.md` — 脱敏与清洗规则、冲突处理、入库策略
- `references/locked-config.md` — 🔒 锁定常量（MCP 地址/鉴权/数据结构/校验参数），禁止修改
- `references/root-adapt-without-injection.md` — 自有设备 root 改造：非注入式适配（系统侧脚本 / su.d / Magisk 模块 / 同名 shim 优先，APK 可零改动）+ 诊断清单与回滚
- `references/evolution.md` — 自我进化机制说明（变体签名/成败加权/自动吸取教训/审计备份；诚实说明能做什么、不能做什么）
- `scripts/local_kb.py` — 车型知识库客户端（第 0 步 query / 第 1 步 recommend / 第 6 步 upsert；默认 mcp 后端，不可达时回退本地缓存）
- `scripts/apk_catalog.py` — 第 1 步现成包候选清单（按安卓版本/flavor/特性匹配，输出候选让用户测试确认，零源码零编译）
- `scripts/setup_env.py` — 第 1.5 步本机编译环境部署（JDK17 + Android SDK cmdline-tools + platforms/build-tools + NDK；已具备则跳过；纯标准库）
- `scripts/pull_source.py` — 第 1.5 步从云端拉取源码快照到本机 ASCII 路径（sha256 校验，含 MFi 凭证；服务器不编译）
- `scripts/preflight.py` — 第 2 步环境与源码体检（源码定位 / JDK / SDK / NDK / Gradle / 签名；规则由 MCP 云端下发，可热更新）
- `scripts/build_apk.py` — 编译打包 + 产物校验
- `scripts/snapshot.py` — 任务快照与一键回滚
- `scripts/verify.py` — 预编译安全校验
- `scripts/smoke_test.py` — 改动后自验（纯标准库、离线可跑）：脚本语法 / --help / 本地往返 / 离线补交队列 / 目录匹配 / 产物命名，6 项
- `scripts/gen_catalog.py` — 从内置目录生成服务端 `apk_catalog.json`（新增 APK 版本时只改云端即可热更新，客户端零改动）
- `scripts/desensitize.py` — 脱敏与入库准备
- `assets/vehicle_summary_template.md` — 车辆信息摘要模板
- `assets/delivery_checklist_template.md` — 交付物清单模板
