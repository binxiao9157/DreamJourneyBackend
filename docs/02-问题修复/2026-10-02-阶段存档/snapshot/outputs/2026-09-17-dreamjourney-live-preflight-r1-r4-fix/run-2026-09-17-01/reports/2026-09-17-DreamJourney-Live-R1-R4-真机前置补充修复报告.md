# DreamJourney Live R1-R4 真机前置补充修复报告

日期：2026-09-17\
范围：第二份 Live 指导的 R1-R4 本地补充修复\
状态：`A_LOCAL_PASS / READY_FOR_LIVE_DEVICE_RETEST`

> 该状态仅表示本轮要求的本地确定性门禁已通过。真实 iPhone、真实 Provider/SDK 事件顺序、物理 20 分钟运行和修复版现场闭环均为 `NOT_RUN`，不能由本报告替代。

## 1. 基线与边界

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`。
- 后端 HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`，工作树干净，本轮无后端改动。
- iOS 工作树开工时已有 B4/B6/B7/B8/Live 等前序未提交成果；本轮没有 reset、clean、整文件覆盖、commit 或 push。
- 未安装或启动 iPhone，未部署，未访问生产数据或处理历史任务，未开展第三项历史 UI 仲裁。
- 开工先将上一 run 的状态校正为 `A_LOCAL_INCOMPLETE`，并将证据不足的 LC-08、TTL-L04 降为 `PARTIAL`；旧结果包保持原样。

## 2. R1：ack 上层旧授权

### 根因

ack 已要求 fresh request authority，但 `beginRequestOrFail` 和 `canCommit` 仍会先读取 `releasePolicyAvailable` 的旧 route decision。旧 route 过期或旧值为拒绝时，会在合法的新鲜策略判定前提前终止。

### 修改

- `OwnerTruthContracts.swift`
  - `OwnerTruthInterviewReviewBatchAcknowledgementUseCase.beginRequestOrFail`
  - `canCommit`
  - `withFreshRequestAuthority`
- 对 `requiresFreshRequestAuthority` 的本场 ack 路径，将旧 route Bool 的判断延后给本次 fresh authority；账号租约、operation generation、当前真实 deny 及回执绑定保持严格校验。
- 过期 decision 的恢复原因由实际 `expiresAt` 推导为 `expiredPolicyCache`，避免策略过期但 reason 字符串仍为旧 allow 原因时无法进入有界恢复。
- 没有将 gate 固定为 true，也没有改变非 fresh 路径的旧 route 语义。

### 红绿证据

- 红：`evidence/red/R1-R3-pre-fix.xcresult`
  - `testB8FreshAcknowledgementUsesCurrentProductionDecisionWhenCapturedRouteExpired` 修前失败。
- 绿：`evidence/green/R1-R3-final-green.xcresult`
  - 使用真实 `FeatureGateService` evaluator/cache 与真实 BackendClient 受控网络；旧 route 过期、当前 policy allow 时只产生唯一 ack POST。
- 当前真实 deny：`testManagerEchoRealGateBackendExplicitDenyKeepsDiskTurnWithoutMessagePost`，最终证据 `evidence/green/R4-explicit-deny-final.xcresult`，消息 POST 为 0，本地材料保留。
- B8 unknown/no-replay 边界包含在 500 项 OwnerTruth 完整回归中。

状态：`PASS_LOCAL`。

## 3. R2：start 原命令恢复

### 根因

1. 已曝光或结果未知的 start 可以被普通 current-session 命中直接接管，绕过原 command 的精确结果核实。
2. 当前 UseCase 新建 start 时只使用局部 command，`preparedStartCommand` 没有同步绑定；丢回执后，即使精确只读查询命中，`bindReadOnlyVerifiedStart` 也无法恢复当前对象。

### 修改

- `OwnerTruthContracts.swift`
  - `startNewSession`
  - `receiveCurrentSession`
  - `classifyStartCommand`
  - `bindReadOnlyVerifiedStart`
- Live start 在准备和曝光阶段把同一原 command、session/product 坐标及 dispatch state 保存在 UseCase 与既有磁盘 journal 中。
- `.mayExpose` / `.outcomeUnknown` 的 start 不接受裸 current-session 作为命令结果证明；只能由原坐标的精确只读 operation/status 结果解决。
- 精确结果匹配后，同一个 UseCase 可进入 ready，并继续派送原磁盘队尾；不创建替代 command，不重复 start POST。
- prepared-not-exposed 才允许原命令首次发送；已曝光/未知仍保持只读核实。

### 红绿证据

- 红：`evidence/red/R1-R3-pre-fix.xcresult`
  - `testLiveUnknownPreparedStartCannotUseCurrentSessionAsCommandProof` 修前错误进入 ready。
  - `testLiveSameUseCaseBindsExactStartReadOnlyResultAndDrainsOriginalTail` 修前无法恢复 ready。
- 绿：`evidence/green/R1-R3-final-green.xcresult`
  - 两个相同业务断言均通过；原 start POST 保持一次，command/session 不变，精确命中后才继续队尾。

状态：`PASS_LOCAL`。

## 4. R3：原始事件身份与关闭交接

### 根因

1. 已登记旧 questionID 的迟到显式包会无条件重写 active question window，导致后续无 ID 的 Q2 正文回滚到 Q1。
2. router 已 freeze 但尚未 deliver 时只保留动态 delegate；停止或换场后迟到执行会重新取得当前 ingress，可能丢失原场事件或串入新场。

### 修改

- `DialogEngineManager.swift`
  - `DialogProviderCanonicalIngressRouter.freeze`
  - `install` / `close`
  - `deliverAssistant`
  - `DialogProviderCanonicalAssistantStreamState.consume`
- 只有新的显式 questionID 可以推进 active window；已登记旧 ID 的迟到包仍归旧成员，但不回滚当前无 ID 窗口。
- freeze 时捕获原场 `engineGeneration`、`dialogOperationID` 和原 delivery closure；延迟执行不再重新读取当前 Echo ingress。
- 原场停止或换场后，已 freeze 的合法事件只交给原场；账号作用域失效时 fail closed，不跨账号落盘。
- assistant `response` 分段和 `ended` 完成共用生产 assembler，回复 ID 变化会开启新流，结束包只完成相同 reply。

### 红绿证据

- 红：`evidence/red/R1-R3-pre-fix.xcresult`
  - `testManagerLateExplicitOldQuestionDoesNotRollbackIdentifierWindow` 修前 Q2 被归为 Q1。
  - `testManagerFrozenDeliveryRemainsBoundToOriginalSessionAcrossStopAndReplacement` 修前原事件没有进入原场。
  - 账号失效场景修前已经 fail closed，作为既有保护保留，不人为制造红测。
- 绿：`evidence/green/R1-R3-final-green.xcresult`
  - 上述两条及 `testManagerFrozenDeliveryIsDiscardedAfterAccountScopeChanges` 均通过。

状态：`PASS_LOCAL`。

## 5. R4：真实本地组合链

### 新增组合验证

`testManagerEchoRealGateBackendLogicalTwentyMinutesClosesAtExactWatermark` 实际贯穿：

1. Manager 生产共用的 owner ASR 解析与 assistant 文本流 assembler；
2. `EchoViewController` 与 `EchoLiveMemoryCaptureCoordinator`；
3. 隔离磁盘 Outbox、follow-up、completion checkpoint；
4. 真实 `FeatureGateService` evaluator/cache；
5. 真实 `DreamJourneyBackendClient` 与 `requestJSON`；
6. 受控 URLProtocol 网络与注入时钟；
7. start、20 条 owner/assistant 消息、一次精确 delivery-status、end、inbox、ack、admit、status。

场景包含 10 个 identifier-only 成员、interim、明确 final、重复 final、assistant response 分段与 ended。逻辑时间推进 1,200 秒，跨多次 300 秒 TTL；第 4-6 问并发等待一次共享刷新；第 15 序列模拟服务端已提交后本地断网，再以精确只读结果恢复；最终关闭水位严格为实际登记的 `N=20`。

关键断言：

- 消息 POST 恰为 20；commandID 不重复。
- delivery-status GET 恰为 1；不重发第 15 序列写入。
- end、ack、admit 各 1 次，status GET 1 次。
- Outbox owner turn 为 10，pending 与未封存成员为 0，关闭水位为 20。
- 诊断可见 task resumed、HTTP 201；日志断言不依赖正文或原始身份标识。

### 红绿证据

- 业务红：`evidence/red/R4-integrated-business-red.xcresult`（保留原包 `evidence/green/R4-integrated-third.xcresult`，红目录为只读符号链接）
  - 精确只读已确认服务端提交后，prepared-only 队尾没有恢复派送，组合断言失败。
- 修复：`EchoViewController.receiveNaturalInputState` 及 delivery-status 恢复路径只对精确确认的未知项推进；prepared-not-exposed 队尾恢复原命令首次派送，可能已曝光项继续只读。
- 绿：`evidence/green/R4-integrated-fifth.xcresult`。
- 明确 deny 业务红：`evidence/red/R4-explicit-deny-business-red.xcresult`（原包 `evidence/green/R4-explicit-deny-second.xcresult`），修前把发前 deny 错误升级为 statusUnknown。
- 明确 deny 绿：`evidence/green/R4-explicit-deny-final.xcresult`，保持 syncPaused、磁盘 1 条、消息 POST 为 0。
- `R4-integrated-first.xcresult` 是组合夹具遗漏 `/presentation` 的构建过程证据；`R4-integrated-fourth.xcresult` 是测试代码编译错误，均不冒充业务红测。

状态：`PASS_LOCAL`。

## 6. 测试与构建

### 实际命令形态

```bash
xcodebuild test -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=8C90FF12-82E3-41A6-A003-EE0BB26BEAA6' \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/<定向用例> \
  -resultBundlePath <本 run/evidence/green/*.xcresult>

xcodebuild test -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=8C90FF12-82E3-41A6-A003-EE0BB26BEAA6' \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests \
  -resultBundlePath <本 run/evidence/green/OwnerTruthContractsTests-full-final-v2.xcresult>

xcodebuild test -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=8C90FF12-82E3-41A6-A003-EE0BB26BEAA6' \
  -only-testing:DreamJourneyTests/AudioOwnerLeaseModelTests \
  -only-testing:DreamJourneyTests/EchoTurnIntentReducerTests \
  -resultBundlePath <本 run/evidence/green/Echo-Audio-regression-final.xcresult>

xcodebuild build -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -configuration Debug -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO

xcodebuild build -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -configuration Debug -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO
```

### 结果

| 门禁 | 结果 | 证据 |
| --- | --- | --- |
| R1-R4 + S01-08 定向 | `PASS`，9/9 | `evidence/green/R1-R4-final-targeted.xcresult` |
| OwnerTruth 完整回归 | `PASS`，500/500 | `evidence/green/OwnerTruthContractsTests-full-final-v2.xcresult` |
| Echo/音频保持性 | `PASS`，22/22 | `evidence/green/Echo-Audio-regression-final.xcresult` |
| 通用模拟器编译 | `PASS` | `evidence/build/Generic-Simulator-final.xcresult` |
| 通用 iOS 设备编译（无签名） | `PASS` | `evidence/build/Generic-iOS-final.xcresult` |
| `git diff --check` | `PASS` | `evidence/source-and-build-fingerprints.txt` |
| 后端门禁/隔离 PostgreSQL | `NOT_RUN / N/A` | 本轮未改后端合同或事务 |
| 独立 XCUITest UIQA | `NOT_RUN` | 当前复核要求为真实 UIKit 对象与组合链；未把它写成 PASS |
| 真实 iPhone | `NOT_RUN` | 按要求暂停 |
| 真实 Provider/SDK 顺序 | `NOT_RUN` | 本地仅走生产共用解析/assembler 的受控包 |
| 物理 20 分钟 | `NOT_RUN` | 本地为注入时钟的逻辑 20 分钟 |

## 7. 保持性与安全边界

- B8、B8-S01、S01-08 继续通过，未知业务写仍只读核实，不以 POST 查询或自动重放。
- start/append/end/ack/admit 保留原 command、session、batch、版本和账号作用域。
- 没有放宽 authority、FeatureGate、账号租约、CAS、hash、revision 或 Binding。
- 原生 Live 声音、低延迟、连续聆听、打断和正式记忆绑定未改；未恢复旧串行语音链路。
- 日志只使用安全阶段、枚举、随机 trace/attempt 与状态码；本轮未新增正文、token、密钥、完整响应或原始业务 hash 日志。

## 8. 部署与残余风险

- 后端：本轮无改动，无后端部署需求。
- iOS：需要后续授权后安装本地验证产物，才可执行真机验收。
- 发布顺序：无需后端先行；iOS 真机验收通过后再决定发布。
- 残余风险：真实 SDK 可能提供本地受控包未覆盖的字段缺失、乱序或 reply/question 身份组合；物理长场还需验证自然 TTL、真实网络切换和最终关闭体验。

## 9. 局部回退

如真机发现回归，只按本轮函数块回退：

1. ack fresh authority 对旧 route Bool 的分支；
2. Live start 原 command 的 current-session 阻断和同对象只读绑定；
3. Manager active question window 与冻结 delivery binding；
4. assistant 生产共用 assembler 和 Echo 精确状态后队尾恢复条件。

回退不得删除磁盘坐标、恢复未知写重放、放宽账号/策略门禁，亦不得回退前序 B8/B8-S01/S01-08 成果。

## 10. 结论

R1-R4 的本地修复、定向红绿、真实本地组合链、完整 OwnerTruth 回归、Echo/音频保持性及两类通用构建均已完成。因此本轮状态可更新为：

`A_LOCAL_PASS / READY_FOR_LIVE_DEVICE_RETEST`

工作停在本地交付阶段，等待复核及后续真机授权。
