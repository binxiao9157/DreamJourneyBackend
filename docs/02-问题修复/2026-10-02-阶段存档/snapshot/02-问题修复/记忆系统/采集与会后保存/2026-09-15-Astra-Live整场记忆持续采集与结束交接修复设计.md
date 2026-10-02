# Live 整场记忆持续采集与结束交接修复设计

日期：2026-09-15。交付对象：Sol。用途：指导问题 1 的局部开发。

关联文档：[Live 长回答朗读中断修复设计](../../Live语音与开麦/2026-09-15-Astra-Live长回答朗读中断修复设计.md)。两项可以由 Sol 在同一个开发任务中完成，但须分开修改块、测试结果和发布结论；共享的 transcript 接口先统一，禁止两处重复采集。本文是正式开发交接，outputs 内审计稿与探针是证据。

问题 1 包含三个缺口：整场中途停止采集；用户最终关麦后没有结束整理交接；旧场成功提示与本场保存状态混淆。08:10 用户仍在聊，此时尚未生成候选本身不是新故障。到 08:22 用户结束后，连已保存前缀也没有进入整理，才是必须修复的关闭交接缺陷。

**用户最新范围调整：本阶段优先修复手机端原生 Live 交互及关麦后的记忆交接。普通蓝牙按系统音频外设处理，不涉及 CarPlay、车型适配或原车机专项开发。原车机复测不属于本阶段必需验收，未进行该项不阻断手机修复的开发与发布；真实手机和 SDK 的功能验收仍须完成。本文这项范围调整覆盖旧审计稿及旧提示词中的原车机必需门槛。**

本轮状态：调查与设计完成；7 项已有真实 XCTest 已运行通过；未修改 iOS/后端业务代码。新方案尚未实现，新方案绿测、真机复测均为 `NOT_RUN`。现有现场缺陷维持 `FAIL`。

## 1. 产品边界和已经确认的事实

用户确认从开始到约 08:22 关麦是一场持续 Live，不能按每个问答、每次 SDK 连接或 08:10:42 的最后入库时间拆成多场。08:06:15 只是现存记录的起点，不等同用户实际开始秒数。

主调查经过用户明确授权后，只读核实了该精确 productSession：

- 服务器 `live / active / open`，seq1–16 连续，最后成功保存 08:10:42；7 条用户文本、9 条助手文本是片段计数。
- 手机捕获的 `echoTextInput` 授权到期时间为 08:10:47；08:11:42 已本地落盘 seq17，但服务器没有该条。
- 手机本地近期文本持续到 08:22:31；seq17 后的完整长对话没有进入该 outbox；不能保证完整找回。
- 用户最后关麦后，原 outbox 没有 close watermark；服务器无成功 end、review batch、admission、Source、抽取 job、extraction、candidate。
- 手机快照有 `purpose=request / allowed=false / reason=capturedPolicyExpired`；快照时间不是首次拒绝时刻。
- 现存两条 `pendingReview` follow-up 来自昨天；它们不能证明今早这场成功。当时具体哪条回调渲染了提示没有事件证据。

证据总入口：

- [现场调查记录](../../../outputs/2026-09-15-live-long-session-investigation/2026-09-15-Live长对话与朗读中断-调查记录.md)
- [限定单场的服务器结果](/Users/gaominge/Documents/liftora/outputs/2026-09-15-live-long-session-investigation/server-morning-chain.txt)
- [手机脱敏字段摘要](/Users/gaominge/Documents/liftora/outputs/2026-09-15-live-long-session-investigation/device-evidence-summary.json)
- [手机原始策略诊断](/Users/gaominge/Documents/liftora/outputs/2026-09-15-live-long-session-investigation/device/DreamJourney.EchoRuntimeDiagnosticsStore.snapshots.v2.json)

高可信因果为：捕获的入口策略过期 → 记忆 append 路径拒绝 → capture 被当作不可恢复终态释放 → Live 继续但后续表达未进 outbox → 最后关麦无该场执行器负责 close。缺失当时的专属请求事件，所以首次拒绝究竟发生在发送前哪一层不能标为已确认。服务器已排除本场“候选生成后未显示”以及“DeepSeek 抽取失败”。

## 2. 当前基线及局部改动位置

iOS 根目录：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`；HEAD `11d0d0051b9be3cce57822dd059472d1e2536866`。后端根目录：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`；HEAD `a25b993922fc90dde1e689d19e51becb68fccdbf`。均存在既有未提交修改，禁止整文件覆盖或 reset/clean。

当前 iOS 的 OwnerTruthContracts、BackendClient、EchoViewController、测试文件 SHA 与昨晚 B7/B8 最终报告一致。指纹保存在 [current-source-sha256.txt](/Users/gaominge/Documents/liftora/outputs/2026-09-15-live-fix-design-analysis/memory/tests/current-source-sha256.txt)。手机 build 1 无法唯一标识当前源码；不能称昨晚 B8 已在真机生效。

| 文件/函数 | 当前缺口 | 最小修改职责 |
| --- | --- | --- |
| `EchoViewController.swift:1219` `EchoLiveMemoryCaptureCoordinator` | `state == live` 同时决定接受文本与网络可用；请求失败后丢掉采集生命周期 | 拆分本地 capture、delivery、completion 三种状态，持续持有同场记录所有者 |
| `EchoViewController.swift:1382` `appendOwnerTurn` / `appendAssistantTurn` | 受传输 state 影响是否落盘 | 只受本地采集同意、原账号作用域、关闭水位、存储能力影响 |
| `EchoViewController.swift:1501` `persistAndEnqueue` | 网络错误可终止后续本地入队 | 持久化先行；落盘成功再通知派送器；队列串行分配 sequence |
| `EchoViewController.swift:1415,1548,11866` `finish/persistCloseRequestIfReady/finishLiveMemoryCaptureIfNeeded` | close 依赖 state=live；先清空 active 指针；无即时可靠关闭状态 | 先原子记录用户 close 意图，排空已登记事件后再 seal 水位，确认后转交 retained completion owner |
| `EchoViewController.swift:1633,11727` | failed/unavailable 被当作整场终态并释放 | delivery 暂停不释放 capture；终态定义只覆盖业务完成或显式冻结 |
| `OwnerTruthContracts.swift:13935,14205,14440` | append 发前和回执阶段复用已过期 route，无续期 | 为持续 Live delivery 增加有界授权准备与明确 transport outcome；原回执处理不能被单纯策略到期抹成未发送 |
| `DreamJourneyBackendClient.swift:12414,11715` | transport 复用 `.echoTextInput` route，并提前失败 | 接受新鲜、精确作用域的 request authority；提供 append 的真实传输曝光证据与只读确认适配 |
| `FeatureGateService` `freshServerPolicyManagedRequestDecision` (`BackendClient.swift:613`) | 已有新鲜 request 获取能力，Live 未使用 | 复用此接口；不要全局改变旧 route revalidation 合同 |
| `OwnerTruthContracts.swift:14657` `OwnerTruthInterviewLiveTurnOutboxStore` | V1 无发送阶段、server binding；只剩 outbox 的恢复只能阻断 | 新增兼容版本、绑定和逐项传输记录、close 标记、可辨别扫描结果 |
| `EchoViewController.swift:3199,3369` RecoveryService | outbox-only 只产生 blockedRecords，页面只日志输出 | 只读发现并展示未关闭/未送达的原场任务；显式恢复另走新意图执行器 |
| `EchoViewController.swift:11755,11943,12080` 状态渲染 | 多个任务竞争同一文案；Live 内错误隐藏；idle 隐藏保存状态 | 按 productSession 选择 presentation owner，显示本场优先和独立持久化状态 |

## 3. 必须保持的安全不变量

1. 一次用户开启 Live 建立一个 productSession；网络重连、策略续期、页内文本切换不更换该标识。用户最后关闭才冻结这一场。
2. 用户已授权采集的每个有效 canonical owner/assistant complete，先写本场磁盘队列，再具备网络发送资格；partial/未定稿材料持久化在 V2 的独立材料区，暂不分配投递 sequence，也不调用现有完整消息 append。不得把 partial 伪装为完整回答或只保留在最后 20 条预览缓存中。
3. seq、messageID、commandID、capturedAt、role、content hash 一经持久化保持稳定。关闭水位单调；不跨场补写，不重新打包旧文本成新 Source。
4. 单纯网络失败或授权快照过期不能把已保存内容删除，也不能让仍授权的同场后续表达静默掉落。不能使用无限内存队列替代磁盘。
5. 策略、账号、authority、CAS、Binding 等拒绝仍然有效。过期时必须拿到新授权；不得延长旧授权时间、重写 expiresAt 或把 deny 改成 allow。
6. 不确定的业务写先只读确认。`pending` 只是本地未接受送达回执，绝不是“肯定未发送”。HTTP 200 也不是正文/hash/序号绑定成功。
7. B6 冷启动/页面恢复仍然零业务写、零自动开麦、零自动新建 session。用户显式恢复同场是新的受权操作，不是假装成后台 recovery。
8. 严格区分用户表达、助手上下文。候选和正式记忆继续由后端现有链生成与审核；不在手机直接创建候选。

## 4. 本地采集与派送解耦

保留现有 coordinator，新增私有状态和值对象即可，不要求拆出通用任务系统。推荐以下三个独立维度，而非一个巨大枚举承载所有组合：

- `capturePhase`: `open / freezing / locallyClosed / frozen(reason)`。
- `deliveryPhase`: `idle / preparingAuthority / sending(sequence) / checkingUnknown(sequence) / paused(reason) / caughtUp`。
- `completionPhase`: 保持 B8 checkpoint 的 endPrepared→ended→acknowledgementPrepared→acknowledged→admissionPrepared→admitted，以及后续 queued/organizing/reviewReady/noCandidates/failed。

`acceptsTurns` 只用于登记新的输入槽位，检查 `capturePhase == open`、同场采集同意仍有效、原 AccountLease 的 runtime/UI 作用域合法，以及存储未失败。它不检查 delivery 是否 paused，也不因 upload policy 的单纯过期返回 false。

建议内部接口（命名可按项目惯例调整，职责不得合并）：

- `appendCapturedTurn(_ event: NativeLiveCanonicalTranscriptEvent)`：核验原槽位身份，将材料更新或权威最终化交给 durable staging；freezing 中仅走 `finalizeRegisteredTurn`。随后检查有序槽位，仅将连续 complete 前缀原子 seal 到 delivery queue，分配稳定 clientSequence/messageID/commandID，再唤醒 dispatcher；其余槽位不获得网络发送资格。
- `requestDelivery(reason:)`：同场 single-flight；仅派送最小未确认 sequence。网络恢复、当前授权刷新和当前会话用户操作可唤醒；每个新 ASR final 不得制造并发刷新风暴。
- `requestClose(reason:.userStop)`：即使 delivery 暂停，也必须接受关闭动作并落盘。不得依赖 capture 的旧 `.live` 状态。
- `suspendDelivery(reason:)`：保留 capture、磁盘队列、全部 command identity。不得清空 `liveMemoryCaptureCoordinator`。
- `freezeCapture(reason:)`：发生真正权限边界变化或无法保存时执行；冻结已接受前缀，禁止接收后续新 turn，向 UI 明确说明。

首次合法 question/reply 输入登记时分配稳定 `captureOrdinal`，先写有序 staging 槽位；它不同于网络 clientSequence。`finalizeRegisteredTurn` 专门接收已登记槽位的权威最终化：open 或 freezing 期间均可在原边界内完成，不能再走只接受 open 的新登记入口。只有 staging 形成连续 complete 前缀时才 seal 并分配投递 sequence，后面完成的助手文本不能越过前面的未定稿用户槽位。若 closeRequested 后、seal 前崩溃，重启仅能根据已落盘槽位标明缺口并只读核实，不能宣称 provider 已排空。

assistant final 的采集仍使用原角色与序号，不以 UILabel 文本页数判断完整回答；不在本问题顺带改音频播放、回声或用户打断机制。

### 4.1 可以继续本地采集的条件

必须已有本场用户主动开启 Live 的明确采集同意，且当前仍为原 subject/vault/authority 的活跃会话；该同意没有被撤销。建议将同意的作用域、创建时间、关闭状态作为 outbox V2 的 session descriptor，不存凭据。

| 情况 | 接收新的本地表达 | 网络行为 |
| --- | --- | --- |
| 远端请求授权快照 `expiredPolicyCache/capturedPolicyExpired`，尚无真实否定授权 | 允许在原未关闭场内保存为“本机已保存，待同步”，前提为已取得的同场本地采集同意仍有效 | 暂停业务写，只读刷新并取得新 request decision |
| 网络断开、timeout、429、5xx，或刷新暂时失败 | 同上；有磁盘容量上限和清晰提示 | 有界等待/退避；不丢表达，不重复写未知命令 |
| access token 续期中，原账号作用域仍由 AccountLeaseRuntime 确认有效 | 同上 | 等待既有认证恢复，获得当前有效凭据后重新核对身份 |
| 同账号凭据轮换，generation/authority 逻辑作用域不变 | 可按已验证的 account runtime 继续 | 不复用旧请求的凭据；当前执行器重新获取合法请求上下文 |

“本地采集同意”和“远端上传许可”必须在代码中分离表达。不能直接把过期的远端 FeatureDecision 伪装成有效本地授权。若项目当前合同把某种期限同时定义为本地采集截止，需按真实截止冻结，不能用本设计绕开。

### 4.2 必须停止接收新表达的条件

- 用户关麦/撤回本场采集同意：立即关闭本场输入门；之后延迟到达的旧回调按捕获 token 和关闭边界裁定，不能成为新 turn。
- 退出登录、切换 subject/vault、authority epoch 改变、账号被禁用或 scope 不能安全验证：冻结原场，旧回调不能写新账号目录。原文件原地保留，不搬到新作用域。
- 成功获取的最新权威策略明确拒绝相关采集能力或撤销用户资格：停止接受新表达，不把其归类为临时过期；已保存前缀保留，上传也阻断。
- 系统录音权限撤销、App 的本场能力真实被关闭：停止采集并明确提示。
- 磁盘写失败、保护态导致无法读写、完整性失败或达到本场容量限制：停止接受新的可承诺保存的表达；不能在语音继续时宣称仍在保存。保留最后一个已验证持久化前缀并显示“无法继续保存，本次已暂停”。

若停止的是本场记忆采集而语音仍可用，不能静默让用户继续讲而漏存。默认暂停本场 Live、保留原会话任务，并说明权限或保存问题。该暂停只调用现有安全停止入口，不重做音频引擎。安全模式/危机流程保持原优先级。

## 5. 有界策略续期与新鲜授权

为 Live dispatcher 增加窄的 `prepareLiveDeliveryAuthorization(originalScope:deadline:completion:)`。优先复用现有策略刷新和 fresh request API，不改变 FeatureGateEvaluator 的全局拒绝语义。

1. 先按当前原场 AccountLease 检查身份和本地意图有效期。
2. 使用 `freshServerPolicyManagedRequestDecision(.echoTextInput)` 从当前缓存直接捕获 request，不复用入口 route。
3. 若是明确 enabled 且未到期，返回该具体 FeatureDecision；若缓存缺失/过期/版本陈旧，允许一次只读 `refreshPolicy(for:.echoTextInput)`。
4. 刷新结果必须匹配同 subject/vault/authority、预期 app build、owner audience、authenticatedOwner cohort；之后再次使用 fresh request capture，并重新核对原场和 deadline。
5. 最新明确 deny 立即冻结或阻断，不能“刷新直到允许”。只读刷新失败仅暂停派送。
6. 每个同场同账号一次只读刷新 flight，默认 15 秒预算；超时后迟到回调不能派送。自动重试设退避并限制一次连续故障 episode 的次数，建议 3 次；额度耗尽后等明确网络/前台/用户操作触发新 episode，不能由新表达逐条重置预算。
7. 可选择在 expiresAt 前 30 秒唤醒 dispatcher 提前刷新，但这只优化体验，正确性必须依赖每次发前检查；App 挂起或计时器未触发也必须可靠。

派送路径必须贯穿新鲜 FeatureDecision，不能在下层 `ownerTruthInterviewNaturalInputTransport()` 又重新拿旧 route 覆盖它。下层仍核对传入授权的当前 scope/版本/期限；拒绝后返回可辨认原因。

回执处理中，单纯 request policy 到期不能把一个已验证服务端 committed 回执抹掉。必须核验原命令、账号、session、sequence、正文/hash 和服务端合同，再持久化送达证据；真实账号/authority 失效时不更新当前 UI、不转交新账号，而保留原场待只读确认状态。

## 6. 业务写结果与未知状态

将 B8 的 typed transport outcome 最小扩展到 Live start/append 的同场绑定；end/ack/admit 复用现有 B8 实现，不可复制一套矛盾分类。初次 start 可能已创建 session 而回执丢失，也必须按原 command 和 productSession 只读恢复。

| durable delivery state | 证据 | 允许行为 |
| --- | --- | --- |
| `prepared/notExposed` | 原 command 已落盘；有持久化状态证明真实 task 尚未允许 resume | 新鲜授权和原意图仍有效时首次发送同一 command |
| `outcomeUnknown` | task 已曝光但无绑定有效终态；或 V1 缺乏发送证据 | 只读确认原 message/command/sequence/hash；禁止以 POST 查询 |
| `serverRejected` | 明确 HTTP/业务拒绝并按合同证明未提交 | 按原因分类；授权拒绝不自动写；冲突先读；不得统一当 notSent |
| `committed` | 服务端原命令回执/只读确认绑定所有必要字段 | 原子记录已确认水位，可清理 pending 正文；保留耐久 canonical 去重元数据，继续下一 sequence |
| `bindingMismatch/conflict` | 同 seq 不同 message/hash 或 session/product 不匹配 | 冻结派送并报告冲突，不自动覆盖/跳号 |

先持久化 `mayExpose`（含原 command、payload hash、attemptID），再允许真实 task resume。若写盘失败则不能发送；若 crash 发生在落盘与 resume 之间，恢复时保守视为 outcomeUnknown。不能把重启后缺少回调或只有 prepared 字样当作“从未发送”。

后端读取缺口已由本轮真实 probe 确认：current 在成功 end 后返回 null；exact repository 仍能读到 ended 和正确连续/close 水位；公开 state wire 不含 delivery/command 字段。因此本次方案需要一项后端增量：owner-scoped、最小数据的 `GET live-delivery-status`，按原 productSession/thread/session 与有界 sequence/command 查询生命周期、水位及逐项 committed/missing/conflict，正文不返回。精确字段与受理版本见 6.1。

只读结果必须绑定原 command/message/sequence/payload hash 与原会话。missing 只表示本次读未观察到提交，不能解除 outcomeUnknown。原执行器、网络恢复、策略刷新、前台与冷启动均不得因此自动重发。只有新的明确用户恢复动作，才可进入经过独立验证的 start/append 重试分支；此前必须完成精确只读核实、原命令指纹与服务端并发幂等/迟到提交竞争验证。该许可不适用于未知 end/ack/admit，后者继续 B8 只读核实。

对新实现中能证明 notExposed 的待送项，新鲜授权与仍有效意图允许首次发送；本案旧 V1 seq17 无法证明曝光状态，必须按 unknown 只读核实，再按新用户恢复意图处理。本设计没有授权现在补发本案数据。新意图不改写原 commandID/messageID/capturedAt/role。

### 6.1 必须新增的最小后端只读合同

下面是**待实现接口**，目前不存在。现有 `GET .../current` 只返回 active/open；结束后返回 null，不能当作旧场不存在。QA `GET .../{sessionId}/state` 不提供逐条投递绑定，也不能拿其产品展示接口代替恢复 API。

建议接口：`GET /v2/vaults/{vaultId}/interview-sessions/{sessionId}/live-delivery-status?productSessionId={originalProductSessionId}&fromClientSequence=1&limit=64`。

- 以当前 owner 身份、vault 权限、当前 authority 和合法只读 FeatureGate 受理；只查已知精确原场，不开放按用户遍历历史。原 `threadId/sessionId/productSessionId` 必须一致；允许读取该原场 active/paused/ended 的投递元数据，实际写权限另判。App account generation 与 vault authority epoch 使用现有各自类型，不能相互替代。
- schemaVersion=`owner-truth-live-delivery-status-v1`；响应包括绑定、lifecycle/boundary、session/thread versions、continuousClientSequence、nullable closeRequestedClientSequence、observedAt、查询窗口及 nextFromClientSequence。
- 每个已提交项仅返回 `clientSequenceNumber/messageId/commandIdHash/role/kind/capturedAt/contentHash`。`contentHash` 必须使用后端现有 conversation content 的规范化算法（schemaVersion/text/captureMode/capturedAt；role/kind 在外层另行核验），不能拿客户端纯文本 hash 冒充；Swift/Python 必须有同一组合成 golden vectors。commandIdHash 固定为原 commandID UTF-8 的 SHA-256，独立于 contentHash；只用于绑定，不是授权票据。
- 窗口上限 64，起点为正整数；服务端返回该固定窗口的已提交项及 missing 序号。客户端与本地稳定项对照得 committedMatch / missingAtObservation / bindingConflict；不要把未查询的下一页算 missing。读取需用同一只读快照获取水位与窗口，避免拼成不一致结果；禁止返回正文、候选、音频或完整业务 receipt payload。
- 可返回已存在的 start/end 成功 receipt 的最小 `operation/commandIdHash/receiptId/绑定/resultState`，供原命令结果核实；end/ack/admit 的后续批次发现仍复用 B6/B8 精确读取，不新增写入型“恢复查询”。读取消息与命令绑定时只能关联成功 command receipt，不能猜测 messageID 就是 commandID。
- `Cache-Control: no-store`。未授权、跨 owner、错误 product/session 绑定按既有隐私合同拒绝，不能泄露其他场是否存在。`notFound` 仅表示在当前合法范围内未读到，**不表示原请求从未发出**，更不授权新 start。
- GET 不产生 command receipt、Source、job、候选，不更新生命周期；数据读取不能调用现有写路径 `_receipt_by_command` 的 `FOR UPDATE`。实现独立无锁只读 repository 方法，并测零业务写。现有消息、receipt、session 表通常足够；没有实际 schema 缺口不新建数据库迁移。

处理顺序：committedMatch → 本地记录原项确认；conflict → 冻结；missing → 保留“此刻未观察到”状态。对于 outcomeUnknown，只有新的明确用户恢复意图、当前写授权、原不可变命令全部满足，且该 start/append 分支已通过并发幂等门禁，才可在完成该读取后同 ID 幂等重试；不能由仍在活动的原执行器自动发起，不适用于未知 end/ack/admit。并发原请求仍可能在读后提交，因此服务器原幂等约束不可削弱；不能重新生成 messageID/commandID/sequence 来绕过冲突。

命令指纹遵守真实 domain 合同：`AppendInterviewMessageCommand.write_record` 的 append idempotency_payload 不含 optimistic expectedVersion；`_deduplicated_result` 对 append 专门比较 messageID/contentHash/author/kind/clientSequence。因此仅 append 可按该现有合同更新当前 attempt 的 CAS 版本而复用稳定 ID，原文本/捕获时间/角色/作用域与全部纳入语义指纹的字段不可变。source schema、policy_version 等共同身份若变更则不能擅自重试；冲突保持可见。必须新增隔离 PostgreSQL 下“GET missing 后原请求迟到提交，与恢复重试竞争”的证明。start/end/ack/admit 不能继承 append 的 CAS 例外，不改变其原命令指纹。

start 的线程/会话/命令/product 标识必须在第一次网络曝光前写盘。新 V2 在 start 回执丢失时按已保存精确 ID 查询；旧 V1 缺绑定只能受限发现唯一匹配，无法确定时停在需要处理状态，不能根据 current=null 自动开另一场。

后端落点：`app/main.py` 增加受保护 GET 与 response builder；`app/services/owner_truth_conversation.py` 增加 service/repository 只读方法；`app/domain/owner_truth/conversation.py` 添加最小读取结果类型。测试放既有 conversation / interview_input_api 套件。现有 append/end 唯一约束、CAS、owner/authority 与 product 隔离保持。

客户端落点：BackendClient 新增 typed GET、OwnerTruthContracts 的读取值与比对器、原 Live dispatcher 消费结果。API 先发布兼容只读接口，iOS 再启用新恢复路径。接口缺失/不支持时明确显示“同步状态暂时无法核实”；不能 fallback 到业务 POST。测试至少覆盖 ended 后可查、跨 owner/product 拒绝、逐页缺号、同号异 hash、已提交但响应丢失、查询与提交竞态、零写与 HTTP 无正文。

## 7. 用户 close：先落盘，后排队收尾

关麦必须只关闭这一场、立即给用户确定反馈。保持现有同场冻结与完整 watermark，不在 5 分钟策略到期时擅自 end。

1. 用户 stop 事件到达即把 `capturePhase` 改为 `freezing`，停止接受新的输入意图，保留 coordinator 引用；先落盘 `closeRequestedAt/closeReason` 与待排空状态，此时最终 watermark 可以尚未知。不要等网络或最终 ASR 回调后才记录用户关麦。
2. 有界排空 stop 前已接受事件及已经登记的进行中 provider turn 的权威最终回调；之后使用同一 persistenceQueue 的 barrier 等待这些 turn 持久化，得到最终 sequence=N。若 SDK 没有可靠 final/drain 合同，该部分是待确认设计约束，不可用任意超时冒充完整；截止后缺少 final 必须保存明确缺口状态。
3. 原子 seal `closeRequestedLastClientSequenceNumber=N`、`capturePhase=locallyClosed`、完整性状态及现有 server binding。只有确认写成且无缺口才显示“本次对话已保存在本机，正在同步/等待恢复”；有缺口须说明仅保存已收到的内容。写入未完成只能显示“正在保存”；失败要明示，不能释放最后持有者。
4. 引用所有权转交给同场 retained completion owner；active capture 可清空，但磁盘已能完整恢复。用户开新 Live 必须得到新 productSession，旧 owner 不得接收新 turn。
5. 无 gap 时 dispatcher 收敛 seq1…N，服务端确认连续水位恰为 N、没有未知项，再按原正常关闭执行器发送一次 end。有 gap 时默认不 end/ack/admit，等待用户明确选择仅整理首个 gap 之前的完整前缀；不得跳过缺口拼接后续已完成文本。
6. 后续 end/ack/admit 继续用 B8 逐阶段准备、传输结果、receipt checkpoint 和只读状态观察。未知阶段不能顺势补新写。
7. 没有任何 owner complete 的真正空场也要有明确结果；本地无 start 则本地空场结束，已有空服务器场按既有合法 end 合同处理。不能为满足正整数 lastClientSequence 构造 sequence=0 消息、虚假文本或伪 Source。若有未定稿 owner 材料，不得判为空场，须标注未完整保存。

迟到 ASR final：只允许属于 stop 前已接受的 provider turn 且有明确 capture token 的最终化，在设计的短 drain 预算内纳入最终投递队列；预算后只能显式标记未收入本场，禁止已经 end 后改 watermark。不可用任意 sleep 猜测 final 都到齐，也不可把下一场 ASR 塞进旧场。

## 8. Outbox V2 与 outbox-only 恢复

V1 缺少 server binding、写曝光和明确关闭意图；不能通过补默认值把历史未知变成已知。建议增加 V2 envelope，保留 V1 读取和原文件，写迁移采用原子替换且验证成功后才切换。V2 验证失败不能退化为空队列。

最少新增字段：

- 原 productSession、scope digest、capture consent descriptor、createdAt/updatedAt、local revision。
- `capturePhase`、closeRequestedAt/closeReason/close watermark；用户恢复意图的审计 ID/时间与作用域（不存 token）。
- 已确认的 threadID/sessionID、对应版本和最后有效 receipt 摘要；若尚未知保持 nil，不生成假绑定。
- 每条 pending：现有稳定 delivery、contentHash、prepared/曝光/unknown/committed 状态、发送 generation；已确认水位。
- 会话断裂/缺失标记：明确区分完整记录与只保留部分历史，禁止把本案首尾缓存拼为完整长对话。

当前 `snapshots()` compactMap 会忽略不可解码文件；本问题新增恢复应使用带 `valid/absent/unreadable/decodeInvalid/scopeMismatch` 的扫描结果，坏文件形成可见 blocked 任务，不显示“没有记录”。

### 8.1 冷启动和页面重建

- 发现 outbox-only 即展示“有一段对话尚未完整同步/关闭”。可根据精确 productSession 进行同账号只读 discovery，记录 server binding 和水位；绝不 start/append/end/ack/admit、不开麦。
- V2 有 close 意图时展示“本次已在本机结束，等待恢复同步”；V1 无 close 意图时展示“上次未正常完成保存，可查看并选择如何处理”。不能仅 `recoveryBlocked` 日志后任由旧成功提示占位。
- 保留 B6 的恢复意图和回合 fencing、读取预算、authority 校验；不要直接恢复会调用 `.start` 的旧 capture initializer。
- recovery registry key 与磁盘 revision 包含 outbox/close revision，旧成功协调器不能作为新发现任务的当前证据。

### 8.2 用户明确恢复同场

新增明确按钮，例如“继续保存这段对话”；有 gap 时另外使用“仅整理此前完整内容”明确缩小范围。操作说明必须能让用户理解只处理该段已有表达，随后完成整理。`核实整理状态` 保持纯只读，不能暗中升级为该按钮。

用户点击后创建一个新的 `LiveMemoryUserResumeIntent`，绑定原 scope、原 productSession、当前磁盘 revision、恢复动作、期限与唯一 intentID。恢复执行器独立于 B6 reader；重新验证当前身份、最新授权和原 server 状态。

- 原服务器 session 唯一且 active/open：只处理该场已存 pending。unknown 先查；已提交则记回执，missing 则在此次明确恢复意图、当前授权与原 payload 绑定均有效时幂等重试同一命令；不能宣称 missing 证明从未发送。
- V2 已 close：按已冻结 N 同步并收尾。V1 未 close：按钮明确包含“保存现有内容并结束本段”，用户点击才新写 close 意图；不得声称推断出过去关麦的准确 N。
- 原 session 已 ended：不再 append；只读核实 batch。若仍有本地待送项，标为不完整/冲突，不能偷偷创建另一 session 或修改历史。
- 查无原 session、多个匹配、owner/authority 不同、消息 hash 冲突：停止写入，提供明确原因，不自动切换身份、不重建旧 Live。
- 恢复任务不自动开麦，不把用户当前的新说话追加进旧任务；若用户要继续新 Live，另建新 productSession。
- 页面离开、deadline 到期或用户取消后，新异步成功不能开始后续写；已曝光的操作保留 unknown，下一次显式操作先读确认。

这与 B6 不冲突：B6 只做发现和核实；只有明确的新用户操作创建可写恢复意图，而且每步仍受当前权限及原命令状态约束。

## 9. 提示归属和页面表现

将 capture/recovery 事件转为带 `productSessionID + localRevision + observedAt + phase + evidenceKind` 的 presentation，而非回调直接写 UILabel。

优先级：当前用户开启的场 → 刚刚用户关闭的场（成功/失败均保留至明确切换）→ 用户点选的历史恢复场 → 其余历史任务摘要。旧场 ready 回调只能更新该任务列表，不能覆盖今早未关闭/失败的提示。

Live 中同时显示简短的持久化状态，例如“正在聆听 · 本机已保存，等待同步”；临时故障不可被 `isUserControlledLiveSessionOpen` guard 全部隐藏。idle 渲染只处理麦克风交互态，不能清除仍有效的保存状态。

成功必须限定精确同场 batch：`reviewReady` 才显示“这次对话已进入待确认记忆”；点击链接带该 batch/source 的既有精确读取上下文。`noCandidates`、queued、failed、unknown 不共用成功文案。历史任务采用可读日期时间；多个历史任务用数量摘要，不使用无绑定的“上次”。

不把“本机已保存”等同“服务器已保存”，不把“服务器完整保存”等同“候选已生成”，不把“候选已生成”等同“正式记忆已确认”。

### 9.1 与问题 2 的 transcript 接缝

音频设计负责输出 `NativeLiveCanonicalTranscriptEvent(scope/accountLease, providerSessionEpoch, questionID/replyID, canonicalTurnID, role, text, finality/partialCause)`。本设计的 `appendCapturedTurn` 消费该事件，核对 scope/epoch 并以 canonicalTurnID 去重，再按 9.2 进入有序 staging；只有连续 complete 前缀才进入 delivery queue。网络派送及 UI 独立。

owner 只在 SDK 合同验证的权威 final 阶段 seal 单次，不能将同 Q 的 ASRInfo/Response/Confirmed 多采。assistant 按 reply 聚合，completed 仅代表文本生成完成，不能代表声音已播放；中断/错误/关场保留已接收 partial，不伪造听完。旧 generic onTTSStarted 的 native capture 入口必须同步撤下，否则新旧入口双写。具体 native final/drain 语义仍须按真实 SDK 核实；文本相似、timeout 不构成合同。

`requestClose` 只纳入 stop 前登记的原 provider epoch/capture token，且以最终事件是否完整决定 gap 标志；下一场和过期回调一律隔离。此处是两项设计共同的验收接缝，并非本轮已运行的新实现。

### 9.2 现有消息写合同与未定稿材料

本轮已读 `AppendInterviewMessageCommand`：现有消息写入合同含外层角色/kind 与 content 中的文本/采集模式/时间，没有 partial/finality 字段。**本方案不擅自扩大该写 schema 或让 Worker 解释虚构字段。** V2 中单独保存 `unsealedMaterials[canonicalTurnID]`，记录已收到文本、原 epoch/question/reply、finality 证据、接收 ordinal 和缺口原因；这是用户已同意的本场受保护内容，不是诊断日志。

每个槽位在首次合法输入登记时固定 captureOrdinal；只有首个未处理槽位起的连续 complete 前缀可在同一磁盘事务 seal 到 delivery queue，才分配 sequence/messageID/commandID。后面已 complete 的槽位要等前面 pending final，不能先派送造成倒序。重发最终回调不能再次 seal。

committed 后可以清理待发送正文，但至少在本场完整生命周期内保留 canonicalTurnID→captureOrdinal/sequence/messageID/commandID/contentHash/finality 的耐久去重元数据。相同 ID 相同封口内容为重复；相同 ID 不同已封口内容是冲突，不能覆盖或另建轮次。文本已经 complete 后发生停音，只改 playbackOutcome，不能降为 partial 或再次提交。未知 finality、被截断文本保留为材料，不能以普通 owner complete 上传、不能用标签拼入正文冒充用户原话。assistant partial 同样保留为上下文材料；完整的 assistant 文本即使未播完仍可发送为 context-only，播放结果独立记录。

有材料缺口时关麦仍立即生效、close 意图落盘；N 仅表示完整投递项的最后水位，不代表整场无缺失。界面显示“已保存收到的内容，部分内容未完整记录”；不自动把它标成完整整场整理成功。首个 gap 之前的完整项可同步，end/整理需经用户明确选择“仅整理此前完整内容”，绑定该 coverage revision；gap 及后面的全部材料（含已完成后缀）继续保留，不越 gap、不上传 partial、不重包装新 Source。后续材料不能自动丢弃或声称已整理，可由后续独立审核恢复需求处理。正常无缺口整场仍按原关闭意图自动收尾。将来若需要服务端承载 partial，须另做版本化内容合同与 Source/Worker 兼容设计，不能在本次补丁里暗加。

## 10. 可执行红绿验收矩阵

所有新实现的测试使用真实 coordinator、真实 outbox 文件、真实 FeatureGateService/Evaluator、真实 BackendClient + 受控 URLProtocol；只替换网络/时钟/磁盘故障点。不能只给 mock 改一个 success 返回值。红绿保持相同业务断言与输入，代码编译失败不算业务红。

| ID | 输入/故障 | 必须断言 | 本轮状态 |
| --- | --- | --- | --- |
| L1-R01 | 原场策略 300s 后到期，seq17 到达，继续到 20min 再关麦 | 原场 seq17…N 均落盘；无静默丢失；一次有界只读续期；随后完整水位与 end/ack/admit | 现场当前行为 FAIL；新方案 NOT_RUN |
| L1-R02 | 当前缓存已新鲜、同账号旧 route 已过期 | Live 使用 fresh request；不复用过期 route；全局 evaluator 仍拒绝旧 captured | 共享组件现状测试 PASS；Live 新组合 NOT_RUN |
| L1-R03 | seq17 派送暂时失败，seq18…30 接着到达 | pending 数量和角色/时间/hash 完整；capture 仍 open；网络恢复按序发送 | NOT_RUN |
| L1-R04 | 任意 seq 已提交但响应丢失 | 本地 unknown；GET 命中原 message/hash；不重复 POST；顺序继续 | NOT_RUN |
| L1-R05 | task 创建前取消/策略拒绝 | 标为 notExposed；原 command 仅首次发送一次；不能靠 pending 推断 | NOT_RUN |
| L1-R06 | HTTP 401/403/409/429/5xx、解码失败分别注入 | 各自分类；不同身份不续写；409读冲突；未知不重放；失败不转成功 | NOT_RUN |
| L1-R07 | 刷新成功但 late、连续多条表达、网络抖动 | single-flight；预算/退避可验证；late callback零写；磁盘仍连续 | NOT_RUN |
| L1-R08 | 新策略明确 deny，账号切换，authority改变 | 立即冻结；旧场文件保持；旧回调零新增写；新账号零泄漏 | NOT_RUN |
| L1-R09 | 相同账号正常token rotation | 本地同场身份保持；新request合法；旧callback按generation处理 | NOT_RUN |
| L1-R10 | 关麦时队列未同步/授权过期 | close先落盘、N唯一；无需等网络即可安全返回；新Live独立；旧场不得提前end | NOT_RUN |
| L1-R11 | close marker写失败、设备锁定保护态、磁盘满 | 不显示已保存；最后已落盘前缀不删；不释放唯一owner；明确暂停 | NOT_RUN |
| L1-R12 | 长用户发言后立即关麦；关麦同时入队/网络ACK；stop后迟到/下一场final | closeRequested先durable，权威final有界排空再seal N；缺final标缺口；seal后N不变；无跨场混写 | NOT_RUN |
| L1-R13 | kill于入队、曝光、回执、close、end/ack/admit各窗口 | 两进程复现；恢复只读；原ID/hash不变；不重复写 | NOT_RUN |
| L1-R14 | V1 outbox-only无close；V2有close无completion | 都展示正确任务；启动零业务写；显式按钮后才受控恢复 | 旧阻断测试 PASS；新恢复组合 NOT_RUN |
| L1-R15 | V2坏文件/未知schema/作用域错误 | 明确blocked，不变空列表；原文件保留；无fallback误写 | NOT_RUN |
| L1-R16 | 两旧pendingReview + 当前未同步任务，乱序回调 | 当前任务始终优先；旧成功只能更新旧任务；点入精确batch | NOT_RUN |
| L1-R17 | 同场20分钟混合事实与问题 | Source包含全部合法user证据/assistant上下文；B7语义测试独立执行，不用L1候选数替代 | NOT_RUN |
| L1-R18 | 受控隔离PostgreSQL全链 | seq/hash绑定、事务幂等、零漏写；status与目标候选集合可关联 | NOT_RUN |
| L1-R19 | 新合成20分钟真机持续Live | 关闭前不擅自end；到期多次仍完整；最后关闭有即时状态且候选归属正确 | NOT_RUN |

定向测试必含真实时钟注入推进 300 秒，无需人为等 20 分钟来覆盖每种排列；另用实际 20 分钟真机测试验证生命周期/音频/后台和实际服务。

每项收集 productSession 安全哈希、actor scope 哈希、sequence、角色、文本长度/hash、传输阶段、授权原因/到期、磁盘revision、HTTP状态、服务端精确回执/水位、UI presentation owner；不输出正文、凭据或完整业务ID。

补充并发/缺口门禁（均 NOT_RUN）：

| ID | 输入/故障 | 必须断言 |
| --- | --- | --- |
| L1-R20 | 第一个 owner 槽位未 final，后面 assistant 已 complete | 后缀不先分配投递序号；最终化后原顺序；最终缺失时只允许明确选择首 gap 前缀 |
| L1-R21 | closeRequested 已落盘、seal 前 kill | 重启不伪造排空或完整 N；freezing 可恢复为带 gap 的耐久状态；零自动业务写 |
| L1-R22 | ACK 后清理 pending 正文，再重复 canonical final | 去重元数据仍在；同内容零新 seq；异内容可见冲突 |
| L1-R23 | GET missing 后原写迟到提交，再触发显式恢复 retry | 旧执行器零自动重发；获授权同 ID attempt 与原写并发最终仅一条记录，未知 end/ack/admit 仍零重放 |
| L1-R24 | Swift/Python 对 Unicode、空白规范化、角色、kind、时间与 captureMode 计算绑定 | contentHash/commandIdHash/完整语义指纹按真实 domain 一致，CAS 例外仅 append；绝不拿 text hash 代替 |

## 11. 本轮实际执行的 7 项已有测试

已按 ios-debugger-agent 工作流使用现有 Booted iPhone 17 模拟器（iOS 26.5），调用 XcodeBuildMCP `test_sim`，复用 `DreamJourney_iphonesimulator26.5-arm64-x86_64.xctestrun`。没有安装或启动用户真机。结果 7 PASS、0 FAIL、0 SKIP，运行约 3.7 秒。

1. `testFormalMemoryPolicyM03FreshReadDecisionDoesNotReuseExpiredRoute`：真实 FeatureGateService；旧 route 仍拒绝，新 fresh request允许。
2. `testLiveTurnOutboxPersistsReplaysAndIsolatesAccountScope`：真实文件 outbox 的稳定队列与作用域保持。
3. `testInterviewLiveDeliveryCarriesStableSequenceAndCloseWatermark`：稳定投递序号与close watermark合同。
4. `testLiveRecoveryEntryBlocksUnclosedAndLegacyClosingOutboxes`：当前 outbox-only 会阻断；它是旧边界的证据，不能用 PASS 表示新恢复能力存在。
5. `testB6EndPreparedDiscoversAcceptedBatchReadOnlyWithoutReplayingEnd`：已有 B6 只读 discovery保持。
6. `testB8AdmissionPersistsOriginalCommandBeforeOneBoundedPolicyRefresh`：已有 B8 admission有界刷新保持。
7. `testB8AdmissionOutcomeUnknownCannotCreateAnotherWrite`：已有 B8未知写禁止新业务写保持。

结果文件：

- [xcresult](/Users/gaominge/Documents/liftora/outputs/2026-09-15-live-fix-design-analysis/memory/tests/existing-memory-contracts.xcresult)
- [完整执行日志](/Users/gaominge/Documents/liftora/outputs/2026-09-15-live-fix-design-analysis/memory/tests/existing-memory-contracts.log)
- [工具结构化结果](/Users/gaominge/Documents/liftora/outputs/2026-09-15-live-fix-design-analysis/memory/tests/existing-memory-contracts-tool-result.json)

这些是现状与兼容性证据，绝不构成本次修复的绿测。新的 coordinator解耦、策略续期、显式恢复、UI归属均尚未实现和运行。

### 11.1 本轮实际执行的后端验证

另执行 **7 项现有后端 unittest，7 PASS，0 FAIL**：start 幂等、owner-scoped append 幂等且不提升为记忆、提前 close 被拒/补齐后可关闭、同一序号异命令冲突、连续前缀关闭合同、current 只恢复 active/open、current 隔离 owner 与 paused 历史。使用真实当前 service/repository/API TestClient 与内存存储，耗时约 0.55 秒；不是 PostgreSQL 或真实模型证明。[日志](/Users/gaominge/Documents/liftora/outputs/2026-09-15-live-fix-design-analysis/backend/existing-contract-tests.log)。

另以真实当前读 builder 运行合成场次：写 seq1→end 成功→current 返回 null，而精确 repository 仍有 ended/连续水位/关闭水位；state wire 缺少逐条投递字段。它验证了 6.1 的接口缺口。[可复跑探针](/Users/gaominge/Documents/liftora/outputs/2026-09-15-live-fix-design-analysis/backend/read-and-long-input-probe.py)、[结果](/Users/gaominge/Documents/liftora/outputs/2026-09-15-live-fix-design-analysis/backend/read-and-long-input-probe-result.json)。新 GET 尚未实现，所以这不是新 GET 验收通过。

## 12. 与 B6/B7/B8 的兼容和交付要求

- B6：保留冷启动只读，原 scope、round fencing、deadline、registry、坏文件显式状态。新增用户恢复执行器不能从 `start()/viewDidAppear/foreground` 自动触发业务写。
- B8：复用 typed outcome与逐阶段 checkpoint；已有 end/ack/admit未知结果不能重放。Live 新鲜授权必须贯穿相应阶段，不只修append却在end/ack又卡旧route。B8未完成的PG/unknown-admit门禁不能被本次7测试替代。
- B7：Source输入完整性是本问题门禁，候选质量是B7独立门禁。昨晚B7未部署，不能把本场无Source归为B7。本轮已对真实当前后端运行长输入 probe：201turn、单turn4001字、整场32000字、33draft分别可能在 organizer 分片合法后被 support_request 整场合同拒绝；small控制通过。该尚未部署的B7风险必须作为集成门禁，避免本次修复随本地B7一起发布后再次失败；B7 的独立交付范围见 12.1，不以它替代capture根因。证据：[后端真实probe结果](/Users/gaominge/Documents/liftora/outputs/2026-09-15-live-fix-design-analysis/backend/read-and-long-input-probe-result.json)。
- 问题2：共享 EchoViewController 时按范围协调修改；此设计不改 Live 朗读、ClientInterrupt、AEC、音频route或分页行为，不把音频通过算作L1通过。
- 不重放或“修补”今早历史数据；保留证据文件。任何找回/重新导入不完整首尾片段须另外让用户审核内容，不能冒充整场恢复。

部署：必需 iOS 更新和最小 API 只读投递状态接口；原 current/state 合同缺口已通过本轮真实 probe 证实，不能把本方案称为纯 iOS 修复。API 字段/受理版本见 6.1。仅为本问题不要求 Candidate Worker 部署或数据库历史清理；若发布包含昨晚B7代码，必须额外满足其独立长输入门禁。

完成局部代码后分别报告 `L1_LOCAL_PASS/FAIL/NOT_RUN`、模拟器/隔离PG/真机状态、指纹、证据路径与回退块。真实长场及新恢复路径未通过前，不能标记现场缺陷已关闭。

### 12.1 独立的 B7 长输入集成门槛

实测当前未部署 B7 的 organizer 能合法分片，但 support_request 仍按整场构造，出现：201 turns 被总 turn 上限拒绝；单 turn 4001 字被单 turn 上限拒绝；全场 32000 字被总长度上限拒绝；33 drafts 被 draft 集合上限拒绝。小输入控制可构造请求。测试仅到真实 request builder，未调用模型，不能称模型处理长对话失败；也不能把该未部署版本作为早晨事故原因。

Sol 应把此项独立列为 B7 集成缺陷，不用本 L1 补丁顺手改全部候选逻辑。交付默认拆开 L1 的 API 增量与 B7 既有未提交改动；若发布组合包含 B7，该组合必须先补齐独立长输入支持并通过上述四例及跨片更正/遗漏用户事实的语义门禁。修复必须保留整个逻辑 Source、每条用户证据及每项候选的审核覆盖；不得截前 200 轮、截字数、只审前 32 个候选、绕过 support review 或无依据抬高上限。未经验证的组合维持集成 NOT_RUN/FAIL，不能因为 L1 恢复持续入库就宣告20分钟完整整理已经通过。

## 13. 给 Sol 的实施顺序与交付清单

1. 核对当前 HEAD 和 dirty diff，保留 B6/B7/B8 既有修改；建立本次局部差异清单。两份设计共用 canonical 事件合同，先写完整/未定稿、稳定 ID、close 排空接口及对应反例。
2. 后端先实现 6.1 精确只读接口及内存/隔离 PostgreSQL 合同；iOS 并行实现 V2 原子入队、mayExpose、关闭意图和水位。不等待网络授权来决定是否已本地保存。
3. 接入 fresh request、有界刷新与顺序派送；再接显式恢复、同场状态展示。与音频改动统一切换 native 采集入口，避免 generic 与 typed 双写。
4. 执行第 10 节固定输入的修复前红/修复后绿，再验跨进程、完整 UIKit 链、隔离 PostgreSQL 和新20分钟真机场次；与第二份文档在手机上组合验收策略过期时长答不断、文本不漏采；不附加原车机专项验收。
5. 输出修改文件和行号、源指纹、实际运行命令/证据、各层 PASS/FAIL/NOT_RUN、发布依赖与回退步骤。不能把未运行门槛写 PASS。

回退按本次局部变更撤回客户端能力；保留 V2 文件及关闭意图，不删除或降级重写未知材料。服务端只读接口可保留兼容。旧客户端若无法解释 V2，只能明确暂不可恢复，禁止当空队列继续写旧场。开发交付完成不等于已获生产部署或历史数据重放授权。
