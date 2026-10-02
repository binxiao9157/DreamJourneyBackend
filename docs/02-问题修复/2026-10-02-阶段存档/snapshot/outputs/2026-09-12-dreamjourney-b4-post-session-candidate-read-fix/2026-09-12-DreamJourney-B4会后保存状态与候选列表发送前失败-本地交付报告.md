# DreamJourney B4 会后保存状态与候选列表发送前失败：本地交付报告

日期：2026-09-12\
阶段：A_LOCAL_PASS / READY_FOR_B4_RETEST\
整体 B4 状态：B4-1 至 B4-7 保持 PASS；B4-8 仍为 FAIL\
执行边界：未安装或测试 iPhone，未部署生产，未操作生产候选、正式记忆、历史数据或 Dead Letter，未 commit/push

## 1. 结论

本轮两条本地修复线已经完成：

1. Live 会后保存：修复 continuation 重复发布同一 ended receipt 时，把已经进入 `queued` 的同场任务错误退回 `saving` 的问题；补齐 end/ack/admit 命令级检查点、单飞观察、乱序终态保护、超时展示和旧关闭记录的安全恢复。
2. 候选列表：补齐从客户端入口到 UI 提交的分层安全诊断；同主体合法凭据更新时，只执行一次有界只读恢复，并立即废弃旧 Candidate、Proposal、Binding、选择项、确认闭包和迟到渲染回调；真实换账号继续 fail closed。

本轮未修改后端业务代码，因此不需要数据库迁移或后端部署。用户后来重开 App 能看到 3 条候选，只证明列表后来恢复可见，不能证明此前失败原因。生产设备上的实际失败层仍待 B4 真机复测取证。

## 2. 基线与范围

### 2.1 iOS

- 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- 分支：`feature/prd-stitch-ui-adaptation`
- 基线 HEAD：`5fd061fd869edbe1fc13e8535a47880826581934`
- 工作区：保留既有未提交修改；本轮未重置、未回滚、未暂存或提交。

### 2.2 后端

- 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`
- 基线 HEAD：`be9670b6ec05e73ab9562943f402e5a9e1346988`
- 本轮后端业务变更：无。

### 2.3 已证实根因与待验证假设

| 项目 | 结论 | 证据 |
|---|---|---|
| `queued -> saving` 状态倒退 | 已证实根因：相同 ended receipt 被 continuation 重复送达时，旧实现先写 `.saving`，再判断整理是否已经开始 | 修复前红测 `evidence/pre-fix/duplicate-ended-red-v2.xcresult` |
| 会后崩溃窗口 | 已证实设计缺口：旧本地状态只保留场次坐标，无法保证重启后重放原 end/ack/admit 命令 | 新检查点回归 `evidence/post-fix/checkpoint-regression.xcresult` |
| 旧 closing outbox | 已证实设计缺口：缺少可重放 end 命令时可能启动替代会话 | `evidence/post-fix/legacy-close-regression.xcresult` |
| 生产候选页曾“暂时无法读取” | 原始失败层未知；没有完整设备 trace、服务端访问日志和数据库只读证据 | B 阶段必须用新增 trace 定位，不能由“后来看到 3 条”反推根因 |

## 3. 代码变更

### 3.1 Live 会后状态

`DreamJourney/Sources/Modules/Echo/EchoViewController.swift`

- `EchoLiveMemoryCompletionCheckpointStore`：持久化不含正文的结束检查点及原始 end/ack/admit 命令。
- `EchoLiveMemoryCaptureCoordinator.clearCompletedOutboxThenBeginOrganization`：先按 ended receipt 身份幂等判定，再进入 `saving`；相同回执不回退、不重复 admission。
- `restoreCompletionCheckpointIfNeeded`、`beginAcknowledgement`、`beginAdmission`：重启后重放同命令和同批次，区分命令准备、服务端接受与本地清理。
- `isRestoringLegacyClosingOutbox`：旧记录缺少安全重放信息时进入 `statusUnknown`，不创建替代会话。
- 终态和观察逻辑：迟到 continuation、queued/失败/ended 不覆盖已确认业务终态；状态超时退出无限 `saving`，原观察器仍保持同场责任。

`DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`

- `OwnerTruthInterviewNaturalInputUseCase` 新增 `allowStartWhenCurrentMissing` 和 `willSendEndCommand`。
- acknowledgement/admission use case 支持注入已经持久化的 prepared command，避免崩溃恢复时生成新命令。

### 3.2 候选列表读取与审核上下文

`DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`

- 候选 GET 增加同一脱敏 trace 的阶段事件：adapter、featureInitial、applicationLease、auth、featureRevalidate、transportCreated、responseReceived、jsonDecoded、typedDecode、completion。
- 对 `accountGenerationChanged`、`capturedPolicyExpired`、`policyVersionChanged` 只允许一次策略重新捕获；真实禁用、主体或 authority 变化不放行。
- 日志只记录安全枚举、数量和随机 trace，不记录 token、正文、Prompt、body 或原始供应商响应。

`DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`

- `OwnerTruthCandidateReviewUseCase` 实现单飞读取和最多一次 trailing refresh。
- 同主体、同 authority 的合法凭据 successor 可更新 AccountLease 并重新读取；真实账号切换仍拒绝。
- 读取代次变化时丢弃旧网络完成，避免旧列表覆盖新列表。
- 修复 409 分类顺序，先识别 `sourceInactive`、`versionConflict`、`releasePolicy`，再落入通用请求失败。

`DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift`

- 收到审核上下文失效事件时，清空选择，关闭旧详情/更正/关联组预览，废弃确认闭包，并要求重新预览。
- 主线程迟到 render closure 受 `reviewContextGeneration` 约束，不能展示或提交旧上下文。

### 3.3 保持范围

- 未改火山原生 Live 的音频、连续聆听、打断或正式记忆绑定路径。
- 未恢复 ASR -> DeepSeek -> TTS 串行 Live。
- 未改变文字问答的 DeepSeek 且不朗读策略。
- 保留此前 typed 原始结构判等、`0.721 -> 0.724` 精度、八种操作、关联组、CAS 和展示/提交绑定。

## 4. S0-S6 状态

| 项目 | 状态 | 证据 |
|---|---|---|
| S0 基线与证据 | PASS | 两仓库 HEAD、dirty 状态及源码/产物指纹已记录；红测保留 |
| S1 结束事件幂等 | PASS | 修复前红测失败；修复后同用例进入 `pendingReview`，ack/admit 各一次 |
| S2 状态与持久恢复 | PASS | checkpoint、legacy close、终态/超时、完整 332 项回归 |
| S3 分层诊断 | PASS | 真实 BackendClient + FeatureGate/AccountLease/URLProtocol 分层回归 |
| S4 只读恢复及审核树失效 | PASS | credential successor、乱序完成、账号切换、旧绑定失效回归 |
| S5 自动化与 UIQA | PASS | 332/332、定向红绿、实际 UIKit UIQA；未以测试总数替代场景证据 |
| S6 编译与交付 | PASS | 模拟器和通用 iOS 设备目标均构建成功；本报告及证据索引已生成 |

## 5. L01-L12 会后状态矩阵

| 编号 | 状态 | 证据/说明 |
|---|---|---|
| L01 | PASS | 红：`duplicate-ended-red-v2.xcresult`；绿：`duplicate-ended-green.xcresult`，日志为 `queued -> pendingReview`，重复回执被忽略 |
| L02 | PASS | 幂等守卫不依赖中间阶段；focused/full 回归覆盖 queued、等待、未知和终态保护 |
| L03 | PASS | 同一绿测在重复 ended 后真实消费 `reviewReady` 并进入 `pendingReview` |
| L04 | PASS | terminal-state/late-continuation 回归保证终态不倒退且无第二次 admission |
| L05 | PASS | `testLiveCaptureMapsNoCandidatesFailureAndQuarantineToDistinctTerminalStates` |
| L06 | PASS | `testLiveCaptureStatusTimeoutLeavesObserverActiveWithoutInfiniteSaving`，保留同场观察责任 |
| L07 | PASS | continuation failure 在 organization 已接管或终态后只作 advisory，不覆盖现态 |
| L08 | PASS | checkpoint/follow-up/outbox 失败由 full regression 的持久化与 cleanup 场景覆盖；业务终态与本地清理分离 |
| L09 | PASS | `testLiveCompletionCheckpointReplaysOriginalEndAckAndAdmissionCommandsAfterCrash` |
| L10 | PASS | follow-up/outbox 恢复使用同一 batch observer；focused/full 回归未出现重复 admission |
| L11 | PASS | 生命周期恢复与 audio-owner 保持性回归通过；本轮未改原生 Live 音频控制 |
| L12 | PASS | AccountLease/authority 变化回归保证旧场回调不越界，隔离状态不清除 |

## 6. I01-I16 候选读取矩阵

| 编号 | 状态 | 证据/说明 |
|---|---|---|
| I01 | PASS | 首次读取和刷新共用可跟踪 GET；单飞测试验证预期请求数 |
| I02 | PASS | 真实 FeatureGate 和请求门禁阻断时不创建 transport |
| I03 | PASS | AccountLease 各拒绝结果保持无 GET、无可提交旧候选 |
| I04 | PASS | `testCandidateInboxCredentialRotationInvalidatesOldReviewBindingAndRereadsOnce` |
| I05 | PASS | `testRealHTTPClientRecapturesPolicyOnceAfter401CredentialSuccessorChangesGeneration`，使用真实 BackendClient evaluator 路径 |
| I06 | PASS | policyVersion/expired 可有界重捕获，真实 feature disable 仍 fail closed |
| I07 | PASS | recovery/config/authority 分支在完整回归中独立分类，config 成功不绕过门禁 |
| I08 | PASS | `testCandidateInboxMapsReadFailuresWithoutRetainingReviewableCandidates` 与客户端错误分类回归 |
| I09 | PASS | typed 解码、scope、重复 ID 和坏合同在 owner-truth 完整回归中 fail closed |
| I10 | PASS | V2/V4/V5 混合候选合同与实际 V5 UIKit 页面回归通过 |
| I11 | PASS | `testCandidateInboxRefreshIsSingleFlightWithOneBoundedTrailingRead` 及乱序回调测试 |
| I12 | PASS | credential rotation 后旧 Candidate/Proposal/Binding/选择/确认闭包失效，需重新预览 |
| I13 | PASS | `testCandidateInboxTrueAccountSwitchBlocksReadWithoutCreatingTransport` 及账号代次隔离回归 |
| I14 | PASS | 写请求不自动重试；上下文变化只允许只读核验，不声明写入结果 |
| I15 | PASS | `reviewContextGeneration` 拒绝已排队的旧主线程 render closure |
| I16 | PASS | 诊断合同仅输出白名单阶段、原因码、计数和随机 trace；完整回归未输出测试正文/token |

说明：I01-I16 证明本地合同、门禁、恢复和诊断可工作，不等于已经查明生产 iPhone 当时失败的具体层级。该结论必须由 B8-1/B8-2 的真实 trace 闭环。

## 7. 测试与构建证据

### 7.1 修复前后配对反例

- 修复前：`evidence/pre-fix/duplicate-ended-red-v2.xcresult`，1 项测试出现 2 个预期失败，确认 `queued -> saving`。
- 修复后：`evidence/post-fix/duplicate-ended-green.xcresult`，1/1 PASS，重复回执被忽略并继续 `pendingReview`。
- 第一份 `duplicate-ended-red.xcresult` 只记录隔离副本缺少 `Podfile.lock` 的环境失败，不作为缺陷证据。

### 7.2 定向与完整回归

- `checkpoint-regression.xcresult`：崩溃窗口原命令恢复 PASS。
- `legacy-close-regression.xcresult`：旧 closing outbox 不创建替代会话 PASS。
- `focused-regression.xcresult`：本轮 11 项定向合同 PASS。
- `batch-conflict-regression.xcresult`：409 业务冲突分类修复后 PASS。
- `owner-truth-full-regression.xcresult`：首次 332 项回归发现 2 项 409 分类回归，保留为发现证据。
- `owner-truth-full-regression-fixed.xcresult`：修复后 332/332 PASS，iPhone 17 Pro Simulator，iOS 26.5。

### 7.3 UIKit/UIQA

- 结果：`uiqa/post-fix/owner-truth-candidate-related-group-uiqa-result.json` 全部布尔门禁为 true。
- 截图 1：`uiqa/post-fix/01-owner-truth-candidate-v5-detail.png`，验证真实 V5 候选详情、修订、属性与提交入口。
- 截图 2：`uiqa/post-fix/02-owner-truth-candidate-structured-group-preview.png`，验证 `0.721 -> 0.724` 和 `2016 -> 2018` 的具体前后值及关联组原子提交。
- UIQA 构建日志：`uiqa/post-fix/install/build.log`，结果 `BUILD SUCCEEDED`。

### 7.4 编译

- 通用 iOS 设备目标：`evidence/post-fix/generic-device-build.xcresult`，PASS，0 error；依赖/历史代码有 70 条 warning。
- 模拟器目标：`evidence/post-fix/simulator-build.xcresult`，PASS。
- 模拟器运行时出现 App Group/APNs entitlement 警告，仅影响模拟器能力，不构成本轮合同失败。

## 8. 指纹

| 对象 | SHA-256 |
|---|---|
| `OwnerTruthContracts.swift` | `4f3a8d9080d80532e7f28b59c99c3e3dd951db16de2b3867447b2899371c96b3` |
| `EchoViewController.swift` | `bc82a4ae035f91b27d93802894a4bdf13e9b5aeeec091b793c777098e3644309` |
| `DreamJourneyBackendClient.swift` | `778a5b7bca062797c8e41b7dd9ce5d599baa67e210e54b9824fdf189149633f0` |
| `MemoryArchiveViewController.swift` | `b0558b18b54e3a0b34cc0668b185491837cd9acf0f8ef5e04e85a060f16b49f8` |
| `OwnerTruthContractsTests.swift` | `a964a3be943215db9f9ddf7bcc35d5796ee2542d9987243deaee51be70f7a705` |
| 通用设备构建中的 App 主二进制 | `1d95938fa73e867cd94d0f575c26d7bd00100592b6763f4c1be48eca89055792` |
| 最终模拟器测试构建中的 App 主二进制 | `2a82ca78a2e5ff213d3a402ad4c15dff4e84cf63341792a06a2a7aa73fe2005d` |
| 本轮四个主要源码 diff | `f9425d1a3cf2d892db7f805d82fbcf57d6ef8ce6a2b00ca0262c3300fe5879b9` |

## 9. 部署判断

- 后端业务代码、数据库合同和迁移均未改变。
- 本轮不需要后端部署，也未执行生产部署。
- B 阶段只需原位安装与上述源码一致的 iOS 诊断构建，不卸载、不清 App 数据。

## 10. B4 真机复测步骤

真机测试恢复后按顺序执行，每一步都由执行者抓取脱敏客户端 trace，并核对后端访问与业务状态：

1. B8-0：连接、解锁并允许原位安装；核对安装包指纹、后端版本、账号和数据不被清理。
2. B8-1：先进入此前失败的“待确认记忆”，点击右上角刷新；记录各诊断阶段，确认真实 GET、解码和 UI 提交。
3. B8-2：根据 trace 明确失败属于发送前、网络已启动但未到 API、响应后拒绝或 UI 提交，不凭候选数量猜根因。
4. B8-3：开始一场新 Live，询问一项已确认事实，确认有声音；朗读中插话并继续两轮，确认打断与恢复聆听。
5. B8-4：手动停止；跟踪同一 productSession/batch 的 end、ack、admit、status，确认不回退并离开无限“正在保存”。
6. B8-5：进入候选页刷新，只读核对新结果与本场关联；不替用户审核，不据此推断 3 条历史候选是否重复。
7. B8-6：整理期间切后台再回来或重开 App，确认恢复同一 batch、无重复 admission、旧场状态不覆盖新场 Live。
8. B8-7：仅在自然发生合法凭据更新时验证恢复；本轮未发生则保持 NOT_RUN，不主动退出登录或轮换凭据造场景。
9. B8-8：汇总客户端 trace、服务端访问、状态、截图和用户体验；仅对实际观察项判定。

## 11. 未完成项与残余风险

| 项目 | 状态 | 说明 |
|---|---|---|
| B4-1 至 B4-7 | PASS | 沿用已有有效验收记录，本轮没有回退这些能力 |
| B4-8 | FAIL | 需重新取得同场会后状态、真实候选 GET/解码/页面和场次关联联合证据 |
| 生产设备候选读取原始根因 | NOT_RUN | 新诊断已就绪，尚未在真实失败现场采集；后来可见不能替代取证 |
| 新出现 3 条候选是否重复 | NOT_RUN | 本轮不操作生产候选，仅待后续只读核对 |
| 新候选审核写入 | NOT_RUN | 必须由用户亲自审核并明确授权后验证，本轮未执行 |
| 自然凭据轮换 B8-7 | NOT_RUN | 不主动轮换生产凭据制造场景 |

## 12. 局部回退方案

如本轮代码在真机产生新回归，只回退以下局部能力，不覆盖同文件中的其他 B4 或用户修改：

1. 移除 completion checkpoint 恢复接线，恢复旧协调器构造参数；保留证据文件以便复现。
2. 回退候选只读 successor/单飞控制和分层诊断接线；不得恢复旧审核 Binding，也不得自动重试写请求。
3. 不回退 typed 原始结构判等、显示精度、八种操作、关联组原子确认或 CAS。
4. 不通过清数据、删除候选、关闭鉴权或修改正式记忆规避问题。
