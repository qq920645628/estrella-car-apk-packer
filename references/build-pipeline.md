# DiPlay-Universal 构建链路（参考实现）

> 本文件是 `scripts/build_apk.py` 的底层依据，也是当前唯一的真实编译实现。
> 工程为 Estrella DiPlay 车机接收端（CarPlay）。参数化：工程根、模块、flavor、签名均可由用户输入覆盖，
> 但**默认约定**如下（与 `locked-config.md` 一致的禁止改项不在此列）。

## 工程与模块约定

- 源码工程根（用户输入，默认约定）：
  `C:\Users\Administrator\Desktop\123\DiPlay-Universal`（中文路径，**仅用于源码编辑**）。
- ASCII 构建副本（构建必须）：`C:\Users\Administrator\DiPlayBuild\DiPlay-Universal`
  （从中文根 robocopy 排除 `build/`、`.gradle/`，源码改动双向同步）。
- 模块 → 适用车机：
  - `retro` minSdk17 → 4.2.2~4.4
  - `legacy` minSdk23 → 6.0~8.0
  - `mid81` minSdk23 → 8.1
  - `automotive` minSdk28 → 9.0+，flavors：universal / lite / cluster / hd / ble
- 签名：`release/diplay-release.jks`（alias `diplay`，密码取自 `release/keystore.properties`）。
  同包名同签名可覆盖升级；4.x/6.0 档须纯 v1 签名，9.0+ 档用 v2（AGP 默认）。

## 第 0 步：环境与源码体检（必须先做）

```
python scripts/preflight.py --module <retro|legacy|mid81|automotive> --flavor <flavor>
```

检查项：源码工程定位（多候选 + 有限深度扫描）、JDK 17+、SDK platform/build-tools/NDK、
Gradle wrapper 与本地缓存、签名配置、目标模块与 flavor。
**退出码 1 = 有阻断项，禁止继续**，按报告「修复」提示补齐后再走下面步骤。

检测规则由 MCP 云端下发（`GET /kb/preflight_spec`，spec_version 递增即热更新），
改规则不用重新分发 Skill；MCP 不可达时回落内置默认。

## 环境

```
JAVA_HOME = C:\Users\Administrator\.workbuddy\binaries\jdk\jdk-17.0.20.1+1
ANDROID_HOME = C:\Users\Administrator\AppData\Local\Android\Sdk
build-tools = %ANDROID_HOME%\build-tools\37.0.0   # zipalign / apksigner
```

## 构建步骤（脚本自动执行）

1. **准备 ASCII 副本**（若根为中文）：
   ```
   robocopy "<中文根>" "C:\Users\Administrator\DiPlayBuild\DiPlay-Universal" /MIR /XD build .gradle
   ```
2. **升版本号**（每版必做，见 pitfalls P25）：在 `automotive/build.gradle.kts` 对应 flavor 块
   升 `versionCode` 与 `versionName`；文件名带版本号。
3. **编译**：
   ```
   gradlew.bat :<module>:assemble<Flavor>Release
   ```
   产物：`<module>/build/outputs/apk/<flavor>/release/*.apk`。
4. **校验产物**（APK 须先拷到 ASCII 临时目录，见 pitfalls P2）：
   - `aapt2 dump badging <apk>` → 核对 `versionCode` / `versionName` / `package`。
   - `apksigner.bat verify --verbose <apk>` → 确认 v1/v2 至少其一 `true`，签名数=1。
   - `zipalign -c -v 4 <apk>` → `Verification successful`。
5. **对齐 + 签名**（若 AGP 未自动签名或需重签）：
   ```
   zipalign -f -p 4 <in.apk> <aligned.apk>
   apksigner.bat sign --ks release/diplay-release.jks --ks-key-alias diplay \
     --ks-pass pass:<pw> --key-pass pass:<pw> --v1-signing-enabled true --v2-signing-enabled true <aligned.apk>
   ```
6. **计算 MD5**：`certutil -hashfile <apk> MD5`（Windows）/ `md5sum <apk>`（Linux）。
7. **交付**：拷贝最终 APK 到用户指定目录（中文路径 OK，仅验证用 ASCII 临时目录）。

## 失败快速定位（对应 pitfalls）

- `project path contains non-ASCII characters` → P1，确认在 ASCII 副本构建。
- `Unresolved reference 'BACKUP_SUFFIX'/'io'` → P21，常量须文件级。
- `createDeviceProtectedStorageContext` 编译失败 → P21，移出候选目录。
- 签名相关：`apksigner.bat` 在 Git Bash 下需经 PowerShell/cmd 调用（路径转义），或走 `java -jar apksigner.jar`。
- 日志 GBK 乱码 → P4，不影响改动。

## 备注

- 删 `build/`、`.gradle/` 被沙箱拦（P3），非必要不清理。
- `:shared:test` 非绿不可当闸门（P22）。
- 长安逸动 plus 设置不落盘 → 用 `ResilientPrefs` 兜底（P5），不要假设 SP 可用。
