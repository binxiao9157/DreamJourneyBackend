# DreamJourney Live 采集中断修复 run-01：独立复核与剩余修改要求

日期：2026-09-21\
复核对象：Sol 的 `2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01` 交付。\
结论：**有实质进展，但不满足完整 LOCAL_PASS。当前应为 LOCAL_REVIEW_CHANGES_REQUIRED。**

这份复核是[原开发指导](2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导.md)的补充，不重新设计已通过的后端长场整理，不要求重做真机来定位已能本地证明的问题。先连续完成本地修复、验证和交付；真机、真实 Provider、发布仍由用户另行主动发起。

## 1. 已核实的成果与证据边界

- 当前关键源码与 Sol 指纹一致：`OwnerTruthContracts.swift`、`EchoViewController.swift`、`DialogEngineManager.swift`、`OwnerTruthContractsTests.swift`、后端两个 proposal 文件及 CAP-15 bridge 均匹配。
- 直接读取 `ownertruth-full-final3.xcresult`：547 项通过、0 失败、0 跳过。Simulator 与通用 iOS 目标最终日志均为 BUILD SUCCEEDED。后端受影响集合日志确为 119 项通过。
- 生产共享记忆适配器、观察与封存的初步分离、Chat 与 TTS 的来源分离、冲突后继续接收后续回合、有界本地写恢复等工作应保留。
- PostgreSQL 回放确实执行了真实 start/messages/end/ACK/admit、Worker、审核与正式记忆重建，没有直接播种已 ACK batch；原文及顺序检查也有价值。但它不等于真实响应驱动同一 iOS 客户端的完整闭环。
- 后端 `authority_epoch` 从 preparation 传给 Run/source binding 的修正方向正确，应保留并补对应测试。

**通过数量是真的，若干场景的断言却没有覆盖要求，部分还把错误行为写成了预期。因此不能据此宣布全部 CAP/KEEP 已完成。**

本次没有改产品源码，没有连接/操作 iPhone、调用真实模型、访问生产、处理历史、部署或 commit/push。除读取原 xcresult、日志和源码外，执行了一个抽取生产算法的独立 Swift 探针。它保留当前 Router/parser、upsert/flush、assistant stream 的算法原文，文件/账号依赖用内存薄壳替代；**用于证明算法缺陷，不冒充真实 Controller、真实磁盘或设备端到端测试**。

探针材料：[生成脚本](/Users/gaominge/Documents/liftora/outputs/2026-09-21-astra-live-capture-review/run-01/evidence/build_probe.py)、[实际执行源码](/Users/gaominge/Documents/liftora/outputs/2026-09-21-astra-live-capture-review/run-01/evidence/main.swift)、[结果](/Users/gaominge/Documents/liftora/outputs/2026-09-21-astra-live-capture-review/run-01/evidence/result.log)、[源码指纹](/Users/gaominge/Documents/liftora/outputs/2026-09-21-astra-live-capture-review/run-01/evidence/source-sha256.txt)。

## 2. R01 / P1：严格终稿与封存仍混淆，中间稿能进入发送队列

### 已证明的行为

| 合成输入，同一 question | 当前算法结果 | 正确结果 |
|---|---|---|
| final「杭州」→ final「苏州」→ ASREnded | 投递苏州，0 issue | 此正常对照应保持 |
| final「杭州」→ interim「未确认的中间稿」→ ASREnded | 投递中间稿，仍标 complete，0 issue | 保留可信 final；interim 不能替换可信终稿 |
| 只有 interim「仅中间稿」→ ASREnded | 分配 delivery，finality 仍为 interim | 保留 partial，不能进入完整正文投递 |

代码原因：

- [DialogEngineManager.swift:1405](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1405) 只保留最后一个观察，后到 interim 覆盖先前 final；[1523](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1523) 的 seal 对观察不检查可信 final，直接产生 observeAndSeal。
- [OwnerTruthContracts.swift:16836](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:16836) 对未封存项无条件换正文，但只有新 complete 才更新 finality，形成“新 interim 正文 + 旧 complete 标记”。
- [OwnerTruthContracts.swift:17350](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:17350) flush 只检查 isSealed；当 isSealed=true 时没有继续要求可信 complete。

### 必须修改

1. 分开 latest observation 与 latest trusted final，保留与正文绑定的 final 证据、来源、观察 ID 和入口序号；不能让正文和证据来自不同版本。
2. 已知严格 final 后的 interim/unknown 仅是观察，不能覆盖选中的 final。合法更新 final 仍可按来源/身份/序号选择最新完整快照。
3. ASREnded、下一 question、stop 只触发检查与封存；没有可信 final 就保留 partial。生产 flush 必须同时验证封存和可信终稿资格，不能只补上游一处 guard。
4. 未封存观察与选中可信版本在磁盘可恢复；不能仅修 Manager 的内存缓存。

### 必须红绿验证

将上述三个输入放入共享生产 raw dispatcher → Manager → 真实 Controller/Coordinator → 真实临时磁盘 → BackendClient 受控网络。同一断言先红后绿；验证 delivery 正文、final 证据、候选前 Source 正文及原命令不变。增加 invalid/unknown final 类型与冷启动后封存对照。只有 interim 停止继续使用已通过的 partial 表现，不得因本轮改动假报完整。

对应原 CAP-02/04、KEEP-07；原来的“仅 interim”测试未覆盖新 sealing 入口，不能当覆盖此反例。

## 3. R02 / P1：未绑定 QueryConfirmed 仍会提前封存语音

[DialogEngineManager.swift:1414](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1414) 将 `.queryConfirmed` 与 `.asrEnded` 放在同一封存分支。虽然不再直接使用 ack 正文，ack 仍改变语音生命周期。

探针输入：final「杭州」→ 没有登记文本请求的 QueryConfirmed → 合法修订 final「苏州」→ ASREnded。结果：杭州提前分配 delivery，苏州变成 immutableBodyConflict。对照不含 ack 时正确投递苏州。

[CAP-03 测试:2600](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:2600) 只验证 ack 正文没有覆盖 voice，却允许该 ack 完成封存，没有验证 ack 之后还有合法修订。这正是漏检位置。

必须按原指导实现：QueryConfirmed 只确认可绑定的本地 text request，不能封存、登记或更换 voice member，也不能把 boundarySeen 加给 voice。合法文本正文来自已登记请求。没有绑定的 ack 只做安全诊断。

必测三组：上述反例；相同 voice 流无 ack 对照；已登记 text request 的合法 ack。另测未知 ID/无 ID/旧账号/旧场 ack。修复不能删掉正常文字输入保存能力。该项与 R01 一起验证，但保留独立测试编号。

## 4. R03 / P1：跨 reply 交错仍会丢掉助手正文缓冲

[DialogProviderCanonicalAssistantStreamState:1870](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1870) 只有单个 replyID/text；[1891](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1891) 遇到任何不同 reply 就清空 text。

生产算法探针：r1 ChatResponse 有正文 → r2 ChatResponse 有正文 → r1 ChatEnded 无重复正文 → r2 ChatEnded 无重复正文。两次 ended 都返回 nil。此前已发出的 interim 留在 Outbox，无法形成两份完整助手回合。

这不是声称火山在本次现场一定出现该顺序；它是原 CAP-05 明确要求且当前本地未满足的交错输入。

必须按 engine generation/reply identity 维护独立、有限生命周期的记忆缓冲和终态；合法迟到结束不得清空另一个 reply。Chat/TTS 来源隔离继续保持，不修改播放、打断、音频租约或 answerAuthority。已完成 reply 的重发不得重新拼接或产生第二份正文。

必测上述交错、顺序正常、反向结束、重复终止、终止携带全文/不携带全文、旧 generation、新 reply 继续。用真实下游 Outbox 和结束完整性断言；不能只测两个字符串在组件里不同。CAP-05 当前单 reply 测试不充分。

## 5. R04 / P1：冲突虽记录，最终 end 没有拦截

- [EchoViewController.swift:2199](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2199) 的 persistCloseRequestIfReady 没有检查 canonicalConflictCount。
- [EchoViewController.swift:2595](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2595) 最终发送 end 也没有检查。
- [OwnerTruthContracts.swift:17209](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:17209) 的 unsealed 计数仅检查 sealed，不包含独立 issue；coverageGap 允许正常 delivery pipeline。

因此，已封存正文冲突后，后续十轮可以继续上传；但队列排空后仍能满足完整 end 条件。页面出现 coverageGap 不等于业务写被阻止。

[CAP-06:1423](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:1423) 把 policy 设为 false，只断言十一条正文及 close intent，没有“不得 end/ACK/admit”断言，无法证明完整性门禁。

必须：统一可完整关闭条件，包含 unresolved issues、严格 final/封存、停止 manifest 排空、磁盘漏采。Store 冻结 close sequence 与 Coordinator 最终发送均防止陈旧快照越过门禁；争议后的合法正文仍可持续落盘和按现有规则同步。不能通过删 issue、改旧命令或新造替代命令获得完整成功。

测试必须用真实 Controller 和允许网络的 fresh FeatureGate/BackendClient，owner 与 assistant 已封存/已曝光冲突分别测试：后续十轮保存、close intent 落盘、原 command/hash 不变、end/ACK/admit 为零。正常无冲突对照必须完成全部关闭链，防止一律阻断。

## 6. R05 / P1：停止 manifest 和磁盘溢出缺口没有持久化

### R05-A：停止意图仍在排空之后才保存

[EchoViewController.swift:2245](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2245) 要等 pendingPersistence、handoff、recovery 全归零，才 requestClose。[Envelope:16367](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:16367) 没有接受观察 manifest、停止入口水位或等价缺口账本。

用户停止时，已接受事件仍在主线程排队，若 App 随后退出，停止事实和未交接事件均无法从磁盘重建。报告所称“close intent 后崩溃重建”没有得到证明。

尤其 [CAP-09:2406](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:2406) 反而断言停止后 Store 为空、排空前 closeIntent 为 nil；它证明的是旧时序，不能作为原指导要求的绿测。

必须把停止请求、冻结入口身份/序号及已接受未排空项清单先形成持久记录，再排空。可用同一原子 Envelope 或有等价证明的持久 journal；仅保存最大 ordinal/内存 Set 不足。已接受项可以在 close intent 后按精确清单补交，水位外新项不能进入。磁盘完全不可写时诚实报告失败，不伪称意图已落盘。

必测：同 member 多份 final/boundary 延迟交接；持久 close intent/manifest 后销毁 Controller、Coordinator 和内存队列；重建后准确恢复或保留缺口；伪造同 ordinal 不同身份拒绝；重复 stop 不产生第二份关闭命令。测试不能强持有旧对象冒充恢复。

### R05-B：故障缓冲 overflow 只存在内存

[EchoViewController.swift:1819](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1819) 达到 128 项/4 MiB 后拒绝操作，只设内存 Bool；[1641](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1641) 的镜像也是内存。Envelope 没有拒收区间/overflow gap。

本进程会因 overflow 阻止 close intent；磁盘恢复后旧缓冲可以排空，但重建 Coordinator 会丢掉 overflow 标志。随后对原场再次 finish，可能将少了拒收内容的场次判为完整。这里不是“重启会自动 end”，而是**重建之后错误允许完整关闭**。

必须在可写恢复的第一批原子持久化中保存拒收区间或足以判不完整的 durable issue，并先于任何完整关闭判断。准确原事件不在了，缺口就不能清零。容量是故障缓冲限额，不是正常一小时会话条数限制。

必测：填满 → 下一项拒收 → 磁盘恢复 → 排空已有 buffer → 完全重建 → 再次 stop；原缺口仍在，不得完整 end。再加所有原事件确实补齐的对照。当前 CAP-08 仅测填满保有对象和单次失败补写，不能覆盖此链。

### R05-C：安全移交后释放

[canReleaseCaptureOwnership:1502](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1502) 对 unavailable/coverageGap/statusUnknown 一律 false；[Controller:13307](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:13307) 仅靠该条件释放 retained 对象。当前做到了“不提前释放”，没有完成“停止后持久安全移交再释放”。在 Controller 持续存活期间，失败场可能持续累积。

以明确的持久移交完成事实释放对象、取消观察器/计时器，不能把 unavailable 简单改回可立即释放。测试页面离开重进、多次失败场、安全移交和旧回调拒绝。只处理本场采集所有权，不借机重构历史任务全局 UI 仲裁。

## 7. R06 / P2：首错保护尚未覆盖主要冲突和真实配额竞争

实质冲突在 [OwnerTruthContracts.swift:16820](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:16820) 作为成功 snapshot 返回，不触发 Coordinator 的 recordFirstCaptureFailure。issue 仅保留 canonicalID/reason/time，没有新观察及其来源/ordinal；被覆盖的正文版本也不可还原。

[EchoViewController.swift:1542](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1542) 未向首错传 callbackOrdinal。[ConversationMemoryManager.swift:1015](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/ConversationMemoryManager.swift:1015) 仍按普通 session 配额淘汰整个文件，没有保护活动场首错。因此“同一文件写一万条后字段还在”不证明另一个音频 session 竞争配额时仍可定位首错。

必须把冲突首次出现接入安全首错，带受限来源/事件码/入口 ordinal/身份哈希和保存水位。冲突正文版本只进入受保护业务存储，绝不进入诊断日志。活动场首错在总配额中受保护，同时保持有界；不得借此清理历史真实文件。

补测：真实 owner/assistant conflict 首错、后续一万事件、不同音频 session 消耗配额、重建及新账号隔离；诊断磁盘故障不递归、不改变音频或业务请求。失败操作六阶段还须补真实 Controller/BackendClient 的关键组合，不能仅调用 Store API 证明生命周期也正确。

## 8. R07 / P1 验收缺口：CAP-15 仍非规定的同客户端闭环

### 已有证据的正确名称

“客户端输出命令和正文 → Python 适配回放 → 真实后端/PG/Worker/审核/正式记忆”。保留此证据，不否定其发现 authority_epoch 缺陷的价值。

缺口在于 [Swift:6184](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:6184) 先在 URLProtocol 桩中跑完客户端，再导出请求；[bridge:206](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/tools/cap15_client_bridge_postgres.py:206) 重写 productSession/thread/session/version，[268](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/tools/cap15_client_bridge_postgres.py:268) 又派生新 ACK/admit commandId。真实后端响应从未返回原 Controller/Outbox。

必须补：真实 Controller/Coordinator/FeatureGate/BackendClient 通过隔离本地 HTTP 或等价双向受控传输，与真实 Backend Runtime/PostgreSQL 对接。每次真实响应驱动原客户端生成下一条命令，Python 不代替客户端重绑身份/回执。仍只替换模型外部 HTTP，真实 parser/validator/Worker/事务/审核继续执行；不需要真机或真实 Provider。

### 内容验收也欠缺

[Swift:5912](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:5912) 的正文是泛化编号表达；[bridge:327](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/tools/cap15_client_bridge_postgres.py:327) 按 owner index 生成泛化 claim，facets 为空模板。156 条数量证明不了短场第二轮补充、多维事实、跨批纠正撤回。

将事实真值表直接输入本轮客户端适配器：短场后一轮补充前一轮；长场首中尾、多维事实、重复/补充/纠正/撤回、纯问题、助手排除。逐项核对同 Source 的候选正文、证据和正式 Memory/Version，重建后仍一致且无重复。不允许用另一份独立大 Source 的语义测试替代本链。保留此前 F-65 同源回归。

## 9. R08 / P2：受影响保持性与后端新增分支的补测

### 音频保持性

本轮 [audio-owner-lease.log](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-01/regression/audio-owner-lease.log:2) 仅运行 AudioOwnerLeaseModelTests 五项；后端 23 项不能代替 iOS 长答/打断/恢复聆听/PCM。CAP-05 的“不产生记忆事件”也不是播放保持性断言。

重跑已有相关类并明确逐项映射 KEEP-06：EchoTurnIntentReducerTests、EchoViewModelTurnAdmissionTests、DialogEngineAudiblePlaybackPolicyTests、DialogEngineDelegatedPlaybackStateTests、DialogRecorderResumeOutcomeTests、DialogTTSPlaybackTimingTests、DialogPCM16WaveEncoderTests，以及实际受影响的音频协调器/租约测试。真实声音与真实 SDK 时序保持 DEVICE_NOT_RUN。

### authority_epoch

保留 preparation 冻结 epoch 的修复。补实际开启 live_long_memory 分支的非零 epoch 传递、非法 bool/负数/非整数拒绝、失效 epoch 拒绝且无新增 Source/admission/effect、同命令幂等不重建 Run/重置预算。现有命名为 authority-epoch 的六项日志主要是未开启该分支的旧 proposal 测试；正常 epoch=0 的桥接不足以替代上述对照。

同时更正报告中的失败阶段：“已成功 admission 后阻断 Worker”不准确。红日志是在 admit_review_batch 构造 Run 时抛错，[persist_admission:384](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_interview_candidate_proposal.py:384) 尚在后方，应记录为 **admission 事务内部失败**。不可将其反推为 run04 现场唯一首因。

## 10. Sol 执行与交付要求

1. 保留当前有效修复和旧证据。新证据使用独立 run-02（若已存在则顺延），不覆盖 run-01，不 reset/clean。
2. 先将本复核中的输入序列与关闭反例写成正确行为断言，保存修前红测。抽取算法探针仅作定位参考，正式绿测必须回到共享生产适配器和真实 Controller/磁盘/BackendClient 装配。
3. 按 R01–R03 输入与封存、R04 完整性门禁、R05 持久停止/漏采与移交、R06 首错、R07 双向闭环、R08 保持性顺序连续完成。不是每做完一项就停下等用户继续。
4. 正常短场、100 用户回合逻辑20分钟、150 用户回合逻辑65分钟应成功；局部不可消解冲突/漏采则保留后续正文和停止事实但不得伪报完整。两类结果必须分别验收。
5. 将 CAP/KEEP 按子场景映射到测试名称、关键断言、xcresult/日志和当前源码指纹；不要只写“包含在547项里”。明确修前错、修后对，不能靠改变预期迎合现状。
6. 完成受影响回归、OwnerTruth、音频、必要后端和隔离 PG，以及 Simulator/通用 iOS 无签名构建。未修改模块不无故反复全量；存在未解决本地缺陷就继续处理。
7. 后续真机清单保留短场补充、普通20分钟约33轮、密集20分钟100轮、未来65分钟150轮、独立故障恢复与音频。当前 Sol 清单把普通20分钟与密集场合并了，应恢复原 DEV-02/03 的分开记录。

全部要求完成后才报告：

`LOCAL_PASS / PROVIDER_NOT_RUN / DEVICE_SHORT_NOT_RUN / DEVICE_20M_NOT_RUN / DEVICE_65M_NOT_RUN / DEPLOY_NOT_RUN / HISTORICAL_REPROCESS_NOT_RUN`

没有手机、未调用真实模型、未部署、历史现场首因仍无法唯一归因，都不是中断本地任务的理由。本轮不操作真实失败场、不重放未知写、不清理候选/历史、不提交推送，不引入车机专项、不放松证据/B7/预算门禁、不修改已通过的后端分批语义合同。受影响模块必须写入回归并实际验证。

## 11. 发给 Sol 的提示词

请继续修复当前 Live 采集中断任务。Astra 对 run-01 做了源码、指纹、xcresult 和生产算法探针复核，发现本地验收尚不完整；不要继续沿用全部 CAP/KEEP PASS。

先完整阅读本文件和原 2026-09-21 开发指导。保留当前已有工作及 run-01 证据，按本文件 R01–R08 完成剩余本地修改，重点修复中间稿误投递、QueryConfirmed 提前封存、跨 reply 缓冲丢失、冲突仍能 end，以及停止 manifest/overflow 缺口未持久化。每项用正确行为断言先红后绿；补真实响应驱动原客户端的 CAP-15 和短长场语义闭环，重跑受影响音频及后端新增分支测试。

本轮应连续完成本地修复、测试、构建和交付，不能只写计划或完成几个测试就停。真机、真实 Provider 和部署由我另行主动发起，不得检测或等待手机，更不能以未连手机中断本地开发。不访问生产、不处理历史失败场、不重放未知业务写、不 commit/push。已经通过的保存、候选整理、审核到正式记忆以及音频行为必须保持，确有影响的模块要加入回归并通过。

交付时按 R01–R08 和 CAP/KEEP 子场景逐项提供证据及剩余边界，完成全部本地要求后再标 LOCAL_PASS。原 run04 现场唯一首触发仍未确认，不能以本地探针替代真机结论。
