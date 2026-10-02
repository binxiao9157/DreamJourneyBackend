# DreamJourney B8-S01 本地修复报告

日期：2026-09-16\
状态：**LOCAL_VALIDATION_PASS / DEVICE_NOT_RUN**\
现场状态：**B8 现场仍为 FAIL；B8-S01 修复版真机验收为 NOT_RUN**

## 1. 范围与基线

本轮仅处理 admission 已提交后，本场结果的即时只读状态读取。没有修改 Live 采集、历史 UI 仲裁、候选内容、正式记忆或后端业务合同。

- iOS 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- iOS 分支：`feature/prd-stitch-ui-adaptation`
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 后端 HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`
- 后端工作树：本轮核对时 clean；本轮无后端修改
- 基线差异：`evidence/baseline/ios-working-tree-with-red-tests.diff`

工作区原有未提交修改全部保留；未执行 reset、clean、commit、push、部署、真机安装或生产数据操作。

## 2. 已确认根因

### 2.1 状态读取复用了旧 route decision

admission 已由 fresh authority 合法提交后，旧即时观察器仍可能通过已捕获的旧 route decision 读取状态。旧 decision 为 denied 或过期时，读取在创建 GET 前被拒绝，页面从已提交事实退化为 unavailable。

本轮只对 `.liveMemoryRecoveryStatus` 读取资源捕获当前合法 read decision。其他 OwnerTruth 资源和写请求的授权选择不变。

### 2.2 capture 完成后的即时观察没有复用有界恢复状态机

原 capture 路径使用独立即时 StatusUseCase，未复用 B6 已有的 trace、deadline、GET 预算、账号作用域、取消和迟到回调隔离。状态读取失败还可能过早清除 checkpoint/follow-up/outbox，丢失已提交 admission 的恢复坐标。

修复后，当前 capture coordinator 私有持有一个只绑定本场 productSession/batch 的 `EchoLiveMemoryRecoveryCoordinator`。它不扫描历史、不接全局 registry，也不生成或重发 ack/admit。

## 3. 修改清单

### 3.1 BackendClient 当前读取授权

文件：`DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`

- 函数：`continueBoundedOwnerTruthRead`
- 对 `.liveMemoryRecoveryStatus` 在初次读取前捕获 fresh current decision。
- 沿用原 ReadContext、attempt state、账号租约、期限和恢复预算。
- 当前策略明确 deny 时在网络前停止，GET 为 0。
- 新增安全诊断 `freshDecisionCaptured`，不记录正文、凭据或原始业务标识。

### 3.2 同场私有有界 reader

文件：`DreamJourney/Sources/Modules/Echo/EchoViewController.swift`

- 类型：`EchoLiveMemoryCaptureCoordinator`
  - admission 已验收后，从实际 admitted checkpoint/follow-up 构造精确同场 plan。
  - follow-up 写入失败时可回退到可信 admitted checkpoint；无法建立可信 plan 时保持 unknown。
  - child 状态只更新观察状态，不倒退 committed admission，不发送业务写。
  - 后台 suspend、前台显式开启一个新 round；旧订阅和回调被隔离。
  - 只有“服务端终态已核实且终态观察已成功持久化”后才清理恢复坐标。
- 类型：`EchoLiveMemoryRecoveryCoordinator`
  - 增加 `terminalObservationPersisted`，区分业务终态和本地持久化结果。
  - poll 只在既有 round 内继续，不能自行重建预算。
  - suspend 后迟到 poll 记录 `pollDiscarded`，不能开启新 GET 或释放新 round 所有权。
  - 每个 poll 绑定独立 `pollID`、roundID、trace 和 generation；跨轮次迟到 closure 校验失败后立即返回，不能清除新轮次的 poll 引用或消费其预算。

### 3.3 自动化

文件：`DreamJourneyTests/OwnerTruthContractsTests.swift`

新增/调整 B8-S01 定向场景：

- 旧 route denied、当前策略 allow，真实 BackendClient 发出状态 GET。
- admitted checkpoint 进入当前 capture coordinator 后立即使用同场 reader。
- 当前策略 deny 时 GET 为 0，committed 坐标保留。
- `readyForAdmission`/未核实结果不得倒退 phase 或重发 ack/admit。
- follow-up upsert 失败时从 admitted checkpoint 只读恢复。
- terminal observation 持久化失败时保留坐标。
- suspend 后旧 poll 不得新增读取。
- A 轮 pending 后保存 poll，suspend A 并显式开启 B；B pending 后执行 A 的迟到 poll，不得改变 B 的状态、引用或预算，B 自身 poll 仍能进入 attempt 2。
- pending 轮询共享一个 trace，attempt 单调递增并止于 6 次 GET。
- 超时后的迟到结果不再更新状态或继续轮询。

## 4. 修前红、修后绿

### 4.1 修前红

结果包：`evidence/red/B8S01RealAssemblyRed.xcresult`

- 2 tests，2 failures，5 个业务断言失败。
- 旧 route denied、当前策略 allow：状态 GET 为 0。
- admitted checkpoint 的即时观察：页面出现 queued 后转 unavailable，状态 GET 为 0，checkpoint 被删除。
- 结果包内容指纹：`e1db409d9d253378f608eba6c3d67aea28cc54595c437f810031c1ee43973c71`

### 4.2 修后定向绿测

结果包：`evidence/green/B8S01BoundaryGreen2.xcresult`

- 9 tests，0 failures。
- 真实 FeatureGateService route cache、真实 BackendClient/requestJSON、受控 URLProtocol、真实临时磁盘 store 和 capture/recovery coordinator 接线通过。
- 同一 pending intent 的 attempt 为 `[1,2,3,4,5,6]`，GET 预算耗尽后停止。
- 状态读取及恢复期间 ack/admit 新增写请求为 0。
- 结果包内容指纹：`b21067423ed65d385c4d096ab326951b337a67391d94e9c6ae81bc252002babd`

说明：即时状态组合测试从真实 admitted checkpoint 进入 capture coordinator，并走真实 BackendClient 状态读取；完整 end/ack/admit 只写一次及 queued 后不回退 saving，由同一完整回归中的 `testLiveCaptureDuplicateEndedContinuationDoesNotRegressOrReadmit` 覆盖。该分层证据不是生产端到端或真机证据。

### 4.3 完整回归与构建

- OwnerTruth 完整回归：`evidence/green/OwnerTruthFullRegression.xcresult`
  - 477 tests，0 failures。
  - 包含既有 B6/B8、unknown-write 只读恢复、UIKit 接线、typed 差异、精度、八种操作和关联组保护。
  - 内容指纹：`7735766c85678deecb32412724997a79957f74ed9d35628294d26e6c47f0db36`
- iOS Simulator 构建：`evidence/green/SimulatorBuild.xcresult`
  - status `succeeded`，0 errors，1 条第三方 Kingfisher Swift 6 warning。
  - 内容指纹：`20ce9aced9872ba087d153a1be156ae771c1f187ad485959a48787161511f667`
- Generic iOS Device arm64、禁用签名构建：`evidence/green/GenericIOSBuild.xcresult`
  - status `succeeded`，0 errors；警告为既有第三方弃用/Swift 6 兼容及工程已有警告。
  - 内容指纹：`43ce0533ed93efb0048bb83d8c3dae07b2f3ffc709812f2b6c70a238adc03cd9`
- `git diff --check`：PASS。

### 4.4 S01-08 跨轮次补充红绿

修前结果包：`evidence/red/B8S01CrossRoundPollRed.xcresult`

- 1 test，1 failed test，2 个业务断言失败。
- A 的迟到 poll 在 B pending 后错误新增一次 GET，使请求数从 2 变为 3，并把 B 从 `queued` 改成 `checking`。
- 内容指纹：`e0223824b33a6d12daf8c524dd94f25a9567d63a7e4f43b1fbb7d1809957db5a`

修后结果包：`evidence/green/B8S01CrossRoundPollGreen.xcresult`

- 1 test，0 failures。
- A 的迟到 poll 仅记录关联 A trace 的 `pollDiscarded`；GET 数、B 状态和 B 预算均保持不变。
- B 自身合法 poll 随后正常发出，沿用 B trace，attempt 从 1 增至 2，resourceGETCount 从 0 增至 1。
- 内容指纹：`715b671e7ab6f0620d85ecb63b9559f935ccd0f6daa472426c7d96f630420aa0`

补充后 OwnerTruth 完整回归：`evidence/green/OwnerTruthFullRegressionCrossRoundPoll.xcresult`

- 478 tests，0 failures，包含全部 B8-S01 和受影响 B6 恢复场景。
- 内容指纹：`bcf75b835ede092534b00071b5cbab8200f326b07039f9f660b30f43bed09c86`

补充后 Generic iOS Device arm64 无签名构建：`evidence/green/GenericIOSBuildCrossRoundPoll.xcresult`

- status `succeeded`，0 errors。
- 内容指纹：`da735a0fd5f1eacc4e7d7528d4b9a6b6db04927738b9f8b87cf67f24b2d67896`

一次按 Xcode 27 名称选择模拟器失败，原因是已安装的 iPhone 17 Pro runtime 为 iOS 26.5。该结果保存为 `evidence/infra/B8S01BoundaryDestinationFailure.xcresult`，随后使用设备 UUID 重跑通过；它是工具链失败，不是业务红测。

## 5. S01-01 至 S01-10 映射

| 编号 | 代码/测试证据 | 状态 |
|---|---|---|
| S01-01 | capture coordinator + admitted checkpoint + real BackendClient GET；完整写链单写保护由完整回归覆盖 | PASS（本地分层组合） |
| S01-02 | `testB8S01CurrentPolicyAllowsStatusReadWhenCapturedRouteWasDenied` | PASS |
| S01-03 | `testB8S01FreshPolicyDenyKeepsCommittedCoordinatesAndSendsNoRequest` | PASS |
| S01-04 | `testB8S01PendingPollingSharesOneTraceAndStopsAtSixGETBudget` 及既有 hard-deadline 回归 | PASS |
| S01-05 | 完整回归中的 policy/auth/account/cancel/late fencing 场景 | PASS（受控环境） |
| S01-06 | `testB8S01ReadyForAdmissionNeverRegressesCommittedPhaseOrResendsWrites` | PASS |
| S01-07 | follow-up upsert、terminal recorder 失败两项定向测试 | PASS |
| S01-08 | 单轮 suspend 测试及 `testB8S01StalePollFromRoundACannotConsumeRoundBOrClearItsPoll` 跨轮次红绿 | PASS |
| S01-09 | child 状态映射定向断言及既有 capture 状态映射回归 | PASS |
| S01-10 | B6 same-resource、401、unknown-write、预算和 late fencing 回归 | PASS |

## 6. 实际执行命令形态

本轮使用 `DreamJourney.xcworkspace` 和 scheme `DreamJourney`：

```bash
xcodebuild test \
  -workspace DreamJourney.xcworkspace \
  -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/... \
  -resultBundlePath <B8S01-result-path>

xcodebuild test \
  -workspace DreamJourney.xcworkspace \
  -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests \
  -resultBundlePath <OwnerTruth-full-result-path>

xcodebuild build \
  -workspace DreamJourney.xcworkspace \
  -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' \
  -resultBundlePath <simulator-build-result-path>

xcodebuild build \
  -workspace DreamJourney.xcworkspace \
  -scheme DreamJourney \
  -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO \
  -resultBundlePath <generic-ios-build-result-path>
```

命令中的实际结果路径就是本报告引用的 `.xcresult` 绝对路径。测试没有使用生产账号、生产 URL 或生产业务数据。

## 7. 源码与证据指纹

详见 `evidence/source-and-result-fingerprints.txt`。关键源码 SHA-256：

- `DreamJourneyBackendClient.swift`：`c5f7e1a60b6f33ac3cbefe28b2c6cd8d907dcf1b873de145bb1387bb2e581874`
- `EchoViewController.swift`：`65da0316a84a6314c07f0b2d78791c84c36c96ac20449ec6255c2f26f1a68647`
- `OwnerTruthContractsTests.swift`：`18bd3f8024c92ec9a87f1a503c2a98b4dd8ad59f48bf3df80f2862426a287687`

## 8. 未运行项与残余风险

- 修复版 iPhone 真机：NOT_RUN。
- 生产 FeatureGate、生产网络及真实 admission 后即时状态：NOT_RUN。
- 历史会话补发、清理或迁移：NOT_RUN，且本轮禁止执行。
- 后端 PostgreSQL：NOT_RUN；本轮没有修改后端合同、事务或数据结构，无需用数据库测试替代 iOS 读取接缝验证。
- 独立 XCUITest scheme：工程当前没有本轮可直接运行的独立 scheme；真实 UIKit/协调器接线由 `DreamJourneyTests` 中的 UIKit/装配测试覆盖，不能替代真机验收。
- 现场旧问题可能还包含本轮范围外原因；本地反例只证明并修复了已确认源码接缝，不能宣称已经定位历史现场的唯一根因。

## 9. 部署判断与回退

### 部署判断

- 本轮仅有 iOS 变更。
- 后端无需修改或重新部署。
- 本轮未部署任何环境，也未安装 iPhone。

### 局部回退

按代码块回退，不回退其他未提交成果：

1. 移除 BackendClient 对 `.liveMemoryRecoveryStatus` 的 fresh current decision 分支。
2. 移除 capture coordinator 的私有 same-session reader、状态映射和生命周期绑定。
3. 移除 recovery coordinator 的既有 round poll guard 与 `terminalObservationPersisted` 清理门禁。
4. 移除本轮 B8-S01 测试。

该回退会恢复“旧 route 拦截即时 GET”和“读取/持久化失败可能丢失恢复坐标”的已知缺陷，因此只应在发布阻断时作为整体 iOS 版本回退，不应清理磁盘记录或重发业务写。

## 10. 后续最小真机验收（未执行）

后续得到单独授权后，使用新合成文字场：

1. 在同一页面完成一次文字会话结束。
2. 记录原 session/batch 的 admission 已提交证据。
3. 不切页、不前后台切换，观察本场即时只读状态到 `reviewReady`、`noCandidates` 或明确下游失败。
4. 核对仅有一个 admission POST；取消/重入后只能出现状态 GET，不能再次 ack/admit。
5. 若读取失败，确认 UI 不倒退已提交事实、磁盘恢复坐标仍在。

停止条件：出现第二次 ack/admit、batch 错配、已提交状态倒退、恢复坐标丢失或旧回调覆盖新 round 时立即停止并保存脱敏 trace/attempt/阶段证据。

本轮结论：**本地验证通过，真机待验收。**
