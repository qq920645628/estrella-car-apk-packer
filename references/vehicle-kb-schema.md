# 车型知识库数据结构（本地与线上统一模型）

> 对应 `scripts/local_kb.py` 与线上 MCP 服务（`server.js`）。本地与线上采用**完全一致**的单文件结构，
> 确保本地缓存可直接导入 MCP、导出格式互通。**唯一索引**与**记录 Schema**两端相同。
> 主键锁定见 `locked-config.md`。

## 存储位置（本地回退）

```
数据根 = 环境变量 ESTRELLA_KB_DIR 或 <skill_dir>/data/
├── kb.json            # 统一存储：{ vehicles, conflicts, meta }
└── backups/           # 全量备份（local_kb.py backup）
```

`kb.json` 结构（与线上 MCP 完全相同）：
```json
{
  "vehicles": {
    "<vehicle_key>": { /* 车型记录，内嵌 pitfalls / solutions / variants（见下） */ }
  },
  "conflicts": [ /* 冲突复核队列 */ ],
  "meta": { "created_at": "..." }
}
```

## 唯一索引

```
vehicle_key = "<车型>|<年份>|<车机系统版本>|<安卓底层版本>"
例：长安|2021|逸动plus尊贵版1.4T|9.0
```

## 记录 Schema（单条车型记录，内嵌坑/方案/变体）

```json
{
  "vehicle_key": "长安|2021|逸动plus尊贵版1.4T|9.0",
  "make": "长安", "model": "逸动plus", "year": 2021,
  "factory_system_version": "逸动plus尊贵版1.4T", "android_base_version": "9.0",
  "arch": "arm64", "chip": "未知", "dpi": 0, "screen": "未知",
  "root_status": false,
  "known_bugs": ["私有存储不落盘"],
  "permission_limits": [], "storage_limits": ["apply()不落盘"],
  "bt_limits": [], "usb_limits": [], "other_limits": [],
  "result": "success",
  "source": "task-20261010-xxx",
  "access_count": 3,
  "created_at": "2026-10-10T19:00:00Z", "updated_at": "2026-10-10T19:00:00Z",
  "pitfalls": [
    { "symptom": "设置保存后重启即丢，热点SSID/密码存不住",
      "root_cause": "车机私有存储apply()不落盘", "severity": "HIGH",
      "fix_ref": "ResilientPrefs 兜底（见 pitfalls.md P5）",
      "tags": [], "created_at": "...", "source": "..." }
  ],
  "solutions": [
    { "problem": "设置不持久化",
      "steps": ["新增 ResilientPrefs 包装 SP", "三目录探针写 JSON 兜底", "替换 170 处 getSharedPreferences"],
      "stability_rating": 5, "risk_level": "LOW", "verified_count": 1,
      "tags": [], "created_at": "...", "source": "..." }
  ],
  "variants": [
    { "firmware_batch": "C211EV-2021Q2",
      "hardware_detail_delta": { "chip": "全志T7", "screen": "1920x720" },
      "source": "...", "created_at": "..." }
  ]
}
```

## 冲突复核队列（conflicts）

```json
[ { "id": "uuid", "key": "<vehicle_key>", "existing": {...}, "incoming": {...},
    "reason": "同问题不同修复步骤，待人工复核", "at": "...", "status": "pending" } ]
```

## 操作语义（对应 local_kb.py / MCP 子命令）

- `query --key K`：返回车型记录（内嵌 pitfalls/solutions/variants）；无则返回 `{found:false}`。
- `upsert --json F`：
  - vehicle_key 不存在 → 新增车型记录，access_count=1。
  - 存在 → **禁止覆盖**：仅追加缺失的坑/方案（按 symptom+root_cause / problem 去重）；
    同问题不同步骤 → 推入 conflicts，**不覆盖**原有记录；标量字段仅填空不覆盖。
- `export --format csv|json`：导出全部（与线上结构一致，可直接互导）。
- `stats`：车型总量、成功/失败/部分案例、累计访问、坑/方案数、冲突数。
- `resolve --conflict-id ID --action apply_incoming|keep_existing`：消费冲突队列。
- `backup`：全量备份至 `backups/时间戳/`。

## 去重与可信度

- 同 vehicle_key 多次一致提交 → access_count 升高，检索优先（可信度权重）。
- 同车型不同固件批次 → 独立 variant 分支（变体分支原则）。
- 每条记录带 `source` 溯源标记，便于脏数据排查。
