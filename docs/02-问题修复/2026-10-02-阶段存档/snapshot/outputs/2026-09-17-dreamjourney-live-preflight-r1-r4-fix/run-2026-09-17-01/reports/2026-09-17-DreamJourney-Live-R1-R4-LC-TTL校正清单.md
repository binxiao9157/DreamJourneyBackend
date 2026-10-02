# DreamJourney Live R1-R4 LC / TTL 校正清单

口径：`PASS_LOCAL` 仅表示本地生产共用代码、真实 FeatureGate evaluator/cache、真实 BackendClient/requestJSON、隔离磁盘和受控网络下的业务断言通过。真实 iPhone、真实 Provider/SDK 顺序和物理 20 分钟仍为 `NOT_RUN`。

本清单取代上一 run 中 LC-08、TTL-L04 的 `PARTIAL` 判定；其余项目保留既有结论，并由本轮 500 项完整回归再次验证。

## LC-01 至 LC-15

| ID | 状态 | 具体测试与断言 | 证据边界 |
| --- | --- | --- | --- |
| LC-01 | `PASS_LOCAL` | `testExplicitFinalASRMapsToCompleteCanonicalOwnerTurn`：仅 explicit final/confirmed 映射 complete。 | 真实 SDK 包 `NOT_RUN`。 |
| LC-02 | `PASS_LOCAL` | `testASRParserRequiresTypedFinalityEvidence`：缺失、错误类型和 legacy definite 均按 typed 规则处理。 | Provider 全变体 `NOT_RUN`。 |
| LC-03 | `PASS_LOCAL` | `testLiveCanonicalOutboxRetainsDeduplicationAfterAcknowledgement`：完成/确认后重复 final 不产生新 delivery。 | 真机 `NOT_RUN`。 |
| LC-04 | `PASS_LOCAL` | `testLiveCanonicalOutboxIgnoresLateInterimAfterComplete`：complete 后迟到 interim 不覆盖正文或状态。 | 真机乱序 `NOT_RUN`。 |
| LC-05 | `PASS_LOCAL` | `testLiveCanonicalConflictDoesNotBlockFollowingOwnerTurn`：不同 final 明确冲突，后续问题仍可处理。 | 真机 `NOT_RUN`。 |
| LC-06 | `PASS_LOCAL` | `testManagerIngressFreezesIdentifierWindowBeforeEchoQueueAndStop`、`testManagerFrozenDeliveryRemainsBoundToOriginalSessionAcrossStopAndReplacement`：freeze 身份与原场交付绑定。 | 真实 SDK callback 调度 `NOT_RUN`。 |
| LC-07 | `PASS_LOCAL` | `testProviderEventMetadataAcceptsIdentifierOnlyASRAndNestedReplyIDs`、`testManagerFrozenDeliveryIsDiscardedAfterAccountScopeChanges`：identifier-only 可登记，歧义/跨账号 fail closed。 | Provider epoch 真实歧义 `NOT_RUN`。 |
| LC-08 | `PASS_LOCAL` | `testManagerEchoRealGateBackendLogicalTwentyMinutesClosesAtExactWatermark`：10 问经过 Manager 共用解析/assistant assembler、Echo、磁盘 Outbox、真实 evaluator、BackendClient/requestJSON；20 条消息、N=20、唯一 end/ack/admit/status。 | 受控包与网络；真实 SDK 10 问 `NOT_RUN`。 |
| LC-09 | `PASS_LOCAL` | `testLiveCanonicalOutboxDoesNotSealCompleteSuffixAcrossPendingGap`：首个 gap 阻断后缀封存。 | 真机 `NOT_RUN`。 |
| LC-10 | `PASS_LOCAL` | `testEchoStopDrainsCanonicalEventRegisteredBeforeClose`、R3 freeze→deliver 测试：stop 排空已接受事件后冻结水位。 | 真机调度竞态 `NOT_RUN`。 |
| LC-11 | `PASS_LOCAL` | R4 10 问每问先发 identifier-only，再 interim/final；关闭后 N 按实际 20 条 delivery 计算。 | 真实 SDK identifier-only `NOT_RUN`。 |
| LC-12 | `PASS_LOCAL` | `testLiveCaptureCloseWithOnlyInterimMaterialRemainsDiscoverableAfterRestart`、`testLiveCaptureSettledInterimClosePersistsCoverageGapInsteadOfEmpty`：partial/gap 持久且不伪 complete。 | 系统杀进程真机 `NOT_RUN`。 |
| LC-13 | `PASS_LOCAL` | `testEchoIdleKeepsCurrentLiveCoverageGapVisible`：真实 Echo controller 的当前场 gap 状态不被 idle 覆盖。 | 真机 UI `NOT_RUN`。 |
| LC-14 | `PASS_LOCAL` | `testCanonicalAssistantTextStartsFreshForEachReply` 及 Echo/音频 22/22：assistant 文本流按 reply 隔离，文本完成不改变音频所有权规则。 | 真实 SDK 播放 `NOT_RUN`；不设车机专项门槛。 |
| LC-15 | `PASS_LOCAL` | `testB8S01StalePollFromRoundACannotConsumeRoundBOrClearItsPoll` 与 500 项回归：B8/B8-S01/S01-08、unknown write、跨轮 poll、B6 no-replay 保持通过。 | 修复版 B8 真机闭环 `NOT_RUN`。 |

共同结果包：

- `evidence/green/R1-R4-final-targeted.xcresult`：9/9。
- `evidence/green/OwnerTruthContractsTests-full-final-v2.xcresult`：500/500。
- `evidence/green/Echo-Audio-regression-final.xcresult`：22/22。

## TTL-L01 至 TTL-L12

| ID | 状态 | 具体测试与断言 | 证据边界 |
| --- | --- | --- | --- |
| TTL-L01 | `PASS_LOCAL` | `testLiveNaturalInputRefreshUsesFreshAuthorityForTypedWrites`、R4 逻辑 20 分钟：过期 route 后为原意图捕获新 authority，scene/product 不变。 | 设备自然 TTL `NOT_RUN`。 |
| TTL-L02 | `PASS_LOCAL` | R4 第 4-6 问在一个 deferred refresh 中并发排队；refreshCount 只增加一次，完成后按序派送。 | 真机并发 `NOT_RUN`。 |
| TTL-L03 | `PASS_LOCAL` | `testManagerEchoRealGateBackendExplicitDenyKeepsDiskTurnWithoutMessagePost`：fresh deny 后 0 消息 POST、磁盘保留、状态 syncPaused。 | 真机 `NOT_RUN`。 |
| TTL-L04 | `PASS_LOCAL` | `testManagerEchoRealGateBackendLogicalTwentyMinutesClosesAtExactWatermark`：注入时钟推进 1,200 秒、跨多次 300 秒 TTL、并发唤醒、第 15 序列断网后精确只读恢复、最终 stop、N=20、end→ack→admit→status。 | 物理 20 分钟 `NOT_RUN`。 |
| TTL-L05 | `PASS_LOCAL` | `testLiveStartCommandAndExposureSurviveStoreReconstruction`：start 原 command 在 POST 前持久化 prepared，曝光单向推进。 | 进程杀死窗口真机 `NOT_RUN`。 |
| TTL-L06 | `PASS_LOCAL` | `testLiveCaptureReadOnlyStatusConfirmsLostAppendWithoutSecondPost` 与 R4 第 15 序列：服务器已提交但本地断网时只读精确命中，原 POST 不增加。 | 真机网络中断 `NOT_RUN`。 |
| TTL-L07 | `PASS_LOCAL` | `testLiveCaptureDeliveryStatusTimeoutIgnoresLateResultAndAllowsBoundedRetry`、`testLiveCapturePartialStatusNeverPostsUnconfirmedQueuedTurn`：超时/部分结果保持未知，不跳序。 | 真机 `NOT_RUN`。 |
| TTL-L08 | `PASS_LOCAL` | `testLiveUnknownPreparedStartCannotUseCurrentSessionAsCommandProof`、`testLiveSameUseCaseBindsExactStartReadOnlyResultAndDrainsOriginalTail`：裸 current 不解锁未知 start；精确 operation 才恢复同对象和队尾。 | 冷启动真机 `NOT_RUN`。 |
| TTL-L09 | `PASS_LOCAL` | R1 fresh ack 测试及 `testB8AcknowledgementCommittedReceiptBindingMismatchRemainsOutcomeUnknown`：新 authority 不被旧 route 覆盖，回执仍严格绑定。 | 真机自然过期 `NOT_RUN`。 |
| TTL-L10 | `PASS_LOCAL` | `testB8FreshAcknowledgementUsesCurrentProductionDecisionWhenCapturedRouteExpired` 与 R4 closing chain：真实 evaluator/cache + BackendClient；end/ack/admit 保持原 command。 | 真机跨 TTL 关闭 `NOT_RUN`。 |
| TTL-L11 | `PASS_LOCAL` | R4 第 15 序列受控离线及 `testB8RealBackendAcknowledgement201WithUnboundReceiptRemainsUnknownAndNeverReplays`：写 transport 不递归认证重发，未知只读。 | 真实网络故障组合 `NOT_RUN`。 |
| TTL-L12 | `PASS_LOCAL` | `testManagerFrozenDeliveryIsDiscardedAfterAccountScopeChanges`、`testEchoQueuedCanonicalEventIsRejectedAfterAccountLeaseChanges`：旧事件、旧 authority 和旧回调不跨 lease。 | 真机账号自然轮换 `NOT_RUN`。 |

## 校正结论

- LC-08：由 `PARTIAL` 校正为 `PASS_LOCAL`，依据是新的真实本地组合链，不是测试总数。
- TTL-L04：由 `PARTIAL` 校正为 `PASS_LOCAL`，依据是同一组合中的逻辑 20 分钟、多 TTL、并发等待、受控断网、stop 与准确水位。
- 真实 iPhone、真实 Provider/SDK 事件顺序、物理 20 分钟：全部 `NOT_RUN`。
- 本地状态：`A_LOCAL_PASS / READY_FOR_LIVE_DEVICE_RETEST`。
