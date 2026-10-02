# DreamJourney Live 逐轮模拟 run-04 复核与验收收尾

日期：2026-09-21。

**结论：认可本轮正常短场/长场双向链和IDEMP-01产品修复；完整模拟验收尚未全部满足，不能把SIM/GATE矩阵整体标为PASS。剩余问题主要在验收装配与证据强度，本次没有证据表明Live正常保存再次被改坏。**

本文件只承接已有[逐轮模拟验收设计](2026-09-21-DreamJourney-Live逐轮模拟与待确认记忆全链验收补充设计.md)及[run-03收尾要求](../记忆系统/采集与会后保存/2026-09-21-Astra-Live采集中断修复-run03复核与局部收尾.md)，不增加产品功能，不重做音频、保存或语义整理架构。

## 1. 已核实通过，必须保留

- 原始xcresult：两次独立short和两次long分别为1/1 PASS；实际顺序为各自short后long。
- 最终OwnerTruth原始结果为 **563 total = 560 PASS + 3 SKIP，0 FAIL**。三个跳过是另有上述独立结果的外部装配入口，不视为未验证。Echo/音频/账号为51/51 PASS。
- 首次全量曾有 `testLiveRecoveryClassifiesCheckpointFailuresPerWorkflowWithoutRepairOrReplay` 的scope断言失败；单项及全量重跑通过。保留原失败，不仅凭重跑成功认定波动根因已修复，也不把它直接推断为本轮保存缺陷。
- 当前交付列出的5项文件及受保护源码集合、模拟器测试二进制摘要与现工作区一致。此结论不等于完整依赖集合和构建来源绑定已充分，见A项。
- `requestClose` 已改用 `effectiveManifest = envelope.closeManifest ?? manifest`。非空关闭、同参重复、默认空参重复、Store重建及未处理成员对照都有断言，原始红测与最终绿测成立。
- 两条正常双向链确实经过真实客户端、HTTP、后端/Worker和隔离PG。服务器Source分别220/220和300/300逐条角色、位置和正文相等。
- 真实 `OwnerTruthCandidateInboxViewController` 已读取本场候选，断言primaryValue、preview、来源非空及数量。已超过上一版仅等待pendingReview的证据强度。
- 短场1条候选，110用户轮长场4条候选，150用户轮长场17条候选；各自short+long经隔离审核、正式记忆及Store重建后分别为5条、18条正式记忆。
- 稳定重复确实位于不同生产整理unit：20场turn 7/189，65场turn 7/221；最终一个候选，合并后的support草案含两个turn索引。不是预先在fixture里消掉第二次输入。

报告入口：[Sol完整报告](../../outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/reports/2026-09-21-DreamJourney-Live逐轮模拟与全链验收本地报告.md)。后端91项通过按其执行日志核对，本轮未重跑全量或重型集成。

## 2. A：短场门禁还未完整绑定实际执行环境

对应原设计SIM-02、GATE-01；这是原要求未完成，不是新增标准。

### 已证实缺口

1. **两套配置来源可能不一致。** [Swift:5668](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:5668)优先读取BASE64环境配置，而[控制请求:6001](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:6001)重新读取磁盘配置。runner继承环境未移除BASE64，门禁payload只带缓存摘要。独立隔离探针确认：改变实际BASE64的baseURL/vaultID而保留缓存摘要，磁盘门禁仍返回204。这不证明本次四场使用了错误配置，但未来可绕过“同一配置先短后长”。
2. **依赖清单遗漏。** [server:63](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/tools/cap15_round_simulation_server.py:63)未包含后端 `app/main.py`、`postgres_store.py`、`owner_truth_live_memory_support.py` 和runner等实际依赖。变化不会使旧receipt失效。
3. **只散列测试二进制。** [runner:265](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/tools/run_round_simulation.py:265)未覆盖实际宿主 `DreamJourney.debug.dylib`，也没有本次源码→构建产物的关联manifest。临时宿主变化探针仍204。

已通过部分保留：门禁消费前会重新计算列内源码、磁盘配置和测试二进制摘要；正常204、二次409、受保护项变更412成立。

### 最小修改与验收

- 配置解析只有一个规范化结果，业务客户端与控制端共同使用它；可以在统一runner中明确只支持磁盘配置并清除/拒绝BASE64。实际值参与摘要，不信任配置自带摘要。
- 补全真实依赖清单，至少覆盖上面明确漏项以及候选读取/支持复核的实际实现。绑定宿主执行代码、测试包、实际xctestrun和build-for-testing来源manifest。
- 保留单次longAttemptID；负例在临时配置/manifest/产物副本运行，断言long尚未启动、业务请求不增加。不要改真实产品代码制造负例。

独立探针及指纹核对：[证据目录](/Users/gaominge/Documents/liftora/outputs/2026-09-21-astra-live-round-simulation-review/run-04/evidence/gate)。

## 3. B：逐轮账本还缺跨阶段身份，最终双证据需读回

对应原设计§6、SIM-05。

当前 [validate_source_turns:591](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/tools/cap15_round_simulation_server.py:591)是独立预期与**最终服务器Source**的全文/角色/数组顺序比对。它有效且应保留，但输出账本没有磁盘canonical身份、实际请求命令/消息身份及服务器确认关联；Swift每轮仍主要检查outbox序号。不能写成这些跨阶段身份已经全部逐条匹配。

最小补齐：从实际原始入口、磁盘读回、真实HTTP观测与服务器记录生成同一轮的安全关联账本；记录实际字段和阶段，不从预期数组反向填造。正常流不必每轮同步阻塞等服务器，但规定的排空点须完整对账。加一个正文和数量都相同、关联身份/归属错误的负例，证明关联校验独立有效。

另外，两个turn索引当前断言在support合并草案，候选与正式记忆只断言单条结果和部分内容。补最终候选/正式记忆的证据绑定读回，验证两处证据未在后续持久化或审核过程中丢失。若产品只暴露Source级sourceRefs，保留该展示合同，从已有持久化证据记录核对turn身份，不为了测试新增UI字段或要求重复Source引用。

## 4. C：受控模型输入和“客户端隐藏候选”负例需真实命中

对应原设计§5、§8、SIM-12。

### 模型输入

[受控适配器:505](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:505)按turn索引挑选预置答案并检查证据索引存在，尚未将本次请求的实际正文与独立场景原文核对。[support分支:352](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:352)统一返回supported及空遗漏列表。

这可以验证受控成功路径，但Source正文正确不代表Source→模型请求期间没有截断或替换。**未证明生产因此出错，缺的是验收防止假通过的能力。**

最小补齐：每次组织、support、关系请求按其生产批次/责任范围验证实际正文、证据身份与预期事实；不能错误要求每批包含整场。维持独立事实真值表。注入“索引不变、请求正文被替换/缺失”的错误，必须在受控Provider边界被拒绝，而不是继续返回正确答案并标PASS。

### 客户端负例

[hiddenClientCandidate:1192](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-04/tools/cap15_round_simulation_server.py:1192)只把Python候选数组切掉一条再交给Python校验器，没有经过iOS解析、Source过滤或Controller渲染。这只能命名为后端候选数量校验负例。

补一个真实客户端响应边界用例：数据库仍有完整候选、任务状态仍完成，只在本地受控候选GET响应缺一条，真实CandidateInbox Controller的同场数量/正文断言必须失败；恢复正常响应后同断言通过。不能直接改state.items模拟已经发现问题，也不能碰生产候选。

Astra本轮已用同一源码验证器补做“条数不变、中段/尾段正文替换”轻量负例，均正确拦截。该能力成立，可直接保留并纳入runner，无需重写正文校验：[结果](/Users/gaominge/Documents/liftora/outputs/2026-09-21-astra-live-round-simulation-review/run-04/evidence/same-count-body-negative-probe.json)。原交付删除条目的两个负例只是先命中数量检查，不应把它们描述成这份新增证据。

## 5. D：时间与故障证据不能把独立专项拼成双向PG验证

对应SIM-03/04、SIM-08/09/11。

- 双向场 [Swift:5825](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:5825)把1200/3900秒分配给capturedAt；FeatureGate使用真实Date+3600，Coordinator没有接入该时钟，refresh直接true。因此本次证明的是110/150用户轮保存和事件时间表，尚未证明**同一双向链**经过20/65分钟的过期/调度。
- 已有独立TTL/deny专项确实使用真实FeatureGate/BackendClient和fakeNow，服务端为URLProtocol。这些PASS保留，不说“没有TTL测试”。但执行清单中“本轮仅使用生产共用时钟/调度注入验证逻辑时长”不应指代未注入时钟的PG场。
- SIM-08当前主映射为ClientSpy/URLProtocol的认证与未知写专项。真实HTTP/PG成功场没有“业务处理前明确拒绝”和“服务端已写入但响应丢失”两种注入，尚不满足该项原文。
- 后端91项中截断、非法schema、证据校验和有限预算有有效测试；清单未提供设计点名的模型HTTP 429与超时注入，不能把ConnectError等同于全部这些情形。

### 最小收尾

1. 让至少所标逻辑20/65场的生产时钟/调度实际推进到目标终点，覆盖期望的策略/凭证边界；从观测输出实际推进时长，而不是直接抄配置。复用已有注入点，不以等待物理时长代替，也不增加固定sleep。独立专项仍独立列证据，不要求所有故障与两个时长做笛卡尔组合。
2. 在隔离真实HTTP/PG链各注入一次明确的处理前拒绝、一次保存后丢响应。核对服务端实际效果、原命令与预算、只读恢复以及候选/正式记忆的正确结果；未知写未查明时允许按合同保持待核实，不能为得到候选而盲重放。
3. 补受控模型HTTP 429和超时的typed阶段、有限预算/恢复断言，遵循现有分类，不扩大预算或改Provider合同。修正SIM-09证据映射，明确哪些来自真实Gate/BackendClient，哪些仅ClientSpy；覆盖已充分者直接引用有效结果。

## 6. 有限收尾顺序及交付

1. 保留run-04证据，先修A门禁，再补B/C验收断言和D的缺失装配。优先修改测试及工具，产品只有已证明缺陷才局部修复。
2. 最终版本依次运行 short→20场、另一个short→65场，分别生成新单次凭证。必要故障场用较短输入即可；不用重复做物理20/65分钟。
3. 受影响回归沿用原CAP/KEEP及音频/账号要求。无相关变更的有效结果可准确引用，不为了堆测试次数重复全量；源码/构建变化影响原证据时重新验证。
4. 逐项明确正常链、TTL专项、真实HTTP故障链、模型HTTP错误、客户端负例的证据类型。保留首轮checkpoint失败记录并记录重跑结果，不能抹去，也不在无新证据时扩展为另一次产品重写。
5. A–D未完成前，整体按 `NORMAL_CHAIN_PASS / LOCAL_ACCEPTANCE_INCOMPLETE` 描述；这些是交付状态，不新增App产品状态。已有正常保存链和局部缺陷修复继续保留PASS。
6. 不部署、不调用真实Provider、不连接或等待手机、不访问生产、不清理或重放历史、不commit/push。用户主动发起后才开始外部/真机阶段，未连接手机绝不是停止本地收尾的原因。

## 7. 可直接发给Sol的提示词

请按这份run-04独立复核继续局部收尾。Astra已确认本次两条正常短长链、真实iOS候选列表、跨批单候选及IDEMP-01修复有效，不需要重做保存系统。当前未完成的是A–D验收要求：统一实际配置与门禁并绑定完整源码/宿主构建；补逐轮跨阶段身份及最终证据读回；受控模型验证真实请求正文并补真实客户端隐藏候选负例；将逻辑时钟及两种HTTP/PG失败注入、429/超时落到对应真实本地装配。严格按文档复用已有有效专项，不把ClientSpy或配置中的时长写成双向链已验证。

完成后在同一最终版本上各自先short，再20/65逻辑场，交付精确矩阵、原始证据、指纹与受影响回归。既有产品成果保持，不因验收工具缺口就扩大修改产品。全程本地，持续完成，不检测或等待手机；真实模型、部署和真机由我主动发起。

## 8. 本轮Astra操作范围

读取本地代码、报告、日志和原始xcresult；核对当前文件/构建指纹；运行独立临时门禁探针及等条数正文负例。未重新运行重型集成、未修改产品代码、未连接手机、数据库、真实模型或生产。
