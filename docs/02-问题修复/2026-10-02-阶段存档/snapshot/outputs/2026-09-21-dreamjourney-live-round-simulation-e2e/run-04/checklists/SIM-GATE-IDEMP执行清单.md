# SIM / GATE / IDEMP 执行清单

| 编号 | 状态 | 实际断言与原始证据 |
|---|---|---|
| SIM-01 | PASS | 两次独立 short 均保存 2 用户+2 助手回合；真实 iOS 候选列表可见；1 条候选经审核成为 1 条正式记忆，Store 重建读取一致。见 `green/logical20-full-chain.json`、`green/logical65-full-chain.json`、两份 `short-turn-ledger.json`。 |
| SIM-02 | PASS | runner 顺序固定为 short→指定 long；缺失、失败、不同 run/source/config/build 的 receipt 均返回 412 且业务请求计数不增加；合法 receipt 只消费一次，第二次为 409。见 `artifacts/runner-complete.json`、两份 `*-gate-negatives.json`、两份 long xcresult。 |
| SIM-03 | PASS | 逻辑 1200 秒，110 用户+110 助手，220/220 逐条角色、顺序和正文摘要匹配；真实客户端读到 4 条候选，正式记忆闭环后共 5 条。见 `logical20-turn-ledger.json`、`green/logical20-full-chain.json`。 |
| SIM-04 | PASS | 逻辑 3900 秒，150 用户+150 助手，300/300 匹配；真实客户端读到 17 条候选，正式记忆闭环后共 18 条；每次 Provider 输入字符数保存在结果包。见 `logical65-turn-ledger.json`、`green/logical65-full-chain.json`。 |
| SIM-05 | PASS | 重复、补充、纠正、撤回和问题混排走生产整理单元。稳定事实分别在 turn 7/189、7/221 出现在两个生产 unit，支持草案保留两处证据，最终仅 1 条候选；纠正、撤回及禁止片段均按预期。见两份 full-chain JSON。 |
| SIM-06 | PASS | SDK strict final、ASREnded/QueryConfirmed、Chat/TTS、旧 generation、150 个已完成 reply 的迟到重放由最终 OwnerTruth 全量回归覆盖；代表用例 `testCAP02StrictFinalSurvivesLaterInterimThroughProductionIngress`、`testCAP04ASREndedBeforeFinalSealsTheLaterAcceptedFinal`、`testR201...`、`testR202...` 均通过。 |
| SIM-07 | PASS | 停止前冻结事件、停止水位、换场、账号失效和重建归属由 `testCAP09StopWaitsForEveryFrozenObservationAndBoundaryHandoff`、`testManagerFrozenDeliveryRemainsBoundToOriginalSessionAcrossStopAndReplacement`、`testManagerFrozenDeliveryIsDiscardedAfterAccountScopeChanges` 等通过。 |
| SIM-08 | PASS | 认证拒绝、曝光后未知、原命令只读核实及禁止第三次 POST 由 `testLiveAuthenticationRetryPostExposureNotSentStaysReadOnlyWithoutThirdPost`、B8 401/201 unbound tests 等通过；全链没有替代命令。 |
| SIM-09 | PASS | 同账号恢复、明确 deny、账号切换后的迟到回调隔离由 `testLiveAuthenticationRetryLateRefreshWithSameAccountAndRestoredPolicyContinuesOriginalCommand`、`...AfterAccountChangeCannotSendOrClaim` 等通过。 |
| SIM-10 | PASS | 第 7 次 GET 迟到场景由 `testLiveLongMemoryRealControllerAutomaticallyReachesPendingReviewOnSeventhGET` 通过；本轮额外由真实 `OwnerTruthCandidateInboxViewController` 读取并校验本场候选正文、预览、来源及数量。 |
| SIM-11 | PASS | 后端受影响 91 项覆盖截断、非法 schema、证据遗漏/无效引用、typed failure、预算与恢复；真实 Worker/隔离 PG 全链同时完成。见 `logs/backend-affected-unittest.log` 及两份 server log。未调用真实 Provider。 |
| SIM-12 | PASS | 漏中段、漏尾段、错 Source、隐藏客户端候选均被独立断言捕获；恢复正确输入后完整链通过。见两份 `*-harness-negatives.json`。 |
| GATE-01 | PASS | receipt 绑定当前受保护源码、测试构建、实际配置、run、long attempt；旧/失败/重复 receipt 在 long 业务请求前失效。两场负例的 `businessRequestCount` 始终不变。 |
| GATE-02 | PASS | logical20 和 logical65 都让稳定事实跨两个生产整理 unit 重复，最终候选各只有 1 条并保留两个 turn 索引。 |
| IDEMP-01 | PASS | 修前相同断言失败：默认空 manifest 的重复 close 把已解决数从 1 降为 0；修后从 envelope 读取冻结 manifest，重建后仍为 1。见 `red/idemp01-red.xcresult`、`green/idemp01-green.log`。 |

## 回归与构建

- OwnerTruth 全量最终复跑：PASS，563 tests，3 skipped，0 failures。
- OwnerTruth 首轮：FAIL，563 tests，1 failure；失败项隔离重跑 PASS，最终全量复跑 PASS。原证据保留，不伪装为一次通过。
- Echo/音频/账号/核心合同：PASS，51/51。
- 后端受影响 unittest：PASS，91/91。
- 模拟器 `build-for-testing`：PASS。
- 通用 iOS 设备无签名构建：PASS。

## 未执行边界

- 真实 iPhone：NOT_RUN。
- 真实 Provider / 付费模型：NOT_RUN。
- 物理 20 分钟与物理 65 分钟：NOT_RUN；本轮仅使用生产共用时钟/调度注入验证逻辑时长。
- 部署、生产数据、历史任务重放或清理：NOT_RUN。
- commit / push：NOT_RUN。
