# 交付物清单（Estrella 车机 APK 打包 · 任务复盘）

## 1. 最终 APK
- 文件：{{APK_PATH}}
- 包名：{{PACKAGE}}
- versionCode：{{VERSION_CODE}}  versionName：{{VERSION_NAME}}
- 体积：{{SIZE_MB}} MB
- 签名：{{SIGN_SCHEMES}}  MD5：{{MD5}}

## 2. 车辆信息摘要
- 来源：{{SOURCE_TAG}}
- 唯一索引：{{VEHICLE_KEY}}

## 3. 修改变更清单
| 文件 | 改动说明 |
|---|---|
| {{FILE}} | {{CHANGE}} |

## 4. 多方案对比报告
| 方案 | 稳定性 | 风险 | 适用场景 | 结论 |
|---|---|---|---|---|
| {{PLAN}} | {{STABILITY}} | {{RISK}} | {{SCENARIO}} | {{VERDICT}} |

## 5. 产物校验
- 签名有效：{{SIGN_OK}}
- zipalign：{{ALIGN_OK}}
- MD5 记录：{{MD5}}

## 6. 风险评估与测试注意事项
{{RISK_AND_TEST_NOTES}}

## 7. 复盘记录（坑点 / 逻辑缺陷 / 新增待入库）
- 本次踩坑：{{PITFALLS_HIT}}
- 新增待入库记录：{{NEW_KB_RECORDS}}

## 8. 任务快照包
- 快照 tag：{{SNAPSHOT_TAG}}（回滚：`python scripts/snapshot.py rollback {{SNAPSHOT_TAG}}`）

## 9. 分级日志（摘要）
- ERROR：{{ERROR_LOG}}
- WARNING：{{WARNING_LOG}}
- INFO：{{INFO_LOG}}

## 10. 入库回执
- 状态：{{KB_RECEIPT}}（成功入库 / 存入本地缓存（MCP不可达回退） / 进入冲突复核队列）
