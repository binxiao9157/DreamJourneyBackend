# DreamJourney Live 长会话认证同步：当前代码复核与开发执行方案

> 2026-09-18 独立复核更新：原报告的 9 个失败方法已在业务源码不变的隔离副本中通过测试装配校正分别转绿；另一个认证恢复准备缺口已有局部原型及 569 项本地回归证据。后续剩余修改与交付请以 [独立复核与局部修复指导](../记忆系统/长对话整理/2026-09-18-Astra-Live长会话反复失败-独立复核与局部修复指导.md) 为准，不重复重写已完成的链路。


日期：2026-09-18\
问题编号：LIVE-L20-AUTH-01\
用途：交给 Sol 在现有工作区继续开发，完成本地修复、测试和交付。\
当前结论：**LOCAL_INCOMPLETE，尚不能宣告修复完成，也不应转入真机验收。**

## 1. 本轮任务和执行顺序

修复目标是：长时间 Live 遇到认证续期或确证的认证前置拒绝后，仍能持续采集和本地保存；在满足原命令安全恢复条件时排空同步队列，完成原场次的 end → ACK → admit → pendingReview。此前已经通过真机验证的短场持久化链必须保留。

**直接在已有修改上继续开发。本次任务范围为：本地修复 → 本地定向验证与受影响回归 → 交付本地报告和后续验收清单 → 完成本次开发任务。真机测试仅由用户在之后另行主动发起，不属于本次任务的自动执行步骤或完成条件。**

手机连接、手机截图、新一轮真机复现、生产日志和旧失败场次的恢复，都不是本地开发、测试或交付的前置条件。不得自动检测或等待用户手机，不得因手机未连接、未解锁、未配对而中断本地任务、报告 BLOCKED、索要手机操作或推迟交付；手机已经连接也不代表用户发起了真机测试。

本地测试使用模拟器；通用 iOS 构建使用 generic/platform=iOS 和无签名配置，不绑定真机 UDID、不要求设备在线。完整本地门禁通过并交付后正常结束任务，不继续等待真机授权。报告中的 DEVICE_PENDING 仅记录真机尚未验收，不表示本地任务仍在阻塞等待；同时明确写“本地开发已完成；真机 NOT_RUN，由用户另行主动发起”。

本文件替代此前“本地续修与验收指导”中的进度判断和剩余任务顺序；原设计中的安全边界继续有效。Sol 只需以本文件为当前执行入口，不要把旧清单中的“尚未实现”原封不动地再做一遍。

工作区：

- iOS：/Users/gaominge/Documents/Codex/Video/DreamJourney_dev
- 后端：/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend
- 当前任务：**开发**。不新建任务，不切换到手机操作指导。

本轮允许局部修改 iOS 代码和本地测试，补充隔离的后端合同测试。保留当前工作区所有既有修改，不整体回退。没有授权部署、生产数据操作、手机安装或操作、历史清理、候选审核写、commit/push。

## 2. 当前核对结论

这次核对读取了当前源码、Sol 最新执行记录和真实 xcresult 内容，不按截图、测试包文件名或历史测试总数判断完成度。

### 2.1 已有修改应保留

| 项目 | 当前实际修改和证据 | 判断及保留要求 |
|---|---|---|
| R1 发前认证准备 | Live authority 已接入 accessExpiresAt 判断，复用 refreshAuthSession；认证刷新回调现在重新调用注入时钟。发前到期和刷新期间时钟前进的定向用例通过。 | 已有有效进展；不能退回只刷新 FeatureGate 策略的实现。生产刷新链及最终发送边界仍按第 5 节补齐。 |
| R2 两个 Live GET | current、live-delivery-status 的精确 GET 路由已映射到 echoTextInput，adapter 已传 freshFeatureDecisionAfterRecovery。两个 GET 的旧凭据 401 → 已存在合法 successor → 200 用例通过。 | 映射及该恢复分支已实现。测试目前没有核对完整策略 headers，也没有覆盖本请求真正执行 auth refresh，不能认定 R2 全部验收完毕。 |
| 原 C1 恢复分支被 inFlight 阻挡 | Coordinator 的 ready 分支新增专用恢复提交入口，原 inFlight 不再等普通 FIFO 自动重新取队首。20 段认证拒绝恢复组合已有 PASS。 | 不再把旧的 inFlight 死锁列为“完全未修改”。保护这个修复，继续完成 70/48 和负例。 |
| 原 C2 原场次约束 | 授权重试时禁止 current 缺失后自动 start；ready 分支检查 thread、session、epoch、thread/session version 和 active lifecycle。 | 关键 guard 已补。原完整命令尚未持久化，且缺少各个不匹配分支的验收证据。 |
| 原 C3 重试曝光保护 | 一次性许可留在内存；在曝光 hook 写 authenticationRetryExposed 后消费许可；FIFO 不再把该状态当作可发送；unknown 判断包含该状态。 | 关键修改已补。磁盘枚举重建后零 append 的 spy 用例 PASS，但尚不等价于真实两次 HTTP 后崩溃、重建、无第三次 POST 的组合证明。 |
| 原 C6 场景拆分 | 无 401 注入的逻辑 20 分钟场景与第 15 段认证拒绝场景已独立命名，2/2 PASS。 | 保留两个独立场景。之后又修改了共享夹具，最终交付需在当前同一份源码上重跑。 |
| 后端认证合同测试 | 已新增 test_expired_bearer_rejection_happens_before_live_append_is_applied；与 delivery-status 既有用例合跑 2/2 OK。 | 不能再说“没有新增后端测试”。但现在只检查响应和 deliveries 为空，缺少 handler/业务入口调用数为 0 的直接断言。 |

### 2.2 已核实的测试结果

结果包根目录：

/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-l20-auth-sync-fix/run-2026-09-17-01/evidence/post-fix/

| 结果包 | xcresult 实际结果 | 能证明什么 |
|---|---|---|
| live-l20-r3-integrated-retry-owner.xcresult | 1/1 PASS | 当时版本的专用恢复入口通过一个组合场景。 |
| live-l20-original-and-auth-recovery.xcresult | 2/2 PASS | 当时版本的无 401 场景及 20 段认证恢复场景通过。 |
| live-l20-retry-exposed-rebuild.xcresult | 1/1 PASS | 人工构造 retry-exposed 磁盘状态后，Coordinator 重建保持只读。 |
| live-l20-get-401-successor.xcresult | 1/1 PASS | 一个测试方法分别执行两个 GET，验证 Authorization 从旧凭据换到 successor。 |
| live-l20-auth-clock-and-gets.xcresult | 3/3 PASS | 发前认证、刷新回调时钟、上述 GET 用例通过；包含重复运行，不应把各包数字累加成独立覆盖数。 |
| live-l20-seventy-fortyeight.xcresult | 0/1 FAIL | 70/48 组合没有完整关闭。失败断言包含 pendingTurns=10、append=61 而非 71、end/ACK/admit/status 均为 0、结束水位缺失。 |
| live-l20-seventy-fortyeight-auth-rotation.xcresult | **BUILD_FAIL，测试执行数 0** | 构建阶段有 11 条错误条目，包括测试取消以及 logicalStart、authRefreshCount、authSessions 作用域错误等；summary 的 unknown 不能标成 PASS。 |

最后一份失败构建结束于北京时间 **2026-09-18 00:33:40**。当前 OwnerTruthContractsTests.swift 此后于 **00:35:09** 又被修改，已有多代 authSessions 夹具，因此也不能断言“当前文件仍有完全相同的编译错误”。准确状态是：**夹具又改过，但本次核对未找到该最新版本成功编译及通过 70/48 的结果。**

Sol 最后一条回复转去确认手机连接，随后任务结束；没有形成完整本地修复交付。这不表示上述未通过项目可以跳过。

本次复核证据：

- [实际结果摘要](/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-l20-current-implementation-review/verified-current-xcresult-summaries.json)
- [70/48 失败断言](/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-l20-current-implementation-review/seventy-failed-test-details.json)
- [后续构建失败内容](/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-l20-current-implementation-review/seventy-auth-rotation-build.json)
- [已有后端合同测试执行记录](/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-l20-current-implementation-review/backend-contract-test-record.json)
- [本次源码指纹](/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-l20-current-implementation-review/current-source-fingerprints.json)

## 3. 现场事实、当前缺口和排除项

原真机现场是本地 70 段、服务端确认 48 段、剩余 22 段待同步。第 49 段 append 返回 401；后续只读核实中的认证恢复请求出现 403。关闭时进入 syncPaused，未完成 end/ACK/admit。

必须区分：

1. **正文采集与本地落盘仍工作**；这次不是把 canonical 内容全部采集丢了。
2. **服务端交付与关闭链未完成**；本地有正文不等于生成待确认记忆。
3. 当前源码已修复一部分同步恢复路径，但缺少完整的原命令/拒绝/attempt 证据，关键长场组合仍无通过证据。
4. 原生产 403 的具体 deny reason 尚未取得直接证据。当前路由缺陷及本地复现支持修复方向，但不能写成已查明当时生产唯一原因。

已通过的短场持久化、长回答播放、partial 覆盖摘要，不得因为本问题被重新设计。历史 Echo 文案归属错误继续独立记录；不在本轮修改历史 UI 仲裁、B7 语义过滤、Live 音频、车机/蓝牙策略、ASR 识别模型或候选审核业务。

## 4. D1：先恢复当前测试基线，完成真正的 70/48 组合

**类型：最新修改待验证 + 组合验收未完成。第一步就做，不等待手机。**

入口：[OwnerTruthContractsTests.swift:3839](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:3839) 的 testManagerEchoRealGateBackendSeventySegmentsResumeFromFortyEightConfirmed 及其共享夹具。

### 4.1 修正测试自身的两个问题

先编译并运行最新夹具，解决已有作用域/装配错误。当前源码已经补了 authSessions 等变量，先验证实际状态，不机械重改最后一次构建中的旧行号。

当前 questionCount=35 仍沿用每轮加 120 秒。按现有循环，最后一轮时间为起点后 **4,080 秒，即 68 分钟**，且只有 questionCount=10 时断言 1,200 秒。这是测试时长定义不准确，不能把它当成“物理或逻辑 20 分钟通过/失败”的等价证据。

保留原 10 问答、20 段场景及语义。为 35 问答、70 段场景设立独立时间参数或夹具：

- 明确开始、结束时间，并断言逻辑时长为 1,200 秒；认证 TTL 与策略 TTL 独立注入，按测试计划跨越边界。
- 如要保留当前 68 分钟、多代凭据压力场景，单独命名并记录时长，不能用它取代 20 分钟验收。
- 不靠延长 token 有效期、缩短需要覆盖的认证边界或删除队列/关闭断言换取绿测。

### 4.2 真实覆盖“先积压 22 段，再恢复”

当前夹具每轮都等服务端追上，然后才采集下一轮。它能测试第 49 段的即时恢复，但不足以覆盖现场“服务端停在 48，本地继续到 70，用户停止时还积压 22 段”。

在独立用例中使用受控网络/回调暂缓恢复，不使用固定 sleep：

1. 先完成前 48 段确认。
2. 第 49 段返回符合合同的前置认证拒绝，暂缓后续认证或只读恢复。
3. 继续通过真实 Manager → Controller → Coordinator 接入其余内容，等待真实磁盘完成。
4. 断言本地 70、服务端 48、待同步 22，正文完整；第 50–70 段未曝光。
5. 此时手动触发 finish 一次，确认 close intent 已持久化；不要再次停止或新开 Live。
6. 释放受控恢复，原 49 只允许一次合法再尝试，50–70 各首次交付，连续水位达到 70、队列为 0。
7. 最终只沿用原关闭链：end 的 watermark=70，ACK、admit 各一次，原场次达到 pendingReview。

另保留“仍在 Live 中恢复，然后继续交谈并停止”的场景，避免只验证关闭后恢复。

每个场景检查原 session/thread/productSession 不变、start 总数为 1；原第 49 段两次 payload 全字段相同，不能只检查 commandID。本地状态由真实 Controller/Coordinator 处理 HTTP 回执推进，不得直接把状态设成 pendingReview。

## 5. D2：补齐真实认证刷新及 GET 授权证据

**类型：部分实现已完成，仍有测试和发送边界缺口。**

### 5.1 使用生产刷新实现的至少一条组合链

当前新增组合仍通过 authSessionRefresher 闭包替换 authSession；GET 测试则在第一次 401 的网络 handler 内直接把 active 换成 successor。这验证了一部分 adapter 行为，**没有执行生产 single-flight/CAS/刷新响应合法性检查全链**。

复用 [DreamJourneyBackendClient.swift:17438](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:17438) 的生产 refreshAuthSession。使用隔离 AuthSessionStore、AccountSessionActor 和受控 /auth/refresh 网络；必要时只增加可注入依赖，不新写第二套刷新逻辑，不连接真实账号存储。

至少验证：

- 本调用执行一次 refresh，并发等待方共享它；同族合法 successor 通过 CAS 提交。
- 其他请求已经刷新时复用合法 successor，不再重复 refresh。
- subject、family、parent/session version 不合法，账号切换、注销或 CAS 过期时零业务写，旧回调不清除新账号凭据。
- 多次认证轮换与策略 TTL 分别计数，不能把策略 refreshCount 当作认证刷新次数。
- refresh 失败/超时后正文和恢复坐标保留，不使普通业务 POST 自动刷新重发。

保持 FeatureDecision 的认证账号代次和 App AccountLease 的 UUID 分别验证，**禁止恢复两者必须相等的错误判断**。

### 5.2 GET 测试必须检查导致 403 的授权信息

入口：[OwnerTruthContractsTests.swift:9289](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:9289)。

现有测试仅断言两次 Authorization；受控服务端无论有没有 captured policy 都返回 200，所以它还无法防止“新 token 但缺策略 headers”的回归。

对 current 和 delivery-status 分别保留“已有 successor”并新增“本调用 refresh”分支。受控服务端验证实际请求的 feature、decision、policy revision、account generation 等生产合同所需 metadata；缺失或错误时明确拒绝。metadata 必须由当前有效策略和真实 accountGeneration(forIdentitySource:) 生成，不能硬编码补齐 headers。

覆盖策略同时过期、明确 deny、账号切换、无效 successor。断言拒绝时无业务 POST、不循环刷新；业务 GET、auth refresh、policy refresh 各自计数。保留精确 method/path 映射及负向路由用例。

### 5.3 补真正曝光前的时效反例

[prepareOwnerTruthInterviewNaturalInputAuthentication](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:11880) 已修复刷新回调复用旧 now 的问题，应保留。

但当前链仍为“认证预检 → 可能异步等待策略 → 曝光/发送”；现有测试没有证明策略等待或主线程调度期间认证再次到期的行为。增加受控时钟反例，在策略回调释放前令认证过期，断言过期 token 的业务 task 不得 resume。若失败，在局部发送边界重新验证认证时效与 operation/scope，并有界回到现有认证准备流程，随后重新取 fresh policy。

不要通过固定等待、无界刷新或把所有 POST 的 allowsRefresh 打开解决。也不要把尚未经过反例验证的这一潜在缺口写成已证实的生产根因。

## 6. D3：认证拒绝必须保留可验证类型，不能靠错误文案授权重试

**类型：明确的当前实现缺口，必须修改。**

当前 [ClientError.isVerifiedPreHandlerAuthenticationRejection](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:6918) 只检查 401、code=nil 和固定 detail 字符串；[backendErrorContext](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:17659) 会把 JSON detail、message、error 和纯文本 fallback 都折叠成相同的 detail。

因此，当前分类丢失了“是否为合同规定的完整响应”的信息。不能依靠这个布尔函数为业务重试发放许可。

推荐的局部实现：

1. 在 Live append 的 BackendClient 响应解析边界识别可信 endpoint 返回的完整、符合当前后端合同的响应；在通用错误字符串化之前构造一个内部的、带原请求绑定的认证前置拒绝证据类型。
2. 保持通用错误显示与其他业务结果语义；可让既有 serverRejected 携带该专用 Error，UseCase 仅接受这个专用类型。不要把任意 ClientError 或 401 数字重新解释成安全重试。
3. 证据绑定原 scope、场次、原 command/message/sequence、payload hash、原 attempt 和合同类别；不保存原始 token、完整授权头或未经筛选的响应。
4. 完整规范 JSON 可形成资格；纯文本、message/error fallback、截断、空体、解码失败、其他 401/403/409/5xx、绑定错误都不能形成资格。
5. 同一原 append 至多一个额外业务 attempt。第二次认证拒绝、再次超时或结果未知，仍暂停/只读核实。

后端现有新增用例位于 [test_owner_truth_interview_input_api.py:553](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_owner_truth_interview_input_api.py:553)。扩展它，用 spy/计数器包住 downstream handler 或 Live append 业务入口，直接断言调用数为 0，同时保留零交付、零版本变化等断言。该测试继续走真实认证 middleware 和隔离仓储，不需要部署或生产 401。

当前后端生产代码未改；优先利用现有响应合同完成本地证据，不为本轮客户端恢复随意增加服务端新接口。

## 7. D4：持久化原完整 append、拒绝证据和 attempt 预算

**类型：明确的当前实现缺口，必须修改。**

当前 [StoredTurn](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:16231) 只有 delivery、contentHash、dispatchState。delivery 的 [appendCommand](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:9512) 仍用传入 receipt 的 session/thread/version 重建请求。

ready 分支已校验版本，这是有效保护；但它不能替代磁盘中的原完整命令与拒绝/预算证据。新增内容必须局部、兼容现有 outbox：

| 最小持久化内容 | 要求 |
|---|---|
| 冻结的原 append | 保留原业务字段或可无歧义重建它们的值及规范化 payload hash；包括 command/message、sequence、role、captureMode、capturedAt、正文、thread/session、expected versions。 |
| scope 与场次绑定 | 复用既有 subject/vault/authority/productSession 隔离；不能把认证轮换当成新业务场次。 |
| 原拒绝证据 | 来自 D3，绑定原 attempt 和原 payload；不能从磁盘上孤立的 authenticationRejected 枚举补造。 |
| 重试预算与曝光阶段 | 记录唯一额外 attempt 是否领取/可能曝光；内存许可与磁盘事实分别处理，重建不能重置预算。 |

在原业务请求首次可能曝光前，原完整命令及阶段必须成功原子落盘。拒绝证据和后续 attempt 保存失败则不得发送。第二次请求只能复用冻结业务 payload，允许变化的是合法传输认证/策略和诊断 attempt。

保留现有一次性许可与 retry-exposed 只读规则；不要把 exposed 状态改回 prepared 来重发。旧格式缺字段仍可读，缺少新证据的旧场只读核实，不能批量迁移成有资格自动重试。

验收至少包括：原命令两次全字段/hash 一致、版本改变拒绝重试、拒绝证据落盘失败、attempt 落盘失败、旧文件兼容、已完成短场检查点兼容、两个账号/场次相互隔离。

## 8. D5：补齐原场次、崩溃、迟到回调和关闭并发

**类型：已新增部分 guard，需真实组合证明；失败处再做最小修改。**

保留 [receiveNaturalInputState](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2154) 的专用恢复入口，以及 [prepareVerifiedAuthenticationRetry](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2519) 的单 owner 约束。不要全面放宽 ensureNaturalInputSession 或普通 FIFO。

| 场景 | 必须满足的断言 |
|---|---|
| current 为空、不同 session/thread、epoch 改变、版本前进或 lifecycle 已 ended | 无替代 start；无错误 append；保留磁盘。已 ended 只能凭与原命令匹配的证据收尾。 |
| 规范认证拒绝 → 原命令合法第二次发送 → 响应丢失 → 重建 Controller/Coordinator | 通过真实 BackendClient/受控网络实际经历两次 POST；重建后总数仍为 2，不能是手写枚举代替整个流程。 |
| 原 49 已提交但首次响应丢失 | 精确只读回执可确认；该命令 POST 总数为 1；尾部未曝光项继续。 |
| 原 49 真正未知且只读未查到 | 零重放，不越过缺口，不 end，不伪报成功。 |
| 第二次认证拒绝、401 合同不匹配、策略 deny、版本冲突 | 不出现第三次业务请求；不重置许可或预算。 |
| 恢复过程中用户停止、继续到来的已登记 final、重复唤醒 | capture/落盘连续；真实尾水位正确；close intent、队列 owner、end/ACK/admit 各只有一个有效拥有者。 |
| A 场迟到结果，B 场或新账号已开始 | A 不发往 B，不清 B 的 poll/队列，不消费 B 的重试预算，不改 B 的页面/磁盘。 |
| 认证/策略/状态读取超时；页面离开重进；重建 | 有界等待和坐标保留；迟到成功/失败按有效 operation 归属；不能重新发未知业务写。 |
| 完整关闭后冷启动 | 只读取得本场结果，不重放 start/append/end/ACK/admit；历史页面文案归属仍单独记录。 |

新恢复流程要在异步边界验证账号 lease、Coordinator/UseCase 身份、恢复 operation、原 message/attempt；仅一个 messageID 布尔许可不应被当成所有异步归属的替代证明。已有 guard 足够的分支用测试证明，不无故重构。

## 9. D6：先红后绿与受影响回归

### 9.1 测试组织

复用真实 Controller → Coordinator → UseCase → 隔离 Outbox/Checkpoint → BackendClient → FeatureGate；网络受控、数据合成。至少一个组合走生产认证刷新实现。不要仅以 spy 的调用次数或直接返回业务终态证明整条链成立。

已有可核对的红绿证据保留。新增 D3/D4 缺口、关键 70/48 组合和不足的认证/headers 断言，保留同一有效夹具、同一断言的修前失败与修后通过。需要旧实现时使用隔离副本，不回退当前用户工作区。

区分以下结果：

- 编译失败不等于业务红测。
- 测试夹具 token 或策略过期导致的失败，先说明夹具与目标场景的关系，不能直接认定新生产根因。
- 调整夹具后必须重跑相关用例；旧 xcresult 不证明最新源码已通过。
- 本地逻辑时钟、模拟器、真实 Provider、物理 20 分钟、生产候选结果分开标记。

### 9.2 本轮本地门禁

| ID | 本地验收项 | 最低通过标准 |
|---|---|---|
| DEV-01 | 当前测试编译及原两个 20 段场景 | 当前同一份源码构建成功；无 401 与认证恢复场景分别 PASS。 |
| DEV-02 | 生产式认证准备/刷新 | 单次、并发、successor、CAS/换账号、超时及发送时效边界均通过；没有第二套刷新器。 |
| DEV-03 | 两个 Live GET 的两种 401 恢复 | 实际 token 与全部必需策略 metadata 正确，负例缺头会失败，明确 deny 不循环。 |
| DEV-04 | 认证拒绝合同 | 真实 middleware 下游零调用；客户端形状/绑定负例不能获取重试许可。 |
| DEV-05 | 原 append/attempt 磁盘证据 | 同 payload，预算持久、旧格式兼容、落盘失败零发送。 |
| DEV-06 | 真正积压 22 段的 70/48 组合 | 本地 70/确认 48/队列 22 中间状态可断言；恢复到水位 70，end/ACK/admit 一次，pendingReview。 |
| DEV-07 | 未知写/崩溃/原场约束 | 真正未知零重放，已提交只读确认，已重试最多两次，错误场次零新 start。 |
| DEV-08 | 持续采集和停止/重进/迟到 | 不丢正文，尾水位正确，旧回调不污染新场，超时有界。 |
| DEV-09 | 已通过功能保持性 | 按下表及既有相关测试执行，无新增回归。 |
| DEV-10 | 最终构建和交付 | OwnerTruth 回归、受影响音频/Echo 等回归、模拟器及通用 iOS 无签名构建、diff 检查通过；结果与源码指纹对应。 |

### 9.3 保护已通过部分：改动—影响—验收

| 改动层 | 本地必测 | 用户后续主动发起真机测试时复验 |
|---|---|---|
| Live authority / 认证准备 | 完整短场 start/append/end、generation 与 lease 分域、有效 token 不额外 refresh。 | 短场停止后真实候选产生且唯一；冷启动数量保持。 |
| BackendClient / GET route / 若涉及通用 requestJSON | 两个 Live GET；B8 文字关闭、ACK/admission 未知写不重发；候选/正式记忆和 B6 只读恢复；受影响其他写的 401 零重放。 | 相关读取、短场关闭链与同场状态读取保持正常；不借验收操作生产审核写。 |
| Outbox / Coordinator / UseCase | dedup、完整正文、close intent、水位、未知 start/end/append、旧磁盘、页面重建、B8-S01/S01-08 旧 poll 隔离。 | 物理 20 分钟、认证续期、断网恢复、完整保存、无重复候选。 |
| Manager/Controller 间接受影响 | D1 非 ASR 过滤、明确 final 不降级、停止前排空、覆盖摘要与 partial；既有 Echo/音频保持性。 | 长回答自然播放、主动打断、恢复聆听、停止时 partial 正确。 |
| 共享认证实现如需改动 | 既有 AccountLease、AccountSessionActor、认证 store 的 CAS/并发/退出/切账号测试。 | 隔离测试账号下验证相关认证场景；不得改变真实用户凭据或清数据做测试。 |

每个实际改动文件都要补上影响映射，不能因为“没直接改音频文件”就省略已明确受影响的保持性测试。相反，未受影响的产品模块不新增开发任务。

partial 分支之前的正确表现保持 PASS；没有肉眼捕获瞬时文案切换仍是 NOT_OBSERVED，不算新缺陷。历史 UI 仲裁保留独立 FAIL，不为消除它而扩大本轮范围。

## 10. 开发顺序和停止条件

1. **建立当前基线**：记录源码/差异，编译当前测试；执行 DEV-01 与 70/48，准确记录最新状态。
2. **修正装配与验收场景**：按 D1 区分 20 分钟、68 分钟压力场景和真实 22 段积压；不能只让现有循环变绿。
3. **完成实质实现**：D3 的类型化合同、D4 的冻结命令/磁盘证据/预算，及 D2/D5 反例暴露的局部缺口。每项先定位最小失败，再修改并验证；保留已完成的恢复入口和曝光保护。
4. **完成组合与回归**：DEV-01～DEV-10 在最终源码上全部满足，失败继续在本地修；不转向手机获取开工资格。
5. **交付并完成本次本地任务**：形成报告、测试映射、源码和构建指纹及后续真机清单。仅此时可标 LOCAL_PASS / DEVICE_PENDING，并明确“本地开发已完成；真机 NOT_RUN，由用户另行主动发起”。交付后结束任务，不检测手机，不等待连接或授权。

**本次任务之外的后续验收**：只有用户另行主动提出开始真机测试时，才进入新版短场保持性 → 长场认证/断网恢复 → 受影响交互。此前清单仅供准备，不能自动执行；手机/生产尚未执行均保留 NOT_RUN，不用本地 PASS 替代。

阶段性汇报后继续当前开发，不在“增加测试”“两个用例通过”“看到手机在线”或“准备重新运行”的位置当作交付结束。本地存在失败或关键未测项时，明确 LOCAL_INCOMPLETE 并处理它。若遇到确实无法继续的本地开发条件，交代具体命令、错误、已完成工作和所缺信息；用户手机未连接和用户尚未发起真机测试均不是本地阻塞条件。不能为了取消手机依赖而跳过尚未通过的本地验证。

本轮不自动恢复原手机 70/48 场。未来同类会话能安全完成，与历史无完整拒绝证据的旧场能否恢复，是两个不同问题；不得补造旧证据、清恢复坐标或重发未知写以获得 PASS。

## 11. 交付清单

建议保存到新的目录：

/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-l20-auth-sync-completion/run-2026-09-18-01/

交付至少包含：

1. 当前问题的最终根因与实现说明，区分现场事实、源码证明、本地复现和未证实生产细节。
2. DEV-01～DEV-10 执行清单，实际测试名、命令、xcresult 或后端输出、断言结果；每次构建失败单独记录。
3. 新增/复用红绿证据、70/48 中间积压与最终关闭计数、未知写零重放及原 payload 一致性证据。
4. 逐文件改动与受影响模块映射；短场持久化、B8、D1、Echo/音频等回归结论。
5. 源码与最终构建指纹、测试运行时、最终本地状态；不能引用此前 501/510 等数字替代本轮实跑。
6. 最终真机清单和旧场恢复限制，明确尚未执行的部分。

安全诊断仅记录阶段、计数、经过脱敏的归属、拒绝类别及 metadata 是否存在；不输出真实正文、token、完整 headers 或真实账号标识。

## 12. 可直接发给 Sol 的提示词

> 请在当前【开发】任务和现有工作区继续完成 LIVE-L20-AUTH-01，先完整阅读：
>
> /Users/gaominge/Documents/liftora/02-问题修复/服务端与认证/2026-09-18-Astra-Live长会话认证同步-当前代码复核与开发执行方案.md
>
> 这份文件是当前执行入口。保留已经完成的认证准备、GET 映射、专用恢复入口、原场次 guard 和重试曝光保护；按照文档 D1～D6 补齐剩余实现与测试，不重新从头调查，也不重复已完成修改。
>
> 直接先编译、核对最新 70/48 夹具，随后完成认证拒绝类型、原完整 append/拒绝/attempt 的持久化、生产刷新链和真实授权头验证，以及真实 22 段积压后的关闭组合。此前短场持久化必须保住，受影响模块按表回归。
>
> 本次任务只做本地修复 → 本地测试与回归 → 本地交付，完成后正常结束任务。真机测试由我之后另行主动发起，不是本次任务的自动步骤或完成条件。不要检测、连接、等待、安装或操作我的手机；手机未连接、未解锁或未配对不得导致本地任务中断、BLOCKED 或无法交付。即使手机在线，也不要自行开始真机测试。不要访问生产、清历史、审核候选、部署或 commit/push。
>
> 阶段性汇报后继续开发；DEV-01～DEV-10 适用本地项全部完成前不要宣告本地修复完成。模拟器测试及通用 iOS 无签名构建不绑定我的手机。请最终交付本地实测结果、影响清单、源码/构建指纹和后续真机验收清单；满足本地门禁后标 LOCAL_PASS / DEVICE_PENDING，并写明“本地开发已完成；真机 NOT_RUN，由用户另行主动发起”。交付后结束本次任务，不等待我连接手机或批准真机测试。
