# 最新 B8 修复对 Live → 待确认记忆路径的影响

核查日期：2026-09-16。范围：9/16 DJ-B8-DEVICE-01 本地修复相对于 9/15 真机复测/分析基线的增量。仅只读查源码、报告与已有证据；没有设备、生产或业务代码修改。

## 结论

**没有发现最新这轮 B8 修改新增了导致 Live `owner=0 / unsealed=20` 的采集或封存门槛。这轮修改也不能修复该状态。** 它改动的是已经成功 end 后的 ack/admission 交接层；Live 的 canonical transcript、outbox 完成前缀、close/end 前置条件均与 B8 开工前逐字相同。

**B8 并非只被文字会话使用。** 原生 Live 若能完成封存并成功 end，也会进入相同 coordinator 的 ack → admission → status 路径，因而可受益于 fresh admission authority 和安全回执分类。正常匹配的回执没有新增产品开关。发送后无法绑定的回执现在会进入 unknown 并禁止重放，这是有意的安全停止，不能称为正常路径退化。

但也不能据此说完整链已经恢复：**admission 成功后的即时 status observer 仍使用旧 candidate route**。旧 route 过期/先前拒绝时，新的 admission 可获授权而即时 observer 仍失败；本轮用真实 FeatureGate 方法和真实 StatusUseCase 源块复现了这一接缝。后续 B6 有条件地可以只读接手，不能称为永久丢失或永久断链。

上述结论仅排除/限定 9/16 这轮 B8 增量的影响，**不排除更早的 Live canonical 改造存在回归**，也不证明过去的整条 Live 链曾在相同包与条件下通过。

## 1. 比较基线不是 HEAD 累积差异

iOS HEAD 仍是 `11d0d0051b9be3cce57822dd059472d1e2536866`，工作树含多轮未提交修改。直接把 `git diff HEAD` 全部归于最新 B8 会误判归因。

本轮从 B8 报告附带的 `evidence/baseline/ios-working-tree-before.diff` 与 HEAD 原文重建开工前文件，逐 hunk 校验上下文，再与 9/15 分析的 [source-baseline.json](/Users/gaominge/Documents/liftora/outputs/2026-09-15-device-blockers-analysis/source-baseline.json) 校验 SHA：

| 文件 | 9/16 B8 开工前是否与 9/15 分析基线一致 | 最新 B8 增量 |
| --- | --- | --- |
| EchoViewController.swift | 是，SHA `9fda448e…` | 136 行增量 diff，主要是 completion 诊断和刷新 callback |
| OwnerTruthContracts.swift | 是，SHA `a0c2802f…`，同时匹配旧 probe 保存的源文件 SHA | 610 行增量 diff，限定 ack/admission 协议、use case 与诊断 |
| DreamJourneyBackendClient.swift | 是，SHA `61cdc3f7…`，同时匹配旧 probe 保存的源文件 SHA | 412 行增量 diff，fresh authority transport、诊断、QA 注入 |
| DialogEngineManager.swift | 是 | 全文不变 |
| ConversationMemoryManager.swift | 已重建 B8 开工前版本 | 全文不变 |
| ReleasePolicyStore.swift / AccountLease.swift | 与 9/15 分析 SHA 一致 | 全文不变 |

当前三处业务文件和测试文件 SHA 也分别与 [9/16 B8 本地修复报告](../../2026-09-16-dreamjourney-b8-ack-admission-fix/run-2026-09-16-01/reports/2026-09-16-DreamJourney-DJ-B8-DEVICE-01-本地修复报告.md) 一致。比较产物：[incremental-comparison.json](/Users/gaominge/Documents/liftora/outputs/2026-09-16-live-persistence-regression-audit/b8/incremental-comparison.json)、[baseline-continuity.json](/Users/gaominge/Documents/liftora/outputs/2026-09-16-live-persistence-regression-audit/b8/baseline-continuity.json)。三个 `.b8-incremental.diff` 保留完整增量。

## 2. 文字与原生 Live 共用的前后路径

| 阶段 | 文字输入 | 原生 Live | 最新 B8 是否改变 |
| --- | --- | --- | --- |
| 接收表达 | appendOwnerTurn/appendAssistantTurn，已整理文本直接分段入队 | appendCanonicalTurn；先落 canonical event，再按 finality 转为 delivery | 没改 |
| 转为可派送文本 | 已形成稳定 delivery | outbox flushCompletedCanonicalPrefix，只派送有序完整前缀；遇未 complete 项停止 | 没改 |
| 用户结束 | finish → close intent | 同一 finish → close intent；仍需排空/封存 | 没改 |
| seal close 水位 | closeIntent 已落盘、持久化完成、unsealed=0、owner>0 | 相同条件 | 没改 |
| end | 所有待送项确认后，persistedOwner>0、有效水位，发送 end | 相同条件 | 没改行为，仅增加 end 回执/磁盘诊断 |
| ack | end 回执持久化→寻找精确 batch→prepareAck→ack→绑定→ack checkpoint | 共用同一链 | 新增诊断；曝光后回执不匹配由错误 notSent/contractMismatch 改为 unknown |
| admission | 原 command 落盘→candidate 专属授权→admit→绑定→admitted checkpoint | 共用同一链 | fresh decision 绑定 lease/generation/trace，并贯穿 BackendClient/requestJSON；保留有界刷新 |
| followup/status | 先保存 followup，再删除已完成 outbox/checkpoint，然后读精确 batch status | 共用同一链 | 没改即时 observer 与 B6 discovery 行为 |
| 待确认 | status.reviewReady 后进入 pendingReview；候选读取是另外的读链 | 相同 | 没改 |

所以 `owner=0/unsealed=20` 发生在上表前半段，它尚未达到 B8 新鲜 admission 授权的执行点。B8 修改不会把这些未 complete 的 canonical turn 转成 delivery，也不会补出关闭水位。

## 3. 对 owner=0 / unsealed=20 的具体排除证据

[函数逐字比较](/Users/gaominge/Documents/liftora/outputs/2026-09-16-live-persistence-regression-audit/b8/function-unchanged-evidence.json) 证明 14 个关键函数/类型 B8 开工前后相同，其中：

- [EchoViewController.swift:1443](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1443) `appendCanonicalTurn` 没变。
- [EchoViewController.swift:1488](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1488) `finish` 没变。
- [EchoViewController.swift:1633](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1633) `persistCloseRequestIfReady` 仍要求 `unsealedCanonicalTurnCount == 0` 且 `ownerTurnCount > 0`。
- [EchoViewController.swift:1801](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1801) `advanceNaturalInputPipeline` 仍在 pending 队列排空后要求 `closeRequestPersisted`、`persistedOwnerTurnCount > 0` 才 end。
- [OwnerTruthContracts.swift:15311](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:15311) 整个 `OwnerTruthInterviewLiveTurnOutboxStore` 没变；[15775](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:15775) 完成前缀逻辑仍遇非 complete turn 即 break，仅生成 owner delivery 才递增 ownerTurnCount。

这种 `owner=0` 可以表示本地已有 owner canonical 文本但尚未封存成 delivery，不等于用户没有说话。真正原因必须回到 SDK final 事件、canonical 归一和 seal 责任链定位；最新 B8 没有碰这些部分。

9/15 的该 Live 失败发生在 9/16 B8 本地修复之前。B8 报告还明确本轮未安装真机。因此不能把较晚且未安装的这轮修改归为较早现场故障的起因；本轮未读取当前手机包，不能据此断言手机此刻的最新版本。

## 4. B8 已修到哪一层，以及实际行为边界

当前 [OwnerTruthContracts.swift:11445](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:11445) 先从 client 获取 fresh authority，刷新后再捕获，优先发送 authority 版本的 typed write。[BackendClient.swift:12532](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:12532) 以 freshRequestFeatureDecision 实现该接口；authority 重载把原 decision 送入 requestJSON，而不重新取旧 route。旧 route 本身仍保持拒绝合同。

新增 authority 要求 candidate feature、request purpose、allowed、原 lease/generation 匹配。这些是本次请求授权绑定，不是新增 Live 完成条件、用户资格产品开关或 ownerTurn 门槛。正常服务器回执仍按原 vault/batch/session/version 合同验收。

新 unknown 分类会让“已经曝光、但回执/账号不能可靠绑定”的任务停止自动写。这是纠正旧的错误重试能力；它适用于共享链中的文字和 Live，可能更早显示待核实，但并不减少已持久化的用户内容或阻断有效的正常回执。

`ownerTruthInterviewNaturalInputTransport` 从直接调用 FeatureGateService 改为 `requestFeatureDecision(.echoTextInput)`。生产默认没有 qaFeatureDecisionProvider，因此默认仍调用原 `requestServerPolicyManagedDecision`，不是新增门槛；变化主要使测试能注入真实 gate 对照。`requestJSON` 的 runtime/feature 分支仅增加诊断，原守卫没有变。

仍未修的长场残余：natural-input 的 echoTextInput gate（包括 ack 的 naturalInputPolicyAvailable）继续复用旧 route；本轮 fresh authority 只解决 candidate admission，不可扩张成所有 start/append/end/ack 过期授权问题均已解决。此点属于既有边界，不是本轮新引入。

## 5. admission 后的即时 observer 残余与 B6 接手条件

### 即时 observer 会怎样

1. [EchoViewController.swift:2283](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2283) admission 已提交后，先 upsert 原 productSession/batch 的 followup，再清理 outbox/checkpoint。
2. [2326](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2326) `beginCandidateReadinessObservation` 仍传 `candidateReviewPolicyAvailable`，它在 [12010](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:12010) 仍问旧 route。
3. [OwnerTruthContracts.swift:7230](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:7230) StatusUseCase 发前 Bool=false 就 `.unavailable(.releasePolicyDisabled)`，没有 GET。回执提交也再次问同一 Bool。
4. 即使上层读门禁通过，[BackendClient.swift:11047](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:11047) 原 status GET adapter 仍可能取旧 route。
5. Echo 收到 unavailable 后在 [2402](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2402) 设置 capture `.unavailable`。它是 terminal，会取消 poll/timeout；UIKit 在 terminal callback 释放此 capture coordinator。**该失败分支不会立即创建 B6 reader。**

本轮已编译运行真实 `FeatureGateService.requestDecision/fresh...`、FeatureGateEvaluator、真实 `OwnerTruthInterviewCandidateProposalStatusUseCase` 源块；仅账号和读取端口为合成 fixture。结果：[probe/result.json](/Users/gaominge/Documents/liftora/outputs/2026-09-16-live-persistence-regression-audit/b8/probe/result.json)。

| 输入 | 实际结果 |
| --- | --- |
| fresh admission decision 已允许，旧 route 过期 | status phase=unavailable，reason=capturedPolicyExpired，status client read calls=0 |
| fresh admission decision 已允许，旧 route 最初 denied | status phase=unavailable，旧 reason=featureDisabled，read calls=0 |
| fresh observer 对照 | read calls=1，phase=ready |

只证明即时读取接缝，没有运行 admission POST、UIKit、网络或 B6；不能冒充完整链红绿。

### 后续能否恢复

并非永久断链。正常 admission 后 followup 已落盘，保留精确同场 batch 坐标。后续 viewDidAppear、前台激活、account readiness 或入口 policy refresh 会调用 [resumePendingLiveMemoryOrganizationsIfNeeded:12120](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:12120)。terminal capture 从 retained 集合释放后，该场不再被排除，B6 可发现 followup-only 任务。

正式 BackendClient 符合 bounded reader 协议；[EchoViewController.swift:3043](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:3043) 优先走这个分支，**在旧 Bool 守卫之前返回**。因此该 B6 路径没有被同一个 Bool 直接阻断。

[BackendClient.swift:9540](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:9540) bounded reader 对 `expiredPolicyCache / capturedPolicyExpired / policyVersionChanged` 能重新获取 fresh decision，必要时有界只读刷新，然后 GET；仍需账号、deadline、runtime、网络和返回状态合法。因此旧 route 过期情况下，后续页面/前台触发有机会收敛到同场真实结果。

若旧 captured decision 最初就是 denied，而且 reason 不在上述三个可恢复原因内，bounded reader 当前不会自动将它当过期刷新。可能需要后续重新捕获 route 或另一明确读入口；不能泛称 B6 一定恢复，也不能绕过实际当前 policy deny。

这段 B6 结论来自完整静态调用链，本轮没有执行 B6 组合复现。准确表述是“即时 observer 仍可能失败，已有后续只读恢复路径可在特定条件下接手”，不是“admit 后永远无法进待确认记忆”。

## 6. 验证状态与下一步判别

9/16 修复报告记载 B8 定向/UIKit 20项、OwnerTruth 469项、音频保持5项及构建通过；报告明确真机安装、复测、生产链路为 NOT_RUN。本轮只核对报告指纹与当前源码，未重跑这些套件，不能以报告本地 PASS 当作手机已恢复。

本轮实际完成：开工前源码重建与 SHA 连续性验证、5文件增量差异、14个函数/类型逐字不变验证、3个真实源块即时 status probe。未运行设备/生产、未改业务代码。

下一次判断需要区分两类场景：

- 若仍是 owner=0/unsealed>0、没有 close/end：先修 canonical final/seal 路径；B8 代码还没被执行到。
- 若已经 end、ack、admit committed：用同场 trace、followup 与 status GET 判断即时 observer 和 B6 接手，不能继续把该失败叫“未封存”。

因此不能整体回退最新 B8 来试图解决 Live 未封存。应保留已验证的 fresh authority、unknown 只读边界，针对真正中断的阶段做独立修复与真机闭环。
