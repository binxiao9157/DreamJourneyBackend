# DreamJourney B4 诊断尝试编号与网络生命周期补充修复报告

日期：2026-09-12\
本轮结论：`A_LOCAL_PASS / READY_FOR_B4_RETEST`\
整体 B4：尚未通过；B4-1 至 B4-7 保留前序 PASS，B4-8 仍为 FAIL\
执行边界：未安装或测试 iPhone，未部署生产，未访问或修改生产候选、正式记忆、审核历史或 Dead Letter，未 commit/push

## 1. 基线与范围

- iOS 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- 分支：`feature/prd-stitch-ui-adaptation`
- HEAD：`5fd061fd869edbe1fc13e8535a47880826581934`
- Xcode：26.6（17F113）
- Alamofire：5.11.2（由 `Podfile.lock` 锁定）
- 工作区开始前已有大量 B4、Live 和会后恢复相关未提交修改；本轮未重置、回滚、暂存或覆盖这些成果。
- 本轮只修正候选列表读取的 attempt 上下文和网络生命周期诊断，不修改审核业务语义、Live、正式记忆或后端合同。

## 2. 已证实根因

### 2.1 重试次数与完成上下文不一致

候选列表读取虽然在递归请求中增加了局部 attempt，但认证 successor、策略重新获取和最终 typed decode 没有共享同一份有效尝试状态。UseCase/UI 仍保留最初传入的读取上下文，导致第二次请求成功后，认证、解码或 `uiCommitted` 仍可能显示 attempt=1。

这不是单纯的日志字符串问题：最终完成回调没有把“实际产生结果的尝试上下文”交回 UseCase/UI，因此页面也无法准确表达哪次尝试提交了列表。

### 2.2 任务创建被误记为开始发送

旧实现从 Alamofire `onURLSessionTaskCreation` 记录 `transportStarted`。该回调只能证明 URLSessionTask 已创建，不能证明任务已 resume，更不能证明服务端收到请求。

第一次修正后还发现一个异步竞态：极快的受控响应可能先进入 response serializer，再收到 Alamofire 的 resume 事件；若在响应闭包提前释放观察器，会漏记真实的 task resume。最终修复将观察器保留到 Alamofire 请求真正 finish，并分别记录 taskCreated、taskResumed、transportCompleted 和 responseReceived。

## 3. 代码修改

### 3.1 `OwnerTruthContracts.swift`

- `OwnerTruthCandidateInboxReadContext`：定义同一读取意图共享 trace、每次恢复递增 attempt 的规则。
- `OwnerTruthCandidateInboxReadOutcome`：把最终有效的读取上下文与结果一起返回。
- `OwnerTruthCandidateInboxService.fetchOwnerTruthCandidateInboxRead`：新增带结果上下文的读取合同，同时保留兼容入口。
- `OwnerTruthCandidateReviewUseCase.refresh/receiveInbox`：只在 requestID 仍拥有当前读取权时接收结果，并将最终有效 attempt 交给 UI；旧尝试迟到不会覆盖新列表。
- `OwnerTruthCandidateInboxDiagnosticEvent`：增加事件时间，用 trace、attempt 和时间关联异步日志。

### 3.2 `DreamJourneyBackendClient.swift`

- `CandidateInboxReadAttemptState`：认证更新、策略重新获取和组合恢复共享同一有界计数器，不再在递归入口把 attempt 重置为 1。
- `fetchOwnerTruthCandidateInboxRead`：typed decode 和完成回调使用实际成功或失败的最终上下文。
- `requestJSON` 候选读取分支：分别记录 requestCreated、taskCreated、taskResumed、transportCompleted、responseReceived；只有实际存在 `HTTPURLResponse` 才记录 responseReceived 和状态码。
- `CandidateInboxTaskLifecycleProbe`：监听 Alamofire 5.11.2 的 `Request.didResumeTaskNotification` 和 `Request.didFinishNotification`，并将 probe 保留到真实 finish，避免快响应下丢失 resume 证据。
- `qaAuthSessionRefresher`：仅测试构造入口可注入受控合法凭据刷新，用于覆盖真实 401 刷新分支；生产初始化保持 nil，生产认证路径不变。
- 网络错误只记录白名单分类和安全元数据；未新增正文、令牌、密钥或原始响应日志。

### 3.3 `MemoryArchiveViewController.swift`

- 候选列表页面的 `uiCommitted` 使用 UseCase 传回的同一 trace 和最终有效 attempt，并记录事件时间。

### 3.4 `OwnerTruthContractsTests.swift`

- 新增真实 BackendClient、FeatureGate evaluator、URLProtocol 和 UIKit controller 组合反例。
- 覆盖首次成功、401 已有 successor、401 实际凭据刷新、策略重新获取、认证与策略组合恢复、DNS/离线/TLS/取消，以及创建后未 resume 即取消。
- 保留并执行旧结果隔离、读取单飞、尾随刷新和审核写入不自动重试的既有回归。

## 4. 修复前反例

| 反例 | 修复前结果 | 证据 |
|---|---|---|
| 401/策略组合恢复 | FAIL：尝试序列、HTTP attempt 和最终上下文互相不一致 | `evidence/pre-fix/red-attempt-lifecycle-2.log` |
| UIKit 最终提交 | FAIL：第二次请求成功，但 UI 上下文仍为 attempt=1 | 同上 |
| 创建后未 resume 即取消 | FAIL：错误地产生开始/响应语义 | 同上 |

结果：3 项测试共 13 个断言失败，`DiagnosticsRed2.xcresult` 已保留。旧红证据未被覆盖或改写。

## 5. 修复后自动化验收

| 场景 | 结果 | 证据 |
|---|---|---|
| 首次成功贯通 UI、UseCase、网络、HTTP、解码和 UI 提交 | PASS | `evidence/post-fix/green-attempt-lifecycle-expanded.log`、`DiagnosticsGreenExpanded.xcresult` |
| 401 后使用已有 successor 恢复 | PASS | 同上 |
| 401 后执行实际受控凭据刷新并恢复 | PASS | 同上 |
| 策略重新获取及认证+策略组合恢复 | PASS | 同上；策略单项另见 `green-policy-only.log` |
| DNS、离线、TLS、取消的安全分类；无 HTTP 响应不记录状态码 | PASS | 同上 |
| 创建后未 resume 即取消，不伪造启动或响应 | PASS | `green-lifecycle-race.log`、`DiagnosticsLifecycleRace.xcresult` |
| 快响应仍捕获真实 taskCreated/taskResumed | PASS | 同上 |

定向扩展测试：7/7 PASS。生命周期竞态复测：3/3 PASS。策略单项复测：PASS。

## 6. 完整回归、UIKit/UIQA 与编译

### 6.1 OwnerTruth 完整回归

- 命令：`xcodebuild test ... -only-testing:DreamJourneyTests/OwnerTruthContractsTests`
- 结果：340/340 PASS，0 failure。
- 日志：`evidence/post-fix/full-owner-truth-tests.log`
- 结果包：`evidence/post-fix/OwnerTruthFull.xcresult`

完整回归包含读取/审核交错、单飞尾随请求、旧结果隔离、关联组上下文、typed 结构差异、CAS 和实际 UIKit controller 流程。

### 6.2 真实模拟器 UIKit/UIQA

- 结果：PASS。
- 运行结果：`evidence/post-fix/uiqa/diagnostics-final/owner-truth-candidate-related-group-uiqa-result.json`
- 详情截图：`evidence/post-fix/uiqa/diagnostics-final/01-owner-truth-candidate-v5-detail.png`
- 关联组预览截图：`evidence/post-fix/uiqa/diagnostics-final/02-owner-truth-candidate-structured-group-preview.png`
- 明确验证：时间变化、可信度 `0.721 -> 0.724`、完整关联组操作和原子提交一次均为 true。
- 数据边界：使用 QA 登录旁路和合成候选，未访问生产账号或生产正式记忆。

### 6.3 编译

| 目标 | 结果 | 证据 |
|---|---|---|
| generic iOS Simulator | PASS，BUILD SUCCEEDED | `evidence/post-fix/simulator-build.log` |
| generic iOS device，关闭签名 | PASS，BUILD SUCCEEDED | `evidence/post-fix/generic-ios-build.log` |
| `git diff --check` | PASS，无输出 | 本轮终检 |

构建中只有既有第三方 SDK/弃用 API 警告，没有本轮新增编译错误。

## 7. 指纹

- 工作区差异 SHA-256：`e4febb15561260111569ced2b0cfd7483a34e5f716a14f828b6deed7fedc7376`
- `OwnerTruthContracts.swift`：`e49da4127cbf536da21d240dd0bc1041d5ba80d8df312f955c7a66f8a9538678`
- `DreamJourneyBackendClient.swift`：`1e1e5647caf780eab0ce959551768ce0df8eb60941d866b9ca771ca38b758954`
- `MemoryArchiveViewController.swift`：`91f4727cce58de4d0bc45a2c0c5e4e331d4e0a383203cc5281e10c1206cfcbed`
- `OwnerTruthContractsTests.swift`：`5b3df40a5f1c035c97c58556aba9f74b3f7bb2262d0f2c2edc46892c1a364782`

工作区差异指纹包含此前保留的全部未提交成果，用于后续确认安装包是否来自同一源码状态，不代表本轮独占改动。

## 8. 状态与未验证项

| 项目 | 状态 | 说明 |
|---|---|---|
| attempt 在 UI/UseCase/认证/策略/HTTP/解码间一致 | PASS | 已覆盖首次成功和多种有界恢复路径 |
| taskCreated 与 taskResumed 语义分离 | PASS | 使用锁定 Alamofire 的真实生命周期通知验证 |
| 无 HTTP 响应不记录 responseReceived/status | PASS | DNS、离线、TLS、取消均已覆盖 |
| 旧尝试迟到隔离、单飞与尾随刷新 | PASS | 完整 OwnerTruth 回归通过 |
| A 本地门禁 | `A_LOCAL_PASS / READY_FOR_B4_RETEST` | 本轮可独立执行项已完成 |
| B4-1 至 B4-7 | PASS | 保留前序有效证据，本轮未回退 |
| B4-8 | FAIL | 仍需真机重新采集会后保存与候选读取现场 |
| 生产候选读取原始失败层 | NOT_RUN | 本轮增强了可定位能力，但没有新的生产失败现场，不能宣称根因已查明 |
| 新出现 3 条候选是否重复 | NOT_RUN | 未操作生产候选 |
| 真实审核写入、投影、向量及文字/Live 回查 | NOT_RUN | 等用户明确开始真机复测后执行 |

## 9. 部署判断与影响

- 本轮没有后端业务代码、接口或迁移改动，不需要后端部署；也未执行任何部署。
- 未修改火山原生 Live 的声音、持续聆听、低延迟、打断或正式记忆绑定。
- 未恢复 ASR -> DeepSeek -> TTS，未修改文字问答、记忆事实、整理规则、检索或向量模型。
- 审核写请求没有新增自动重试。

## 10. 局部回退方案

如后续真机发现诊断接线回归，只撤销本轮以下局部修改：

1. `OwnerTruthCandidateInboxReadOutcome` 与最终 attempt 向 UseCase/UI 的传递。
2. `CandidateInboxReadAttemptState` 及候选读取分支的生命周期 probe。
3. 测试专用 `qaAuthSessionRefresher` 和本轮新增回归测试。

回退时不得恢复把 taskCreated 记为 transportStarted 的错误语义，也不得回退既有 requestID 所有权、旧结果隔离、审核 Binding、typed 原始结构判等、八种操作、CAS、正式记忆唯一事实源或原生 Live 链路。不得通过清数据、删除候选、放宽鉴权或修改正式记忆规避问题。

## 11. 停止点

本轮两个诊断缺口已完成本地修复和验证，状态为 `A_LOCAL_PASS / READY_FOR_B4_RETEST`。B4 整体仍未通过；等待用户明确要求开始后，再安装匹配版本并执行 B4 真机复测。
