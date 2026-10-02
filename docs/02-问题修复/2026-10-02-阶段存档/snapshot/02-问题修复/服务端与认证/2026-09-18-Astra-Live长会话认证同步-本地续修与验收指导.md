# DreamJourney Live 长会话认证同步：本地续修与验收指导

> 本文件保留此前核对时的代码和测试快照，C1–C7 的未完成判断已有后续进展。当前开发请改以 [当前代码复核与开发执行方案](2026-09-18-Astra-Live长会话认证同步-当前代码复核与开发执行方案.md) 为入口，避免重复已完成修改。

日期：2026-09-18\
问题编号：LIVE-L20-AUTH-01\
关联主设计：**v1.2**，本文件为当前工作树的续修清单。\
当前结论：**LOCAL_INCOMPLETE；不是仅剩真机验收。**

## 1. 现在应该做什么

继续修改现有代码，完成本地闭环、本地回归和交付后结束本次开发任务。真机测试仅由用户之后另行主动发起，不属于本次任务的自动步骤或完成条件。现有失败已经能在 iOS Simulator 上重现，不需要连接手机、刷新候选、点击恢复、重装或等待生产日志；手机未连接不得导致本地任务中断、BLOCKED 或推迟交付。

本文件承接 [主设计](2026-09-17-Astra-Live长会话认证续期与同步恢复-问题分析及修复设计.md)，没有新增业务范围。保留 Sol 已有 R1/R2 及此前短场持久化修复，补齐未完成的 R3/R4 和测试。不能将整个工作区回退，也不能只把状态文案改到“不报错”。

## 2. 已核实的进度和停止原因

【开发】任务目前为空闲。最近一轮最后回复的是手机连接截图：确认 iPhone 已被 Xcode 识别，随后结束了回复。此前的执行记录显示正在补 R3 并编译；没有完成本地修复交付。因此这次停止不能解释为“已经修好，等真机”。

补充执行记录还包含用户反馈的列表刷新、只读核实和 App 重启，不能继续声称原始进程始终未被重启。这些记录不阻止本地修复；旧场恢复以后按当时真实磁盘与回执另行核实，本轮不再要求手机操作。

直接使用 `xcresulttool` 读取实际结果，得到：

| 已有结果包 | 实际结果 | 能证明的范围 |
|---|---|---|
| `pre-fix/live-l20-red.xcresult` | 0/2，FAIL | 路由和认证用例失败。认证用例后续调整过策略时间夹具，其修前失败原因还需按 C6 校正。 |
| `post-fix/live-l20-r1-r2-green.xcresult` | 1/2，FAIL | 路由映射用例通过；认证用例仍失败。不能因文件名有 green 就称全部通过。 |
| `post-fix/live-l20-r1-green.xcresult` | 1/1，PASS | 修正夹具后，认证预检的一个定向用例通过；不代表生产 single-flight/CAS 全链通过。 |
| `post-fix/live-l20-r3-integrated.xcresult` | 0/1，FAIL | 当前加入 401 注入的组合测试未排空、未关闭。 |

这些包位于：

`/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-l20-auth-sync-fix/run-2026-09-17-01/evidence/`

最后一项具体断言失败包括：

- `pendingTurns.count` 实际为 **6**，预期 0。
- append 请求实际 **15** 次，预期 21 次（20 个正常交付，加一次明确认证拒绝的受控再尝试）。
- 第 15 段只尝试一次，没有完成该受控重试。
- end、acknowledgement、admit、最终状态读取均为 **0**，预期各 1。
- 未进入 pendingReview，结束水位没有到 20。

这是一条本地可复现的阻断证据。测试使用 iOS Simulator，不是手机实测。当前同名测试已加入第 15 段 401 场景，不可直接说“原来完全相同的测试回归失败”；必须恢复原场景覆盖并单独命名新增场景。

经工具核实的结果摘要与失败位置保存在 [本次复核证据目录](/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-l20-local-completion-review)：`verified-xcresult-summaries.json`、`failed-integration-test-details.json`。

## 3. C1：先消除当前恢复分支的队列阻断

### 当前代码问题

[EchoViewController.swift:2487](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2487) 的 `prepareVerifiedAuthenticationRetry` 将 `inFlightTurn` 保留为原 turn，持久化状态后清空 `naturalInputUseCase`，再调用 `ensureNaturalInputSession()`。

但 [advanceNaturalInputPipeline](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2239) 的入口要求 **`inFlightTurn == nil`**。重建 UseCase 返回 active session，并不会带上该 turn 的成功 message receipt，因此 `.ready` 路径不会将这个 in-flight 清除，发送入口仍被挡住。这条代码路径与本地 6 段未排空的表现一致。

### 局部修复方式

不要用“无条件清空 inFlight”或“放宽所有发送 guard”解决。为原失败 append 建立一个明确的恢复入口，使它成为唯一持有该 turn 的操作：

1. 固定原 turn、原 append、原账号及场次和失败 attempt；记录唯一恢复 operation generation。
2. 在原身份下核实认证拒绝资格、合法认证 successor、fresh policy 和精确会话状态；异步期间保留队列所有权，阻止普通 FIFO 同时取同一个 turn。
3. 只有全部核实成立，才由专用恢复入口提交这一个原 append。可以在同一个 UseCase 增加受约束的 resume intent；或使用显式绑定旧场次的恢复对象。不能通过把任何失败用例强设为 ready 来获得通行权。
4. 恢复入口直接管理原 turn 的一次发送，不再依赖“仍有 inFlight，却要求 FIFO 自动重新取队首”的矛盾路径。
5. 原 append 成功且回执绑定通过后，磁盘 acknowledge 一次，清除该 in-flight，再调用原 FIFO 推进后续未曝光项。
6. 原 append 结果未知则保持只读核实。失败回调、重复唤醒、超时与关闭并发均不能启动第二个发送 owner。

先写一条短反例：2 个已确认 + 1 个认证拒绝 + 2 个本地待发送，模拟成功的合法恢复。修前必须因队列不推进失败，修后证明原拒绝项重试一次、尾部各首次发送、最终 queue=0；然后再跑完整 70/48 场景。

## 4. C2：恢复必须精确绑定原场次，不能退回普通 start

当前 `prepareVerifiedAuthenticationRetry` 使用 `naturalInputUseCase = nil → ensureNaturalInputSession()`。普通构造的 `allowStartWhenCurrentMissing` 对当前活动场次可能为 true；`receiveCurrentSession` 在 current 为空时可能进入新 start 路径。这不符合本次“恢复已建立原场次”的要求。

恢复入口须携带并逐项校验原 productSession、threadID、sessionID、vault/subject、authorityEpoch 和原命令所用版本：

- current 为空、查到不同 session、版本与冻结命令矛盾、authorityEpoch 变化，均暂停，不创建替代场次。
- 正常首次 start 的行为保持不变；严格限制是针对中途 append 恢复入口。
- 原场次已 ended 时，只能按对应已结束证据收尾，不能继续 append 或开启新场次。
- 旧 callback 只属于自己的 operation generation，不能重绑新 Live、消费新场预算或清除新队列。

必须新增 current=空、同 productSession 但不同 session、epoch 变化、版本前进、原场次已结束的负例，断言新增 start=0、越权 append=0，原磁盘记录保持。

## 5. C3：重试资格与重试曝光必须分开，保证崩溃后不会再次发送

当前已经增加 `authenticationRejected`、`authenticationRetryExposed` 两个内部枚举，但仅增加名字还不足以实现安全恢复：

1. `prepareVerifiedAuthenticationRetry` 在真正发请求前就写入 `authenticationRetryExposed`。
2. FIFO 又把 `authenticationRetryExposed` 当成可发送状态。
3. `hasUnknownClosingWriteExposure` 只识别 mayExpose/outcomeUnknown，遗漏新增加的 retry exposed 状态。

因此必须修正这两个含义：**被允许进行一次重试**与**那一次重试已经可能上网**。

- 使用一个仅属于当前恢复 operation 的一次性发送许可，配合磁盘中的 attempt 证据。许可不是由“读到 exposed 枚举”自动产生的。
- 在网络发送前的曝光边界落盘该 attempt 的曝光事实；一旦可能曝光，后续 deadline、冷启动、页面重建、重复唤醒都视为结果未知，只读核实。
- `authenticationRetryExposed` 不能作为通用 FIFO 自动发送条件。保留它也好，改成等价结构也好，都必须遵守上面的语义，不新增产品页面状态。
- 重试资格领取、曝光前落盘失败、发送后响应丢失、收到第二次认证拒绝，各有明确终态或暂停分支。最多允许原 append 两个网络 attempt（最初一次 + 一次合同允许的重试）。
- 不通过回写 preparedNotExposed、重建 messageID/commandID、重置 retry budget 或清磁盘实现再次发送。
- 检查所有 dispatch state 读取处，尤其恢复加载、截止时间、队列调度、Unknown 判断、序号水位、持久化编码解码和穷举分支。不能只改到编译通过。

关键验收：第一次重试已被网络接收、响应丢失，然后重建 Controller/Coordinator；该 append 的总网络次数仍为 2，不能变成 3。仅获得许可但尚未发送的中断也要保存真实阶段，不谎报已保存。

## 6. C4：冻结原 append 与拒绝证据，不能重新拼出另一个请求

当前 `StoredTurn` 只有 delivery/contentHash/dispatchState；delivery 只固定 commandID、messageID、sequence、角色、正文和时间。`appendCommand(...)` 会使用重新传入的 session/thread/version 拼装完整请求。

因此，单凭相同 commandID/messageID 不能证明重试的是完全相同业务命令。此次只为恢复需要增加最小持久化数据：

- 原完整 append 的不可变业务字段，或能够无歧义重建原 payload 的冻结值及其 hash；包括原 thread/session、expected versions。
- 拒绝类别、对应的原 attempt、账号/场次作用域、command/message/sequence/payload hash 的绑定。
- 已使用的重试预算与当前 attempt 的曝光阶段。

写入原始命令/拒绝证据失败时不得获得自动恢复资格。旧格式缺少这些证据则保持原先的只读/安全暂停，不批量补造。新的可选字段或版本迁移须兼容既有短场和已完成检查点。

新旧两次 append 应逐字段及 payload hash 对照；除了传输层认证/功能授权 headers 与诊断 attempt，业务 payload 不能变化。若只读取得的服务端版本无法与原命令相容，应暂停，不静默重算 expected version。

## 7. C5：补齐真实认证合同和发送时效的验证

### 7.1 认证拒绝分类

当前 [ClientError.isVerifiedPreHandlerAuthenticationRejection](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:6918) 仅判断 401、code=nil、固定 detail 字符串。函数名中的 Verified 不能代替证据。

本地使用真实后端 middleware 与隔离测试仓储证明该完整响应一定在业务 handler/事务前产生，handler 次数=0。客户端在有原请求绑定的响应解析处识别符合合同形状的完整响应，再形成明确的类型与持久化证据。普通 401 数字、纯文本 fallback、截断/解码失败、不符合合同的错误形状、未知 4xx 或后端权限 deny 均不能自动获得重试许可。

当前本地后端工作树没有本轮新增 middleware 合同测试的交付证据。先补本地测试，不要求拿手机或生产去制造 401。若本地合同证明失败，保持该分支关闭并继续修正，不把它当成“等待真机”的理由。

### 7.2 R1 的时钟与认证刷新

当前 `prepareOwnerTruthInterviewNaturalInputAuthentication` 把调用时的 `now` 传到异步 refresh 回调继续使用。增加一个反例：refresh 等待期间时钟前进，successor 在真正发送时已经过期；不能仍因旧 now 而判可用。

以可注入时钟在回调和曝光前重新读取时间，配合原 operation/lease/CAS 检查。保留现有 single-flight/CAS 实现，不另写刷新器。至少一个组合测试走真实刷新实现与隔离 auth store，不能全部使用直接替换 session 的 QA callback。

### 7.3 R2 的真实 HTTP 证据

当前通过的 R2 测试只断言 `featureForRequest` 返回 `.echoTextInput`，并未证明实际 GET 的第一次 401、刷新、第二次请求 headers 正确。

为 current 和 delivery-status 分别新增 BackendClient + FeatureGate + URLProtocol 测试，检查两次实际请求。successor 要改变 sessionId，并用生产 `FeatureGateService.accountGeneration(forIdentitySource:)` 生成对应代次，不能拿 sessionId 原文或 lease UUID 替代。覆盖本调用刷新、另一个请求已刷新、明确 deny、账号切换、策略与认证同时过期。

## 8. C6：恢复已通过场景，新增失败场景，校正红绿证据

当前 `testManagerEchoRealGateBackendLogicalTwentyMinutesClosesAtExactWatermark` 中已经加入 sequence=15 的 401 注入、append=21 的新断言。这个改动不能替代此前的断网/未知响应保持性覆盖。

1. 保留并恢复原已通过场景的测试语义与原断言；从可核对的改前快照/补丁取得，不凭记忆造一个“原测试”。新增独立命名的认证拒绝组合测试；可复用夹具，但各自断言与失败原因独立。
2. 原认证红测使用过时策略夹具，后来调整过。请用相同正确夹具和相同断言，在隔离改前实现上补足真正失败于“access token 未续期”的红测。不能把策略门禁拒绝当成认证预检的正确反例，也不能改当前工作树回退用户已有成果。
3. 原 route 测试继续保留，同时新增 C5 的实际 HTTP 断言。
4. 用第 3 节的小恢复场景快速定位，再跑完整 70 段/48 确认场景：49 明确拒绝 → 合法续期/授权 → 原命令重试一次 → 50–70 各一次 → queue=0 → end watermark=70 → ACK/admit 各一次 → pendingReview。
5. 同时证明第 49 段真正未知且未查到时仍零重放；已查到精确回执时不再 POST；第二次拒绝不再重试；不完整内容仍 partial。

至少保留以下独立结果：原逻辑 20 分钟保持性、认证过期发前续期、实际 GET 401 恢复、明确拒绝原命令恢复、未知写零重放、70/48 完整关闭。任何一项未通过，都不能把本轮整体报成“已修好”。

## 9. C7：执行顺序和完整交付

| 顺序 | 本地工作 | 本步结束证据 |
|---|---|---|
| 1 | 保存当前增量和失败结果，保留 R1/R2 与历史修复；用短恢复反例定位 C1。 | 明确触发哪个 guard、队列状态和请求次数；不再停在泛泛调查。 |
| 2 | 按 C1–C4 实现精确、一次性的原命令恢复，补 C5 认证合同和时钟问题。 | 小场景稳定通过，负例没有新增写，磁盘及崩溃边界正确。 |
| 3 | 按 C6 恢复原场景并新增认证场景，执行主设计 L20-01～L20-16。 | 70/48 完整排空、关闭链成功；未知分支仍只读。 |
| 4 | 执行主设计第 7 节全部受影响本地回归、OwnerTruth、音频/D1/B8 保持性、模拟器与通用 iOS 编译。 | 本轮实际 xcresult/构建摘要，缺项明确，不抄历史数量。 |
| 5 | 交付本地修复报告、逐文件影响与验收映射、指纹、最终真机清单。 | 适用本地项全通过才 `LOCAL_PASS / DEVICE_PENDING`；当前为 `LOCAL_INCOMPLETE`。 |
| 后续独立阶段 | 仅在用户另行主动发起后进行真机验收。 | 本地交付后任务正常结束，不检测或等待手机；后续真机先短场，再跨认证有效期的长场及受影响功能。 |

本地工作未结束时，手机连接状态、截图或顺便询问的状态问题只作为补充信息。应简短回答后继续原修复任务，不把本地修复替换成一轮手机操作引导，也不在已知本地测试失败时称“可以安装/真机测试”。如果受到新的互斥指令中断，应明确说明保留下来的开发状态，而不是隐去未完成部分。

本次不操作手机、不修改生产数据、不部署、不清理历史、不 commit/push；不修改历史 UI 仲裁、B7、音频产品策略和覆盖摘要规则。共享层受影响功能按主设计映射回归，已通过短场保存链必须保住。

需要交付完整本地结果，不要在“R1/R2 各一项通过”“加入新枚举”“编译通过”“GET 能返回”或“手机已连接”的位置结束开发。
