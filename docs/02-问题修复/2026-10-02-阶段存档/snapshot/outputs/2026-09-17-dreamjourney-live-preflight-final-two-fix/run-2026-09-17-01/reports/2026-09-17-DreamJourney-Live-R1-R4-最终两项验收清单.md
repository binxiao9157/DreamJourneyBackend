# DreamJourney Live R1-R4 最终两项验收清单

| 要求 | 文件 / 函数 | 修前证据 | 修后证据 | 状态 |
| --- | --- | --- | --- | --- |
| 非空场 freeze 后、handoff 前停止，不提前 close/end | `DialogEngineManager.swift`: `DialogProviderCanonicalIngressRouter.freeze`；`EchoViewController.swift`: `reserveCanonicalHandoff`、`completeCanonicalHandoff`、`finish`、`persistCloseIntent` | `remaining-two-pre-fix.xcresult` | `remaining-two-post-fix.xcresult`、最终定向包 | `PASS_LOCAL` |
| q2 只进原 A 场，B 场为零 | 同上，冻结 binding 捕获原场 | 同上 | `testManagerFrozenNewMemberDelaysNonEmptyCloseBoundaryUntilOriginalHandoff` | `PASS_LOCAL` |
| 停止后真正新 q3 仍拒绝 | router close + Store closeIntent 保护 | 既有规则 | 新反例最终断言 | `PASS_LOCAL` |
| 账号失效不跨账号交付 | Echo reservation 账号租约 + 原 binding | 既有保护已通过 | `testManagerFrozenDeliveryIsDiscardedAfterAccountScopeChanges` | `PASS_LOCAL` |
| t0 allow，t301 真实 gate 判定过期 | `DreamJourneyBackendClient.swift`: `FeatureGateService.now/requestDecision/revalidateRequest` | `remaining-two-pre-fix.xcresult` | `remaining-two-post-fix.xcresult` | `PASS_LOCAL` |
| 刷新后 fresh policy 允许并继续真实 transport | 同上 + BackendClient requestJSON | 同上 | `testB8FreshAcknowledgementUsesCurrentProductionDecisionWhenCapturedRouteExpired` | `PASS_LOCAL` |
| 真实 deny、unknown write、跨轮 poll 不回归 | 前序 R1-R4/B8/S01-08 | 前序证据 | 最终 10 条定向 + 501 项完整回归 | `PASS_LOCAL` |
| 普通文本持久化等待后仍写 closeIntent | `EchoViewController.swift`: `persistAndEnqueue` | `OwnerTruthContractsTests-full.xcresult` | `delivery-failure-isolation-v2.xcresult`、完整 v2 | `PASS_LOCAL` |
| Echo/音频行为保持 | 既有 Audio/Reducer 代码 | N/A | `Echo-Audio-regression.xcresult`，22/22 | `PASS_LOCAL` |
| 通用目标编译 | `DreamJourney.xcworkspace` | N/A | 两个 build xcresult | `PASS_LOCAL` |
| 真实 iPhone / SDK / 物理 20 分钟 | 后续授权 | 无 | 未执行 | `NOT_RUN` |

最终状态：`A_LOCAL_PASS / READY_FOR_LIVE_DEVICE_RETEST`。
