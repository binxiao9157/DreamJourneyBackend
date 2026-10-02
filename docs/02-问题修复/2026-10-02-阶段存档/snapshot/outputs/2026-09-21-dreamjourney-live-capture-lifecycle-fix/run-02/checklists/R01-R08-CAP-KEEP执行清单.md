# R01-R08 / CAP / KEEP 执行清单

## R01-R08

| 编号 | 状态 | 生产行为与关键断言 | 直接证据 |
|---|---|---|---|
| R01 | PASS | latest observation 与可信 strict final 分离；后到 interim 不覆盖 final；只有 interim 时不投递完整正文 | `testCAP02StrictFinalSurvivesLaterInterimThroughProductionIngress`、`testCAP04InterimOnlyBoundaryRemainsPartialThroughProductionIngress`；`green/r01-r03-production-green.xcresult` |
| R02 | PASS | 未绑定 QueryConfirmed 不封存语音；绑定文字请求仍保留 | `testCAP03UnboundQueryConfirmedCannotSealVoiceBeforeLaterFinal`、`testCAP03DifferentQueryConfirmedBodyCannotRewriteVoiceCanonical` |
| R03 | PASS | reply identity 独立缓冲；交错结束、Chat/TTS 隔离 | `testCAP05InterleavedRepliesRetainIndependentAssistantBodies`、`testCAP05RawChatIsCanonicalAndTTSRemainsPlaybackOnly` |
| R04 | PASS | canonical conflict 后继续落盘后续正文，但 end/ACK/admit 为 0；正常对照完整关闭 | `testCAP06CoordinatorConflictKeepsCaptureAndPersistsLaterTurnsAndCloseIntent`、CAP14 正常关闭测试 |
| R05 | PASS | stop 先持久化精确 manifest；冻结事件只交原场；overflow gap 重建后仍阻止完整关闭；持久移交后可释放 | `testCAP09StopWaitsForEveryFrozenObservationAndBoundaryHandoff`、`testManagerFrozenNewMemberDelaysNonEmptyCloseBoundaryUntilOriginalHandoff`、`testManagerFrozenDeliveryRemainsBoundToOriginalSessionAcrossStopAndReplacement`、`testCAP08OverflowGapSurvivesDiskRecoveryAndFullCoordinatorRebuild` |
| R06 | PASS | owner/assistant 首错、ordinal 与隔离范围持久；一万事件和竞争配额不覆盖首错 | `testCAP13FirstCriticalFailureSurvivesTenThousandEventsAndCoordinatorRebuild`、`testCAP13CriticalSessionSurvivesCompetingSessionQuota` |
| R07 | PASS | 同一 iOS Controller 通过真实 FeatureGate/BackendClient/requestJSON 与本地 FastAPI/PostgreSQL 双向响应驱动；Python 未重绑客户端命令 | `green/r07-cap15-ios-summary.json`、`green/r07-cap15-server-evidence-sanitized.json` |
| R08 | PASS | 42 项音频保持性；live_long_memory 非零 epoch、非法类型、陈旧 epoch、幂等重放均验证 | `regression/audio-keep-summary.txt`；后端 `test_live_admission_*authority*` 三项及 192 项门禁 |

R01-R03 的修前失败保留于 `red/r01-r03-production-red.log` 和 `red/r01-r03-production-red.xcresult`，修后相同业务断言见 `green/r01-r03-production-green.log`。R06 配额反例见 `red/r06-session-quota-red.xcresult`。R05/R06 绿证据见 `green/r05-r06-*.xcresult`。

## CAP-01 至 CAP-15

| CAP | 状态 | 场景证据 |
|---|---|---|
| CAP-01 | PASS | 重复/句号容差/真正差异；未封存更新、封存后 command 不变 |
| CAP-02 | PASS | strict final 后 interim 不降级 |
| CAP-03 | PASS | QueryConfirmed 不冒充语音封存，文字绑定保留 |
| CAP-04 | PASS | ASREnded 先到仍接收已登记 final；interim-only 保持 partial |
| CAP-05 | PASS | 交错 reply 两份正文；TTS 仅播放不入记忆正文 |
| CAP-06 | PASS | owner/assistant 冲突保留证据，后续回合继续保存，完整关闭写为 0 |
| CAP-07 | PASS | 本地 Outbox 六阶段原子写故障逐一恢复 |
| CAP-08 | PASS | 暂时磁盘故障、恢复顺序、overflow durable gap、完整重建 |
| CAP-09 | PASS | 每个观察/边界冻结，stop manifest 先落盘，排空后才 end |
| CAP-10 | PASS | 离页、Controller/Coordinator 重建、冷启动发现原场坐标 |
| CAP-11 | PASS | delivery/status 只读核实不把临时状态当终态 |
| CAP-12 | PASS | 多次 TTL、401 一次恢复、deny、账号切换和迟到回调 |
| CAP-13 | PASS | 10,000 事件、竞争 session 配额、诊断失败不改变业务链 |
| CAP-14 | PASS | 逻辑 20 分钟 100 用户轮；逻辑 65 分钟 150 用户轮 |
| CAP-15 | PASS | 短/长命令序列；真实 iOS↔FastAPI/PostgreSQL 双向闭环；短场、40 候选长场、149 候选 65 分钟链 |

CAP 核心 21 项：`regression/cap-core-summary.txt`。OwnerTruth 全量：`regression/ownertruth-full-summary.txt` 与 `regression/ownertruth-full-final.xcresult`。

## KEEP-01 至 KEEP-10

| KEEP | 状态 | 验证范围 |
|---|---|---|
| KEEP-01 | PASS | 短场两轮与补充进入候选、审核、正式记忆、重建读取 |
| KEEP-02 | PASS | 100/150 用户轮首中尾持续采集、分批整理、会后统一发布 |
| KEEP-03 | PASS | 跨批重复、补充、纠正、撤回及证据索引 |
| KEEP-04 | PASS | 纯问题无候选、事实+问题保留事实、助手回答不升级 |
| KEEP-05 | PASS | 接受/拒绝/更正、Binding/CAS/hash/revision、幂等与重建 |
| KEEP-06 | PASS | 42 项长回答、主动打断、恢复聆听、PCM、音频租约本地回归 |
| KEEP-07 | PASS | strict final、非 ASR 过滤、覆盖摘要、停止 partial |
| KEEP-08 | PASS | TTL/认证恢复、同账号续接、换账号隔离、旧 poll/迟到回调 |
| KEEP-09 | PASS | 未知写只读核实、原命令/Source/预算、失败 Run 不重复调用 Provider |
| KEEP-10 | PASS | 当前场状态与恢复坐标，不提前成功或串入历史场 |

真实 SDK 音频时序、真实 Provider、iPhone 短场/20 分钟/65 分钟均为 NOT_RUN。
