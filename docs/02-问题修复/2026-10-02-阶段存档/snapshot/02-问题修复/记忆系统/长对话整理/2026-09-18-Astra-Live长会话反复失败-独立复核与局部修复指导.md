# DreamJourney Live 长会话反复失败：独立复核与局部修复指导

日期：2026-09-18。交付对象：Sol 的现有“开发”任务。

**本次已经完成源码复核、隔离模拟器对照和局部修复原型验证。用户的 iOS 工作区尚未应用这些修改。真机测试由用户今后主动发起，不属于本轮开发任务的完成条件。**

## 1. 结论与本轮范围

截图中 `A_LOCAL_INCOMPLETE` 如实反映了 Sol 当时的结果。但“9 个测试失败”不能直接解释成 9 个产品缺陷：本次在业务源码保持不变的隔离副本中，校正测试装配后，原报告的 9 个失败方法已经分别通过。

同时，本次另用受控网络和真实组件确认了一个产品缺口：**认证拒绝后的恢复流程过早领取唯一重试名额；恢复准备失败又被当成原 append 的发送结果处理，产生非法的持久化状态回退。随后即使认证恢复，也可能无法继续原场次。**

局部问题编号：`DJ-LIVE-L20-RECOVERY-20260918`。

本次在隔离副本中修改两个业务文件，使用同一个有效反例完成先红后绿：认证恢复先失败，之后提供有效凭据并触发已有前台恢复入口，原场次从确认水位 48 排空至 70，完成 end / ACK / admit / status，进入 pendingReview。最终受影响模块回归 569/569 通过。

因此，Sol 下一步应集成已定位的测试校正和局部恢复修复，不必重新改写采集、持久化、音频或整套授权系统。之前的指导对测试前置条件核对不足，本文件纠正这一点；既有权限、绑定和未知写保护继续保留。

## 2. 基线与证据

- 用户工作区：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`。
- 原始报告：[Sol 本地修复报告](../../../outputs/2026-09-18-dreamjourney-live-l20-auth-sync-completion/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证同步-本地修复报告.md)。
- 本次证据目录：`/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-l20-second-review/`。
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`，在其现有未提交成果上取副本。
- 复核的 OwnerTruthContracts、EchoViewController、BackendClient、测试文件指纹与原报告一致；交付前再次确认原工作区的四个文件均未被本次改变。详见 [源码核对](/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-l20-second-review/final-source-verification.json)。

| 验证对象 | 本次实际结果 | 证据 |
|---|---|---|
| 原 Sol 完整 OwnerTruth 回归 | 517 项，508 通过，9 个方法失败 | 原 `ownertruth-full.xcresult`；本次目录的 `ownertruth-full-summary.json` |
| 业务源码不变，只校正凭据签发时间 | 原 70/48 用例 1/1 通过 | `fixture-fresh-at-refresh.xcresult`、`fixture-only-result.json` |
| 业务源码不变，校正另外 8 个测试 | 8/8 通过 | `fixture-corrections-and-recovery-negative.xcresult`、`followup-probes-result.json` |
| 同包新增“恢复认证失败”反例 | 1 个方法失败，实际变为 unavailable | 同上；不能与前面的 8 个通过混算 |
| 完整“先失败、后恢复并关闭原场次”反例，原业务源码 | FAIL：非法状态回退、名额已消耗，仍停在 48 | `recovery-eventual-completion-red-bound.xcresult`、`resume-red-bound-result.json` |
| 同一完整反例，局部修复原型 | PASS；连同 7 个保持性用例，共 8/8 | `recovery-prototype-targeted-green.xcresult`、`prototype-targeted-result.json` |
| 原型的受影响模块回归 | 569/569 通过 | `recovery-prototype-affected-regression.xcresult`、`prototype-regression-result.json` |

上述均为 iOS 26.5 模拟器、受控网络及合成数据证据。真实 SDK、物理 20 分钟、生产网络与真机均未在本轮运行。原型的模拟器编译随测试完成；通用 iOS 无签名构建需由 Sol 在最终集成源码上执行。

## 3. 原报告的 9 项失败为什么反复出现

### 3.1 70/48 用例在恢复时提供了已过期的“新凭据”

位置：[测试辅助函数](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:3939)，原始关键段在 3972–4026 行。

测试提前生成六代凭据，第 n 代有效期为 `logicalStart + n × 240 秒`；刷新时只是取下一张预生成凭据。第 49 段拒绝后，测试让采集推进到逻辑第 1200 秒，再释放恢复。下一张凭据也在第 1200 秒到期，不能作为有效的新凭据。

[真实认证合同的独立探针](/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-l20-second-review/auth-fixture-expiry-probe-result.txt)确认：该 successor 身份关系有效，但恢复时访问凭据已不可用。BackendClient 拒绝它是正确行为。

只把测试刷新器改为在刷新发生时签发 successor，使用当时的 `fakeNow + TTL`，保留同一用户、family、parent、递增 version 和原请求绑定，原 70/48 完整测试即通过。未增加产品延时，未删除凭据时效校验，也未缩短逻辑 20 分钟。

测试还应按实际事件同步：确认被控制的 delivery-status GET 已到达后才释放响应；若测试允许再次核实，首次延迟应由独立的一次性标志控制，不能把后续 GET 再次挂起。这是测试时序校正，不是修改产品超时。

### 3.2 明确 deny 测试等待了一个尚未触发的 start

位置：[原明确 deny 用例](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:4661)。

空 Outbox 的 Coordinator 初始化并不立即发 start；第一段有效正文落盘后才启动自然输入链。原测试先等待真实 start POST，再输入第一段正文，因此前置等待无法成立。它提供的 start 回执还使用固定 thread/session ID，与真实请求生成的 ID 不一致。

校正后的流程是：先用允许状态输入一段合成正文并确认保存；记录请求基线；再让策略过期并刷新成明确 deny，输入第二段。断言第二段只保留在磁盘，**deny 后新增 message POST 为 0**。回执严格使用当前请求的 thread/session ID。真实 Manager → Echo → Coordinator → 磁盘 → FeatureGate → BackendClient 组合通过。

禁止为迁就旧测试而提前启动真实业务场次，或放宽 receipt 绑定。

### 3.3 另外 7 个旧测试没有适配请求授权与异步边界

| 原失败方法 | 测试校正 | 业务源码不变的结果 |
|---|---|---|
| `testInterviewLiveUseCaseQueriesTheSelectedProductSession` | 提供有效 fresh decision，等待 ready | PASS |
| `testInterviewLiveUseCasePausesOrdinarySessionBeforeStartingLiveWhenTransitionAllowed` | 同上，保留旧场暂停和新场绑定断言 | PASS |
| `testInterviewNaturalInputUseCaseSubmitsLiveAssistantTurnWithContextOnlyRole` | 提供 fresh decision，分别等待 start、append 完成 | PASS |
| `testLiveCaptureDeliveryFailureDoesNotStopDurableCaptureOrCloseIntent` | 提供 fresh decision，让测试真正到达目标 append 失败 | PASS |
| `testLiveCaptureReadOnlyStatusConfirmsLostAppendWithoutSecondPost` | 同上，保留仅一次 append 与只读确认断言 | PASS |
| `testLiveCaptureDeliveryStatusTimeoutIgnoresLateResultAndAllowsBoundedRetry` | 同上，保留迟到结果和有界读取断言 | PASS |
| `testLiveCapturePartialStatusNeverPostsUnconfirmedQueuedTurn` | 同上，保留未确认内容不重发断言 | PASS |

这里的等待使用有界事件/条件同步；不能改成固定 sleep，也不能给所有测试默认放行权限。缺失授权的负例仍应保留拒绝行为。

## 4. 真正需要修改的恢复缺口

主要位置：

- [Coordinator 恢复准备](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2572)。
- [Coordinator 统一失败处理](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2210)。
- [磁盘状态转移约束](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:16561)。
- [实际重试曝光边界](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2107)。

已复现的顺序：

1. append 49 获得可绑定的 pre-handler 认证拒绝证据，磁盘状态为 `authenticationRejected`，前 48 段已确认，22 段待同步。
2. 精确的只读核实确认第 49 段未应用。
3. `prepareVerifiedAuthenticationRetry` 立即将唯一额外尝试标成已领取，然后才重建 UseCase、读 current、准备认证和策略。
4. 认证准备失败，UseCase 发出 `requestNotSent`。
5. Coordinator 将它套用到原 append，试图写入 `preparedNotExposed`；Store 正确拒绝 `authenticationRejected → preparedNotExposed`，Coordinator 随后进入 `unavailable`。
6. 再次前台核实后，名额已领取，不能再次 claim，原场次继续停在 48。

**关键错误是混淆“恢复准备没有成功”和“原 append 从未发送”，并在还没有进入重试发送前消耗了重试名额。** 磁盘正文没有因此消失；同步恢复无法推进，因而 end / ACK / admit 没有完成。

这一结论来自本地真实组合反例；不能反推原真机 `401 → 403` 的所有服务端原因已经查清，也不能把历史 UI 仲裁日志归入本场根因。

## 5. 局部修改设计及已验证原型

实验性业务补丁：[isolated-recovery-prototype.patch](/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-l20-second-review/isolated-recovery-prototype.patch)。它仅修改 EchoViewController 与 OwnerTruthContracts 两个业务文件，未应用到用户工作区。

1. **恢复准备阶段只验证资格。** 检查原冻结命令、拒绝证据、当前磁盘阶段、名额未领取和场次绑定。读取与认证准备不消耗发送名额。
2. **把 durable claim 移到已取得新鲜请求授权之后、实际重试曝光之前。** 写入失败必须零发送；claim 后出现崩溃仍按已有保守规则处理，不重置名额。不得把 claim 移到网络发送之后。
3. **恢复准备失败只结束本次准备操作。** 按原 message / 场次 / UseCase 归属清除进程内临时许可及失效 UseCase；保留正文、冻结命令、认证拒绝证据及真实曝光阶段。不得改写为从未发送，也不得把真实存储失败吞成网络暂停。
4. **恢复入口重新读事实，再决定是否发送。** 已验证原型使用已有前台恢复事件，先 GET 核实，再绑定原 current session，获得有效授权，发送同一冻结 append，然后排空尾部、关闭同一场次。禁止直接因前台事件重发未知写。
5. **已曝光的重试继续只读。** 如果额外尝试已经标记 `authenticationRetryExposed`，后续 notSent/失败不得把它改回 prepared 或重新领取。Store 的 retry exposure 必须要求前一阶段是 `authenticationRejected`，防止同一已领取名额重复曝光。

原型已经证明上述方向可以完成“失败后恢复并真正保存”，不只是改变页面提示。它没有增加用户可见状态、固定延时、成功兜底或新的保存路径。

正式集成时还要收紧两处边界：明确策略 deny 应稳定暂停，不能通过既有状态核实入口形成反复刷新循环；账号或场次已变时，旧准备回调不能恢复发送。可以沿用现有 generation/UseCase 身份保护，无需重构历史 UI 仲裁。

## 6. Sol 的执行顺序与验收

本次交接不是让 Sol 再从头调查。按以下顺序直接完成本地集成：

1. 核对当前未提交工作区；保留已有修改。集成上面的测试校正，先重跑原 9 个失败方法，确认都触达各自目标阶段。
2. 加入有效的失败后恢复反例。以 `recovery-eventual-completion-red-bound.xcresult` 对应场景作为产品红证据：明确命中认证准备失败，随后凭据恢复，仍要求原场次完成 70 段及关闭链。
3. 参考两个业务文件的原型进行局部实现；不把临时目录脚本当作在正式工作区可直接执行的安装脚本。确保每次失败归属原操作，claim 与曝光顺序保持正确。
4. 同一断言转绿后，验证下表中直接受影响的边界；已有有效证据可以复用，不扩大到无关产品改造。
5. 在最终集成源码上运行 OwnerTruth 全量、所选账号/Echo/音频保持性、模拟器构建、通用 iOS 无签名构建和 `git diff --check`。记录最终指纹与真实结果。
6. 全部本地验证通过即正常交付 `LOCAL_PASS / DEVICE_NOT_RUN`。真机条目列为用户后续主动发起的独立验收，不等待设备、不请求连接、不安装或启动 iPhone，也不把没连手机写成阻断。

| 受影响模块/风险 | 必须保持的验收结果 | 本次原型证据 |
|---|---|---|
| Live 正常同步与关闭 | 逻辑 20 分钟完成；70/48 排空 22 段；同场 end/ACK/admit/status 完成 | 已通过 |
| 认证准备先失败、后恢复 | 失败时不消耗发送名额、不错误回退磁盘状态；恢复后只补发原第 49 段一次，最终完成 70 段 | 同一反例先红后绿 |
| durable claim / exposure / 重建 | 原命令和证据不变；已领取/曝光不能重置；已曝光重建零第三次发送 | 已有定向与完整回归通过；集成时补足发送后 notSent 的专项断言 |
| 明确 deny | 已允许部分保持已确认，后续拒绝部分保留本地；零新增业务 POST、无刷新循环 | 普通 deny 组合已通过；重试准备期间 deny 单独核对 |
| 回调归属 | A→B 或账号变化后，旧回调零新发送、不改 B 状态/预算 | 既有 OwnerTruth、B8 和账号回归通过；重试准备迟到回调单独核对 |
| canonical/partial/关闭交接 | 正文持续落盘，partial 不伪装完整，关闭水位不提前 | OwnerTruth 完整回归覆盖并通过 |
| 短会话待确认记忆路径 | 原短会话 end/ACK/admit 与唯一候选行为保持；不替换已经真机通过的路径 | 本地相关回归通过；此前真机 PASS 保留，不冒充本轮重测 |
| 音频、主动打断、恢复聆听 | 原所有权、播放和回合约束保持；无车机分支或 CarPlay 修改 | 所选 Echo/音频与账号共 51 项通过；真机音频本轮未运行 |

本次完整回归细分：OwnerTruth 518、AccountLeaseRuntime 7、AudioOwnerLeaseModel 5、DialogAudioSessionOwnershipPolicy 3、DialogEngineAudiblePlaybackPolicy 3、DialogEngineDelegatedPlaybackState 5、EchoLiveAudioRoutePolicy 7、EchoTurnIntentReducer 17、EchoViewModelTurnAdmission 4，共 569。这里的 51 项是明确选择的保持性范围，不是所有音频测试或真机声音的证明。

不改历史 UI 仲裁、Live 音频、B7 语义过滤和覆盖摘要文案。不部署、不操作生产/历史数据、不清理旧任务、不 commit/push。后端本轮没有已证实需要新增修改的合同；不要因为本地夹具失败扩展后端工作。

## 7. 可直接发给 Sol 的提示词

```text
请继续当前“开发”任务，在现有未提交成果上完成本地集成。

先完整阅读：
/Users/gaominge/Documents/liftora/02-问题修复/记忆系统/长对话整理/2026-09-18-Astra-Live长会话反复失败-独立复核与局部修复指导.md

Astra 已经独立运行受控模拟器对照：业务源码不变时，校正凭据签发时间、deny 启动装配及旧测试的 fresh decision/异步等待后，原报告的 9 个失败方法分别通过。另一个真实缺口也已有先红后绿：认证恢复准备失败会过早耗掉重试名额，并错误回退 append 的磁盘状态；两个业务文件的局部原型已让认证失败后的原场次恢复、从 48 排空至 70 并完成 end/ACK/admit/status。原型回归 569/569 通过。

请按文档第 6 节直接集成测试校正和局部实现，不重新做整套调查，不重写已通过的保存路径。参考证据目录内 isolated-recovery-prototype.patch 和 isolated-final-prototype.patch；它们是隔离副本的实验材料，请结合当前源码审阅后集成，不能盲目覆盖工作区。

验收必须包含恢复准备先失败、随后认证恢复，最终仍完成原场次保存；不能只修改提示文案。补齐文档所列重试期间 deny、迟到回调及曝光后 notSent 的针对性保持性验证。未知写不重放，原 command/正文/场次绑定与一次额外尝试预算不放宽。

最终源码完成必要的本地红绿测试、OwnerTruth 全量、所选 Echo/音频/账号回归、模拟器及通用 iOS 无签名构建、diff 检查，并交付指纹和报告。遇到本地失败继续定位首个失败断点并修复，不以阶段性失败报告代替尚未完成的本地工作；如有真正无法本地解决的外部阻断，明确列出证据。

本轮任务不包含真机。真机由我以后主动发起；不要检测或等待手机，不要因未连接手机中断，也不要请求真机授权。本地通过后即可按 LOCAL_PASS / DEVICE_NOT_RUN 正常交付。

保留历史 UI 仲裁、Live 音频、B7 和覆盖摘要修复。不要部署、操作生产或历史数据、清理历史任务、commit/push。
```
