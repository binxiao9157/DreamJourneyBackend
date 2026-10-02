# DreamJourney B6 五项遗漏补充修复本地交付报告

日期：2026-09-14\
交付状态：`A_LOCAL_PASS / READY_FOR_B6_DEVICE_RETEST`\
现场状态：B6 `FAIL`；修复版 iPhone 复测 `NOT_RUN`

## 1. 基线与边界

- iOS：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- 分支：`feature/prd-stitch-ui-adaptation`
- 基线 HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 后端 HEAD：`a25b993922fc90dde1e689d19e51becb68fccdbf`
- Xcode：26.6（17F113）；macOS：26.6.2（25G83）
- 模拟器：iPhone 17 Pro，iOS 26.5（23F77）
- 工作区开始和结束均存在前序未提交修改。本轮未重置、未整文件覆盖、未提交、未推送。
- 本轮生产代码仅修改 `EchoViewController.swift` 中 B6 存储/恢复/模拟器诊断适配代码块；测试修改位于 `OwnerTruthContractsTests.swift`，UIQA 脚本为新增未跟踪文件。
- 未修改 FM-POLICY-01、候选审核、更正预览、正式记忆内容、整理规则、向量模型和 Live 模型配置。

## 2. 修复前反例

权威红测结果包：`evidence/pre-fix/B6-five-gap-red-v2.xcresult`。

同一当前实现基线上执行 4 个业务反例，结果 `0 PASS / 4 FAIL`：

1. 损坏 V2 且无合法 V1 时，更新操作未抛错并可能覆盖原文件。
2. `endPrepared` 有可靠 thread/session 坐标时没有发起只读批次发现。
3. 已取得 `actionRequired` 后，round 仍显示 checking，旧期限可继续改写状态。
4. Controller 重新进入只复用旧完成态，GET 数保持 1，未重新核实。

早期草稿 `B6-five-gap-red.xcresult` 含测试装配噪声，仅保留历史，不作为业务红测结论。

## 3. 五项修复映射

### 3.1 Follow-up 读取失败不再等同无任务：PASS

问题：旧实现将 V2 读取/解码失败与“文件不存在”合并为空数组，后续 upsert 可能覆盖不可读记录。

修改：

- `EchoLiveMemoryFollowUpStore.scan/readV2Outcome/readLegacyOutcome` 返回 typed 扫描状态。
- 明确区分 `absent`、`unreadable`、`decodeInvalid`、`schemaUnsupported`、`scopeMismatch` 和合法记录。
- 所有 mutation 先经过 `recordsForMutation`；非合法状态 fail closed，保留原文件。
- 仅在 V1 合法且作用域匹配时回退；V2 修复写仍沿用原子替换。
- 恢复服务把异常转换为明确 blocked reason，不伪造空任务。

代码：[EchoViewController.swift](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:340)、[测试](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:12)。

证据：`B6-five-gap-green-v2.xcresult`，覆盖 V2 损坏、V1 损坏、文件保护式不可读、未来 schema、错 scope、原字节/目录保留及恢复服务阻断。

### 3.2 end 已接受但回执未落盘：PASS

问题：本地 `endPrepared` 被直接解释成“需要继续 end”，没有利用现有只读 pending-batch 合同识别服务器已接受窗口。

修改：

- `verifyStatus/discoverPendingBatch` 在存在原 thread/session 坐标时先执行只读发现。
- 唯一匹配才绑定原 batch 并继续状态核实；零匹配为 `notObserved`；多匹配或绑定冲突为明确阻断。
- 找到批次不等于整理完成，仍由原 batch status 合同决定阶段。
- 恢复路径不调用 end/ack/admit，也不创建 session/batch/source/candidate。

代码：[EchoViewController.swift](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2564)、[测试](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:564)。

证据：唯一匹配、未观察到、多匹配、错绑定和恢复业务写计数为 0 均进入定向绿测及完整回归。

### 3.3 确定阻断不会被旧超时覆盖：PASS

问题：action/access/contract 等确定状态没有一致结束 round，旧 deadline/poll 仍可能把状态改成 deadline exceeded。

修改：

- 每轮生成 `activeRoundID`，deadline、读取回调和结束路径均校验 request/generation/round。
- `finishRound` 统一撤销 request、poll 和 deadline，并释放本轮执行权。
- `actionRequired`、`accessBlocked`、`contractBlocked` 结束当前轮但保留任务坐标。
- `finishTerminalRound` 先尝试记录完成观察，再结束本轮；记录失败不会删除原坐标或伪称持久化成功。

代码：[EchoViewController.swift](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2478)、[测试](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:1178)。

证据：旧定时器触发后 action/access/contract 状态不变；新 round 不受旧回调影响。

### 3.4 页面重新进入重新核实：PASS

问题：Registry 返回缓存 coordinator 后，新页面会立即看到历史完成态，却没有当前只读核实。

修改：

- coordinator 用 `subscriptionEpoch` 区分历史观察与新页面订阅。
- 新合法订阅可先看到缓存状态，随后触发一次有界只读 reentry round。
- 同一订阅重复 start、前后台通知和并发进入仍受单飞/预算约束。
- 完成观察写入 follow-up；写入失败保留旧坐标，新 Registry/Controller 仍可重新发现并只读 GET。

代码：[EchoViewController.swift](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2546)、[测试](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:1724)。

证据：Controller 重建 GET 从 1 增至 2；持久化成功后仍重新核实；持久化失败后新 Registry 仅凭保留坐标再次读取，写命令为 0。

### 3.5 真实组合链路：PASS（本地模拟器）

问题：run-01 的跨进程 UIQA 使用专用模拟读取客户端，只证明磁盘发现和页面展示。

修改：

- UIQA recovery client 改为封装真实 `DreamJourneyBackendClient`。
- 走真实 `FeatureGateEvaluator`、隔离 policy snapshot、Alamofire session、受控 URLProtocol、typed status decode 和 EchoViewController 页面入口。
- 输出 trace、最终 attempt、真实 GET 计数和 adapter 类型；日志只保留白名单诊断。

代码：[EchoViewController.swift](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:13953)、[UIQA 脚本](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/Scripts/QA/prd-stitch-ui/run-b6-cold-start-read-recovery-smoke.sh:1)。

证据：`evidence/post-fix/b6-cold-start-uiqa-real/`。第一进程 PID 54220 创建磁盘坐标；第二进程 PID 54242 只凭磁盘和新合法账号恢复，`recoveryAdapter=DreamJourneyBackendClient`、`statusGETCount=1`、trace 存在、无麦克风、无新 capture、恢复进程业务写标记为 0，页面显示“上次对话已进入待确认记忆”。

## 4. 测试与构建

- 定向绿测：`B6-five-gap-green-v2.xcresult`，14/14 PASS。
- 完成观察落盘失败：`B6-completion-observation-persistence-failure-green.xcresult`，1/1 PASS。
- OwnerTruth 最终回归：`OwnerTruthContracts-regression-final.xcresult`，440/440 PASS。
- 完整 DreamJourneyTests：`DreamJourneyTests-full-regression-final.xcresult`，593/593 PASS。
- 跨进程实际 UIQA：PASS，截图见 `b6-cold-start-uiqa-real/02-recovered-pending-review.png`。
- 模拟器构建：PASS，0 error，51 warning；结果包 `build/simulator-build.xcresult`。
- 通用 iOS 设备目标构建：PASS，0 error，70 warning；结果包 `build/generic-ios-device-build.xcresult`。
- `git diff --check`：PASS。
- 运行日志与 JSON 脱敏扫描：PASS，未发现 Authorization/Bearer/凭据/密钥模式。

构建警告以三方库和既有 deprecated API 为主；本轮相关文件存在 1 条既有未使用 self 警告，不影响构建，未做范围外清理。

主要执行命令（均在 iOS 工程目录执行）：

```bash
xcodebuild test -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro,OS=26.5' \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests \
  -resultBundlePath '<run>/evidence/post-fix/OwnerTruthContracts-regression-final.xcresult'

xcodebuild test -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro,OS=26.5' \
  -only-testing:DreamJourneyTests \
  -resultBundlePath '<run>/evidence/post-fix/DreamJourneyTests-full-regression-final.xcresult'

OUTPUT_ROOT='<run>/evidence/post-fix' RUN_ID='b6-cold-start-uiqa-real' \
  bash Scripts/QA/prd-stitch-ui/run-b6-cold-start-read-recovery-smoke.sh

xcodebuild build -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'generic/platform=iOS Simulator' \
  -resultBundlePath '<run>/build/simulator-build.xcresult'

xcodebuild build -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO \
  -resultBundlePath '<run>/build/generic-ios-device-build.xcresult'
```

定向红/绿测试使用同一组 `-only-testing` 业务断言，仅结果包路径分别指向 `evidence/pre-fix/B6-five-gap-red-v2.xcresult` 与 `evidence/post-fix/B6-five-gap-green-v2.xcresult`。
跨进程 UIQA 本次首次执行时使用了脚本默认输出目录，验收后将该次原始结果和截图完整复制到本 run；上面的 `OUTPUT_ROOT/RUN_ID` 是在本 run 内复现同一验证的推荐命令，不把复制动作描述成重新执行。

## 5. PostgreSQL 与后端判断

本轮未修改后端合同或代码，因此没有重复运行后端部署、迁移或生产验证。

T28 沿用 run-01 的隔离 PostgreSQL 16 合成证据：2 次恢复状态读取均 `changedTableCount=0`；独立同文 Live 计数为 session=2、batch=2、source=2。证据位于 `run-2026-09-14-01/evidence/post-fix/`。这只是隔离数据库合同证据，不替代修复版 iPhone 现场验收。

部署判断：无需后端部署；后端生产状态未访问、未改变。

## 6. 指纹

- `EchoViewController.swift`：`332d24793fef03d4876da9f60c51653b0b83a2247b5f313f312b4c93869322ef`
- `OwnerTruthContractsTests.swift`：`8175673e305b773fa79a08f7cba54468bd118ffc16472d5a147059cde66449e6`
- `run-b6-cold-start-read-recovery-smoke.sh`：`3ab704d2af62eca11383a0168c1b0196a0f9925beaacf4449a9b1635eadeacc6`
- 模拟器可执行文件：`1f698aece6fc28f95926a53d61d008c9591538f709d63b083cf91f19fd2754d8`
- 通用设备可执行文件：`1d95938fa73e867cd94d0f575c26d7bd00100592b6763f4c1be48eca89055792`

## 7. 残余风险与未执行项

- 修复版 iPhone 的真实进程终止/重启、真实策略/认证波动和真实服务端完成时序：`NOT_RUN`。
- B6 现场状态保持 `FAIL`，不能由本地模拟器证据关闭。
- 真机上文件保护导致的暂不可读需要现场观察；本地已验证 fail-closed 和恢复后可重新发现。
- 生产后端、正式记忆、候选、历史和 Dead Letter：未访问、未操作。

## 8. 局部回退

仅回退本轮在 `EchoViewController.swift` 中的 follow-up typed scan、read-only pending-batch discovery、round fencing/reentry observation、UIQA 实际适配器代码块，以及对应测试/脚本。不得整文件回退，以免破坏前序 B4、FM-POLICY 和已有 Live 修复；不得恢复冷启动 end/ack/admit 写重放，也不得删除恢复记录。

## 9. 结论

五项本地门禁均已闭环，可标记：`A_LOCAL_PASS / READY_FOR_B6_DEVICE_RETEST`。

停止在等待用户另行授权真机复测的位置。本报告不把 B6 现场缺陷改为 PASS。
