# 完整工作流细化（0~8 步）

> 与 `SKILL.md` 第 3 节对应。本文件为流程细节，规则优先级以 `SKILL.md`「硬性规则」与 `security-rules.md` 为准。
>
> 核心范式：**每车独立闭环 = 提交 → 分析 → 编译 → 记录**。**出包一律在用户本地**；MCP 只作为车型方案、负向教训、产物引用、源码快照的**记录/存储层**，它**不替用户编译、不替用户决定 APK，云端也不装 JDK/SDK/NDK（避免服务器压力）**。用户无源码时由 Skill 在本机 `setup_env` 部署环境 + `pull_source` 从云端拉源码，再本地编译。

## 第 0 步 · 车型提交 & 100% 命中检测（入口）

**输入**：用户提交的车型/年份/车机系统版本/安卓底层版本（必填）+ 可选参数。
**动作**：
1. 校验完整性：缺任一必填项 → 主动询问补齐，**不得臆测**。
2. 计算 `vehicle_key = 车型|年份|车机系统版本|安卓底层版本`，车型段自动归一（小写+去空白）。
3. 调用 `python scripts/local_kb.py query --key "<vehicle_key>"`：
   - **命中且 `vehicle.artifact` 存在** → 直接输出 `ready_apk`，任务结束（100% 相同特点）。
   - 命中但无 artifact → 复用历史方案/坑点，进入第 1 步分析。
   - 未命中 → 标记【MCP暂无该车型变体记录】，进入第 1 步分析。
4. MCP 不可达时：跳过远端，按未命中处理，进入第 1 步。

**产出**：命中状态、车辆信息摘要（如命中）。

**终止条件**：100% 命中 artifact 时直接出包，不经过 2~5 步。

## 第 1 步 · 分析（recommend + pitfalls + 现成包候选）

**动作**：
1. 调用 `python scripts/local_kb.py recommend --json <file|json>`：
   - 返回 `ready_apk` → 直接输出，闭环结束。
   - `variant_mismatch=true` → 提示用户先验证再套用历史方案。
   - `matched=similar` → 仅相似车型参考，非同款，须重新评估。
   - `android_drift=true` → 提示跨大版本须复核。
2. 检索 `references/pitfalls.md` 已知坑，命中直接复用正确做法。
3. **无现成产物时**，调用 `python scripts/apk_catalog.py --no-source --vehicle "..."` 列出候选：
   - 输出候选清单（本机路径或下载站链接）。
   - **明确标注"请先测试，不要直接拍板"**。
   - 用户测试反馈后，再决定用哪个 / 是否进入编译分支。
4. 输出【车辆信息摘要】+【风险评估与适配判断报告】。

**终止/分支**：
- 用户确认前 → 不进入编译分支。
- 用户无源码且不想编译 → 停留在现成包候选测试阶段。

**产出**：分析报告、候选清单（如有）。

## 第 1.5 步 · 无源码分支：本机部署环境 + 从云端拉源码（出包在本地）

**触发条件**：第 1 步分析后决定需要编译，但本机未定位到 DiPlay-Universal 源码。

**动作**：
1. **部署本机编译环境**：`python scripts/setup_env.py`（默认交互；`--accept-licenses --yes` 全自动）→
   在本机安装 JDK17 + Android SDK（cmdline-tools）+ platforms(34/36/37) + build-tools 37.0.0 + NDK 29。
   需联网、约数 GB 磁盘；已具备则自动跳过。
2. **从云端拉源码**：`python scripts/pull_source.py`（默认解包到 `~/DiPlayBuild/DiPlay-Universal`）→
   从 MCP 服务端下载源码快照（含 MFi 凭证、按授权不做脱敏），做 sha256 完整性校验，解包到 ASCII 路径
   （避开 AGP 拒绝非 ASCII 工程路径的老坑）。服务端打包时已排除 `*.jks`/`keystore.properties`。
3. 得到源码后，进入第 2 步体检确认本机环境就绪，再走第 3~5 步本地编译。

**架构红线**：所有 `gradle assemble` 都在用户本机执行；`106.12.162.224` 仅提供 `/kb/source`（源码快照下载）与车型知识库，不装编译链、不参与出包。

**产出**：本机源码工程路径、环境就绪状态。

## 第 2 步 · 环境与源码体检（仅编译分支）

**触发条件**：第 1 步分析后决定需要编译（用户确认、且现成包/产物均不满足）。

**动作**：
1. 调用 `python scripts/preflight.py --module <m> --flavor <f> [--project <源码根>] [--json]`。
2. 退出码 `1`（阻断）→ **停止**，按报告补齐环境/源码。
3. 退出码 `0` 但有 warn → 可继续，报告中写明。
4. 无源码 → 走第 1.5 步（本机 `setup_env` 部署环境 + `pull_source` 从云端拉源码）后本地编译；云端只存源码快照、不编译。
5. 规则热更新：检测规则由 MCP 云端下发，不可达时自动回落内置默认规则。

**产出**：体检报告、是否可编译。

## 第 3 步 · 匹配修改/修复方案（多方案择优）

1. 若命中历史方案（`solution` 表）：列出全部，输出**对比表**（稳定性评分 / 风险等级 / 适用场景），
   默认推荐 `confidence` 最高且风险最低者；高风险方案**高亮**并提示需用户确认。
2. 若无历史方案：基于第 1 步分析生成适配方案，标记【新方案，待入库MCP】。
3. **修改前必做**：`python scripts/snapshot.py snapshot <工程根> <tag>`（tag 含 vehicle_key + 时间戳）。
4. 执行改动（按方案 steps）：
   - AndroidManifest：权限、组件、SDK 版本。
   - 资源：分辨率/DPI/横竖屏。
   - 代码补丁：闪退/崩溃/功能失效（如存储不落盘→`ResilientPrefs` 兜底）。
   - 依赖：版本匹配、so 架构。
   - 车机策略：后台保活、音量焦点、弹窗限制、休眠。

**产出**：修改前快照 tag + 变更清单（草稿）。

## 第 4 步 · 预编译安全校验

调用 `python scripts/verify.py --project <工程根>`：
- 配置语法（gradle / manifest 可解析）
- 权限冲突（同权限不同 protectionLevel）
- 重复组件（同 activity/service 全限定名）
- so 架构匹配（libs 下 ABI 与目标一致）
- 静态潜在崩溃扫描（空调用、明显 NPE 模式、资源引用缺失）

**硬规则**：任一 ERROR 级 → **终止打包**，输出完整错误明细（含堆栈/行号），不得强行出包。

## 第 5 步 · 编译打包

按目标 module/flavor 调用 `python scripts/build_apk.py`（内部走 `build-pipeline.md` 链路）。
完成后校验：APK 大小、签名（`apksigner verify`）、MD5（`certutil`/`md5sum`）。
**产物校验失败 → 终止并报告**。

## 第 6 步 · 脱敏入库（必须带 artifact）

1. `python scripts/desensitize.py --task-json <任务记录>`：剔除私钥/签名密钥，仅留脱敏字段。
2. **关键字段**：把产物 APK 的**下载 URL 或相对引用标识**写入 `artifact`（不要写本地绝对路径，会被脱敏为 `<USER_HOME>`）。
   下次同 `vehicle_key` 100% 命中即可直接出包。
3. 清洗校验（见 `desensitize-clean.md`）：空白/错误过滤；与库完全一致→更新访问统计；硬件差异→新增子变体；冲突→进复核队列（**禁止覆盖**）。
4. `python scripts/local_kb.py upsert --json <脱敏记录>`：写入本地待入库缓存。
5. 输出**入库回执**（成功入库 / 存入本地缓存（MCP不可达回退） / 进入冲突复核队列）。

## 第 7 步 · 输出交付物清单

按 `assets/delivery_checklist_template.md` 逐项生成：
最终 APK、车辆信息摘要、修改变更清单、多方案对比报告、产物校验（MD5+签名）、
风险评估与测试注意事项、复盘记录（坑点/逻辑缺陷/新增待入库）、任务快照包、分级日志、入库回执。

## 第 8 步 · 进化闭环

- 交付后明确提示用户回报结果。
- 调用 `python scripts/local_kb.py report_outcome`（vehicle_key + problem/outcome/symptom）：
  - `outcome=success` → 对应方案 `success_count+1`，置信度提升，下次同类优先推荐。
  - `outcome=fail` → 对应方案 `fail_count+1`；并自动生成 `tags:["negative_lesson"]` 坑，阻止下次死板复用。
- 同款车不同车机参数自动计算「变体签名」并拆分记录，避免历史修复被错误套用。

## 边界与终止条件

- 100% 命中 artifact 时直接出包，不经过 2~5 步。
- 无法自动修复（缺硬件/缺源码支持/越安全红线）→ 明确告知限制，**终止打包**（硬性规则 2）。
- 用户未确认第 1 步报告 → 不进入第 3 步及之后。
- 用户无源码且拒绝部署环境/拉源码 → 终止在现成包候选阶段。
- 任意校验/产物 ERROR 级 → 终止并报告（硬性规则 3 + 安全规则）。
