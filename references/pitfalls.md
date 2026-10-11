# 实战踩坑库（Estrella DiPlay 工程沉淀，禁止再踩）

> 本节固化 DiPlay 车机 APK 工程历代真实踩坑。每次进入「兼容性判断 / 修复方案设计」前，
> **先在本节按现象检索**，命中则直接复用正确做法，不要再从头排查。
> 这些坑多数与具体车型无关，属工程级通用教训；车型专属坑另存 `scripts/local_kb.py` 的 `pitfall` 表。

## 一、构建环境坑

### P1 · AGP 9 拒绝非 ASCII 项目路径
- **现象**：Gradle 报 `Your project path contains non-ASCII characters`，构建直接失败。
- **根因**：AGP 9.x 在 Windows 上禁止项目根路径含中文/非 ASCII 字符（如 `Desktop\DiPlay-全部版本\...`）。
- **正确做法**：构建前把工程 robocopy 到纯 ASCII 路径（如 `C:\Users\Administrator\DiPlayBuild\DiPlay-Universal`，
  排除 `build/` 与 `.gradle/`），在副本上跑 `gradlew`。**源码改动需与中文原工程双向同步**。

### P2 · aapt / apksigner 无法解析中文路径下的 APK
- **现象**：对桌面中文路径的 APK 跑 `aapt2`/`apksigner` 报错或乱码。
- **正确做法**：任何 APK 验证（versionCode、签名、对齐）前，先把 APK 复制到 ASCII 临时目录再验证；
  最终交付再拷回桌面。

### P3 · 删 build/.gradle 被沙箱拦截
- **现象**：直接删 `automotive/legacy/shared/mobile` 的 build 目录被环境沙箱拒绝。
- **正确做法**：需清理时加 `dangerouslyDisableSandbox`；删 `.gradle` 后重编会重下依赖（3~5 分钟），非必要不删。

### P4 · 构建 / 诊断日志含 GBK 乱码
- **现象**：gradle.properties、构建日志中文显示为 GBK 乱码。
- **正确做法**：历史遗留，不影响改动逻辑；读取时用支持 GBK 的方式，勿误判为代码问题。

## 二、安卓存储坑（最致命）

### P5 · 车机私有存储不落盘（长安逸动 plus 等 ROM）
- **现象**：`SharedPreferences.apply()` 写完进程重启即丢，表现为「设置存不住」「功能点了没反应」
  （依赖已保存设置的功能读不到参数→无反应）。`MFi` 凭证文件曾被写成 0 字节同源。
- **根因**：部分车机 ROM 私有目录写入不可靠。
- **正确做法**：新增 `ResilientPrefs`（透明包装标准 SP）：每次提交后异步把整份 SP 镜像写 JSON 到
  **可靠目录三选一探针**——`getExternalFilesDir(null)` → `filesDir` → `noBackupFilesDir`；
  进程重启后把兜底缺失 key 合并回原生 SP。Robolectric 下退化为原生 SP。已替换 170 处 `getSharedPreferences`。
  **若仍丢，说明兜底目录也无写权限，需换 mmkv / 裸文件策略**，不要再假设 SP 可用。

### P6 · File.renameTo 跨目录在车机常被拒
- **现象**：`renameTo` 搬文件失败，无异常但文件没动。
- **正确做法**：需要搬文件时直接写入目标目录 + 逐步校验字节，禁用「暂存目录 + renameTo」。

### P7 · AppStorage.privateBase() 装读必须同源
- **现象**：装到一个私有目录、读到另一个 → 报 `Offline MFi directory is missing or invalid`。
- **正确做法**：统一走 `AppStorage.privateBase()`（API21+→`noBackupFilesDir`，否则 `filesDir`），装读同源。

## 三、兼容性降级坑

### P8 · 降级只改「用哪个 API」，不改「选哪个对象」
- **现象**：为简化把「遍历 config 找含 USBMUX+NCM 的那个」改成「取接口最多的」→ 6.0 报
  `does not expose an NCM function`。
- **正确做法**：兼容处理只换底层 API 调用，绝不改判定/选择逻辑，否则是隐性回归。

### P9 · 4.x 档不能降 minSdk
- **现象**：试图把 minSdk 降到 19 会 manifest merger 直接报错。
- **根因三堵墙**：① 依赖地板 `androidx.appcompat`/`core`/`media3`/`car.app` 全系 ≥21；
  ② BLE 广播 API（BluetoothLeAdvertiser/AdvertiseData）API21 引入；③ Kotlin2.2/AGP9 字节码下限 >19。
- **正确做法**：走「去 androidx + 原生 View」路线（retro/legacy 现状），不要改数字。

### P10 · 不要按 USB 规范写死 subclass
- **现象**：iPhone NCM control 实测是 `02.02.1f`（厂商自定义）而非标准 `02.0d`，data `0a.00.21`(alt=2)。
- **正确做法**：消费方按「功能配对」识别，勿写死 subclass。

### P11 · SharedPreferences 在 API23 前不可用 getSystemService(CLASS)
- **现象**：API23 以下 `getSystemService(CLASS)` 取 SP 崩溃。
- **正确做法**：用 `getSystemService(String)` 字符串版。

## 四、无线 / 有线 CarPlay 坑

### P12 · 无线 CarPlay 角色方向不可反
- **现象**：车机主动拨 iPhone 连不上。
- **正确做法**：**车机 = RFCOMM Server，iPhone = Client**。iOS 不向第三方暴露 CarPlay SDP 服务，
  车机主动拨必然失败。配件 SDP UUID 结尾 `ff`（Server）/`cafe`（Client）——把 Client UUID 当 Server 注册 = 方向+UUID 双错。
  `bluetoothServerSocket` 必须在 close 时一并关闭；SDP 只能由 Controller 在连接时注册，Activity 里不要再注册。

### P13 · 无线 CarPlay 必须手机端发起
- **现象**：期望全自动连接失败。
- **正确做法**：车机端点完连接后，用户须去 iPhone「设置→通用→CarPlay」点一下 DiPlay/允许弹窗，不可能全自动。

### P14 · 有线排查顺序不可跳步
- **现象**：4.x 真机绝大多数卡在第 1 步 USB 授权（系统弹窗未绘制，logcat 无 Displayed 行，无 granted/denied）。
- **正确做法**：顺序 `USB 授权 → iAP2 → NCM/VPN → IP`。先看日志卡在哪个阶段再动手，改 IP 层（IPv4/IPv6）对卡在 USB 授权完全无效。

### P15 · 有线路径是架构级 IPv6-only
- **现象**：误以为是「只挑了一个地址」的 bug。
- **根因**：`CarPlayVpnService.attach()` 强制 linkLocal 为 IPv6 link-local，`Ipv6NcmBridge` 只搬 IPv6。
- **正确做法**：不要当 bug 修；真跑 IPv4 需重写 NCM 桥 + VPN 加 IPv4，对 4.x 风险高，须真机验证后再动。

## 五、无障碍 / UI 坑

### P16 · 无障碍自动点击必须「窗口级判定 + 按钮级点击」
- **现象**：要求同一节点既含关键词又是 Button → 条件永远不成立，点了白点。
- **正确做法**：整棵窗口树判关键词 → 再点正向按钮（确定/取消）。必须**排除自身包名**（自己 UI 也含 "USB" 字样会误点），并限定系统包。

### P17 · 用户开「无障碍」按钮会盖住 USB 弹窗
- **现象**：测授权时用户点 App 内「开启无障碍」打开 Settings 页，盖住 USB 弹窗导致测不出。
- **正确做法**：测 USB 授权流程时避开先开无障碍。

## 六、Kotlin / 编译坑

### P18 · Kotlin lambda 内智能转换失效
- **现象**：`chosen.id` where chosen nullable 报 Unresolved。
- **正确做法**：拆成局部变量再用，不要直接在 lambda 里链式可空访问。

### P19 · AtomicReference<*>.get()?.let{} 的 it 须显式类型
- **现象**：平台类型推断不出 `it` 类型 → Unresolved。
- **正确做法**：显式写 `?.let { socket: BluetoothSocket -> }`。

### P20 · AGP9 挂外部源码须同时挂 java + kotlin 目录
- **现象**：只挂其一，Kotlin 文件不参与编译。
- **正确做法**：`java.directories += path; kotlin.directories += path` 同时挂。

### P21 · ResilientPrefs 实现自身坑（已踩）
- **现象①**：`BACKUP_SUFFIX`/`io` 声明在 `object ResilientPrefs` 内，但 `PrefsBackup` 是独立类访问不到 → 编译失败 `Unresolved reference`。
- **正确做法①**：这类被独立类共用的常量/执行器必须放**文件级（top-level）private**，不要塞进 object。
- **现象②**：曾误加 `createDeviceProtectedStorageContext()`（API24+），common(23)/shared(21) minSdk<24 直调编译失败。
- **正确做法②**：候选目录只用 `getExternalFilesDir`/`filesDir`/`noBackupFilesDir`（API≥21 可用），不碰 deviceProtected。

## 七、测试 / 报告坑

### P22 · :shared:test 不是绿的，不能当验收闸门
- **现象**：存在大量既有失败（VideoInCarTest、RemoteMfiAuthenticationClientTest 等），整套件成败无参考意义。
- **正确做法**：验证改动只看**单个测试类**结果，不要以整套件成败判断。

### P23 · 诊断报告文案改了要同步测试
- **现象**：v1.5.5 汉化后 `ProcessExitDiagnosticsTest` 等 4 个测试红。
- **正确做法**：改文案后必须同步改对应测试字符串，并按新串全量 grep `**/src/test/`。

### P24 · 错误信息必须可自证
- **现象**：模糊报错无法定位。
- **正确做法**：把路径、字节数、接口明细打出来（v1.7 那轮靠真机接口明细一眼定位 NCM 误判，回报率极高）。

## 八、版本 / 交付坑

### P25 · 每版必升 versionCode + 改文件名
- **现象**：同包名同签名同版本号覆盖安装，用户分不清装没装新版。
- **正确做法**：每版升 `versionCode`，文件名带版本号（如 `..._v1.5.6_40MB.apk`）； automotive flavor 版本在
  `automotive/build.gradle.kts` 的对应 flavor 块里升。

### P26 · 判定老车机能否无线 CarPlay 的两分钟法
- **命令**：`adb shell getprop ro.build.version.sdk` / `pm list features | grep bluetooth_le` /
  `dumpsys wifi | grep -iE "5GHz|802.11a|11ac"`。4.2.2 普遍缺 BLE 发射+5GHz+稳定 VPN，硬件天花板改代码无用。

### P27 · 现实解优先级
- USB 有线 CarPlay 永远最稳；无线无解时走外接 CarPlay 盒子（车机只当显示器）。不要在无硬件能力的车机上死磕无线。
