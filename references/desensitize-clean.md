# 数据脱敏、清洗与入库策略（众包核心）

> 对应 `SKILL.md` 第 6 步、`scripts/desensitize.py` 与 `scripts/local_kb.py upsert`。
> 无论任务成功/失败/部分修复，均须执行，绝不遗漏。

## 一、脱敏（剔除敏感信息）

**必须剔除（永不入库 / 永不出现在交付物）：**
- 签名密钥库：`*.jks`、`*.keystore`、`release/keystore.properties` 内容（键名命中即剔除）。
- 私钥：`identity.pk8`、`certificate.p7b` 等 MFi/证书私钥材料（仅可记录「已内置凭证」事实，不可含字节/密码）。
- 签名配置：alias、密码、签名算法参数。
- **值级扫描（不仅按键名）**：所有字符串值中出现明文凭据（`password=xxx`、`storePassword=DiPlay2026`、`token`、`secret`、`api_key`、`private_key` 等 key=value）一律红框为 `<REDACTED>`；密钥文件名（`.jks/.keystore/.pk8/.p7b`）红框为 `<REDACTED_FILE>`；用户绝对路径（含 `Users/`、`home/`、`root/`）红框为 `<USER_HOME>`。
- 白名单之外的未知字段一律丢弃，杜绝脏字段灌库。

**允许保留：**
- 车型、年份、车机系统版本、安卓底层版本、硬件参数（arch/chip/dpi/screen/root）。
- 故障现象、根因、适配方案步骤、稳定性/风险评级。
- 构建/运行日志（已剔除路径用户名与密钥行）、任务结果（成功/失败/部分）。

> 脱敏在**两端强制执行**：本地 `desensitize.py` 与线上 `server.js` 各实现一份等价逻辑，
> 入库前必过脱敏，确保即使调用方漏脱敏也不会有凭据/路径落库。

## 二、清洗校验

1. **空白过滤**：必填字段（vehicle_key 四要素）缺失 → 标记 `invalid`，不入库，提示补全。
2. **明显错误过滤**：安卓版本与 API 等级矛盾、arch 非法值 → 标记 `suspect`，进人工复核，不自动入库。
3. **与库完全一致** → 仅 `access_count +1`，更新 `updated_at`，**不重复新增**。
4. **同车型版本、硬件细节存在差异** → 新增为**子变体（variant）**记录，不覆盖原车型。
5. **数据冲突**（如同一 vehicle_key 下方案根因相矛盾）→ 推入 `conflict_queue`，**禁止直接覆盖原有记录**。

## 三、入库策略

- **线上模式**：`scripts/local_kb.py upsert` 写入 MCP（不可达时回退本地 `<skill_dir>/data/`），输出回执：
  `成功入库` / `存入本地缓存（新增变体，MCP不可达回退）` / `进入冲突复核队列`。
- **正式上线阶段**：切换 `local_kb.py` 后端为 `mcp`，优先读取 MCP；新数据校验通过后入库，
  冲突进入人工复核队列。
- **入库回执必输出**，告知状态与记录 id。

## 四、溯源与可信度

- 每条记录带 `source`（任务 id），便于脏数据排查。
- 多次一致提交 → `access_count` 升高 → 检索优先（可信度权重）。
- 变体分支：同车型不同固件批次独立存储。

## 五、备份与导出

- `scripts/local_kb.py backup`：全量备份至 `backups/时间戳/`，防误删。
- `scripts/local_kb.py export --format csv|json`：导出全部缓存，可直接导入 MCP 数据库（上线用）。

## 六、MCP 通信降级

- MCP 连接失败 → 明确告知用户，任务正常执行，数据写入本地缓存；
  网络恢复/MCP 部署后重试提交，不丢失数据。
