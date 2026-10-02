# DreamJourney Live 持久化回归溯源与真实保存链修复设计

日期：2026-09-17。问题编号：**DJ-LIVE-PERSIST-01**。

范围：比较已成功的真机保存链、09-15 长对话改造、09-16 持续保存修复和 09-17 saving 修复；定位当前服务端写入阻断，并给出局部实施及验收方法。

**本轮结论：发现并用生产源码片段复现了一个确定的授权合同错误；业务修复尚未实施，最新真机保存结果仍为 FAIL。** 本轮只读审查工作区、历史报告和相关修改记录，运行隔离源码反例并生成文档。未修改 App/后端代码，未操作手机、生产数据或历史任务，未部署、commit/push。

## 1. 结论与对上次分析的纠正

用户关于“长对话相关修改破坏了以前能工作的持久化”的判断有依据，但前后发生了两次不同的回归：

1. **09-15 采集入口替换回归。** 原生 Live 从旧 `onASRResult` 采集迁移到 canonical 接口，旧入口关闭，而新接口把显式 ASR final 降成 interim；在没有额外 QueryConfirmed 时，完整用户发言无法封存。此问题已在后续代码中修正。
2. **09-16 长场授权修复又引入请求阻断。** 新增的 `OwnerTruthInterviewNaturalInputRequestAuthority` 要求 `FeatureDecision.accountGeneration` 等于 `AccountLease.generationId.uuidString`。前者在生产中是 24 位 SHA-256 摘要，后者是 36 字符 UUID，二者属于不同身份域，无法相等。正常的新鲜授权也因此不能构造写入 authority。

当前应首先修第 2 项。标准非 QA 路径中，新场 current-session 读取成功且无既有会话后，会准备 start 命令；上述比较会在真正 start POST 之前拒绝它。已有服务端会话时，使用相同 authority 的 append/end/ACK 也受影响。**不是必须聊够 20 分钟才触发，一问一答就能暴露。**

上次 `DJ-LIVE-SAVING-01` 分析找到了“失败用例不再推进，saving 没有收尾”的真实缺口，但没有查清用例为什么失败，也没有发现测试覆盖了错误的身份输入。这让实现集中在恢复一次失败 GET、增加截止时间和转换展示状态，未恢复实际写入。该分析和验收范围不足，本文件修正其根因优先级。

已有截止时间和诚实的异常提示可以承担异常收尾，但不能作为此缺陷修复完成的证据。此轮不新增用户状态、不以切换文案验收，也不把所有近期修改整体回滚。

## 2. 证据分层与历史对照

### 2.1 已有成功基线

[09-12 B4 真机复测报告](/Users/gaominge/Documents/liftora/outputs/2026-09-12-dreamjourney-b4-device-retest/2026-09-12-DreamJourney-B4真机复测报告.md:24) 记录：

- 约 10 轮交流，10 个 owner turn 全部持久化。
- 同一批次完成 end/ack/admit，状态到 pendingReview。
- 候选从 36 变为 38，新增 2 条能对应本场 review batch/source。

这是“以前确实能保存到本场待确认记忆”的历史证据。它不等于当时所有功能都通过：该报告还记录候选读取和审核问题，也不能证明当时真实 20 分钟跨 TTL 一定成功。

### 2.2 回归时间线

| 阶段 | 实际变化 | 对保存的影响 | 证据性质 |
|---|---|---|---|
| 09-12 B4 | 原有会话、正文、end/ack/admit 链工作 | 本场产生 2 条待确认候选 | 历史真机报告 |
| 09-15 长对话/长回答合并改造 | 原生 provider 禁用旧采集入口，切换 canonical；显式 final 曾映射 interim | 完整 owner turn 无法封存，阻塞连续投递前缀 | 修改报告、前后源码片段及已有隔离反例 |
| 09-16 L1-A | 修正严格 finality、停止前排空等 | 恢复合格 final 的本机封存 | 当时修复报告与当前代码；不等于服务端链已通过 |
| **09-16 L1-B，11:35:24 北京时间** | **在长场新鲜授权修复中加入哈希与 UUID 相等条件** | **正常生产身份无法构造 start/append/end/ACK 的 authority** | 原始 apply_patch、成功回执、当前源码、此次隔离反例 |
| 09-17 非 ASR/覆盖摘要修复 | 排除非 ASR 假成员，独立发布覆盖摘要变化 | 改善真实采集及 partial 展示 | 当前真机正文 complete/partial 分支证据 |
| 09-17 saving 修复 | 允许特定失败 GET 重读；关闭期限后退出 saving | 未修 authority 拒绝；最终仍有正文排队、无本场候选 | 最新真机 FAIL 与当前实现 |

09-15 的具体入口变化见 [长回答交付报告 §2.3](/Users/gaominge/Documents/liftora/outputs/2026-09-15-dreamjourney-live-continuous-capture-playback-fix/run-2026-09-15-01/reports/2026-09-15-DreamJourney-Live长回答朗读中断-本地交付报告.md:27)。此前保存的 [源码对照反例结果](/Users/gaominge/Documents/liftora/outputs/2026-09-16-live-persistence-regression-audit/live/probe/comparison-results.json) 显示：同一 `ASRFinal=true` 输入，旧 provider owner capture 次数 1，改后旧入口为 0，新 canonical finality 为 interim。该反例是源码片段对照，不是真机重放。

09-16 引入点对应 [持续保存修复报告 L1-B](../../../outputs/2026-09-16-dreamjourney-live-capture-continuous-save-fix/run-2026-09-16-01/reports/2026-09-16-DreamJourney-Live采集回归与持续保存-本地修复报告.md)。本轮从相关开发记录找回了原始补丁，时间为 `2026-09-16T03:35:24.661Z`，成功回执为 `03:35:31.389Z`：

- [引入补丁，只供对照，不是修复补丁](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-persistence-root-cause-audit/authority-guard-introduction.patch)
- [补丁成功回执](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-persistence-root-cause-audit/authority-guard-patch-result.json)

这些修改仍位于未提交工作树中，不能把它们编成一个不存在的 Git commit。当前 iOS HEAD 是 `11d0d0051b9be3cce57822dd059472d1e2536866`。09-12 真机版本另有其报告中的 HEAD 和二进制指纹，不能混称同一构建。

### 2.3 最新真机事实

来自 [saving 修复后的真机报告](../../../outputs/2026-09-17-dreamjourney-live-saving-fix/device-retest-2026-09-17-01/reports/2026-09-17-DJ-LIVE-SAVING-01-真机复测报告.md) 和 [脱敏观察](../../../outputs/2026-09-17-dreamjourney-live-saving-fix/device-retest-2026-09-17-01/evidence/live-session-sanitized-observation.md)：

- 有声音；owner 与 assistant canonical complete，`deliveryCount=2`、`partialTurns=0`、`registeredMembers=2`。
- 手动停止时 `persistedOwnerTurnCount=0`、`queuedTurnCount=2`，随后出现 `existingUseCaseNotSafelyResumable`。
- 约 15 秒期限后 saving 变为 syncPaused，用户看到本机保存/联网同步提示。
- 候选真实 GET 成功，45 → 45，本场测试标记候选为 0。
- 冷启动只有旧 workflow 进入 read-only plan；新增 blocked workflow 未进入计划。新增场的关联来自前后数量和脱敏 hash 对照，保留“关联推断”属性。

**`persistedOwnerTurnCount=0` 不能解释为“本机没有正文”。** 当前代码在服务端回执被接受、对应本地投递记录确认后才增加这个计数，见 [EchoViewController.swift:2317](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2317)。本场证据支持“本机已封存正文，服务端确认计数仍为 0，队列未清空”。

### 2.4 必须保留的证据边界

- 24 位摘要与 UUID 的不兼容是已验证的代码缺陷，不是仅凭界面猜测。
- 最新真机没有完整记录最早失败时的 authority 拒绝理由，不能声称此次已经抓到了手机上的 `qaOnlyDisabled` 原始事件；源码反例证明该生产路径必然阻断，并且与现场后续状态一致。修复时需在组合测试及后续最小真机中贯穿验证。
- 更早那次“45 秒 saving，重启后见 pendingReview”的记录没有完整同场 batch 关联，不能倒推它当时已经在服务端成功，也不能无证据把它与最新场定为同一现场根因。
- 另一次用户说到最后几个字时停止，真实 partial 展示仍为 **PASS**；未观察到无正文→partial 的瞬时切换仍为 **NOT_OBSERVED**，不是新缺陷。
- 历史 blocked workflow、候选数量、旧 pendingReview 都不能替代本场保存证据。

## 3. 当前生产代码的确定阻断

### 3.1 两个同名概念来自不同身份域

生产 [FeatureGateService.accountGeneration](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:972)：

```swift
let source = BackendAuthSessionStore.shared.currentSession?.sessionId
    ?? UserManager.shared.currentUser?.id
    ?? "anonymous"
return SHA256.hash(data: Data(source.utf8))
    .map { String(format: "%02x", $0) }
    .joined().prefix(24).description
```

该生产算法在当前 HEAD 的原有版本中也是 24 位摘要。新增的是后面的错误消费者，不是生产生成器最近突然改变格式。

[AccountLease.generationId](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/App/AccountLease.swift:22) 是账号运行时的 UUID。`AccountLeaseRuntime.capture` 从当前 `AccountSession` 复制它；并不是 FeatureGate 摘要。

新 [authority 构造条件](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:10350)：

```swift
let expectedGeneration = accountLease.generationId.uuidString
guard featureDecision.feature == .echoTextInput,
      featureDecision.purpose == .request,
      featureDecision.allowed,
      featureDecision.accountGeneration.caseInsensitiveCompare(expectedGeneration) == .orderedSame,
      featureDecision.expiresAt.map({ $0 > now }) ?? true,
      operationGeneration > 0,
      !trace.isEmpty else { return nil }
```

24 位摘要不可能等于带连字符的 36 字符 UUID。大小写转换、重新联网、再刷新同一政策，都不能解决这种合同错误。

### 3.2 从首次写入到假性等待的传播链

实际调用顺序如下，源码锚点均为本轮工作树：

1. [ensureNaturalInputSession](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2031) 创建 Live `NaturalInputUseCase`。
2. [start/current-session](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:14777) 先读取本场；成功且确实没有会话，才进入 `startNewSession`。
3. [startNewSession](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:14785) 先通过 `willPrepareStartCommand` 把原命令写入磁盘，标记 `preparedNotExposed`。
4. [withLiveRequestAuthority](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:15392) 获取新鲜 decision；错误比较使 authority 为 nil。若 decision 本来 allowed、未过期且 reason 非过期恢复原因，转为 `.unavailable(.qaOnlyDisabled)`，没有调用成功回调，也不会进入 start POST。
5. 协调器收到非 unknown 的暂停结果，转为 syncPaused。本地 canonical 仍可以接收并封存两个完整 turn；这解释了“能说、能听、正文在本机，但候选没有”。
6. 停止写入 close intent 后进入 saving；[当前新增恢复函数](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:14735) 只接收 `.requestFailed/.requestNotSent`，不接收 `.qaOnlyDisabled`，所以 `existingUseCaseNotSafelyResumable`。
7. [advanceNaturalInputPipeline](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2220) 要求 useCase ready 才能投递；队列未清空、服务端 owner 确认数为 0 时，end 也不会执行。
8. 最后由 deadline 转 syncPaused。此过程既没有把正文投递出去，也没有进入 ACK/admit/候选生成。

这里 `.qaOnlyDisabled` 还错误地掩盖了失败原因：这是身份合同不兼容，不是需要为生产打开 QA。**不得用启用 QA、放宽权限或刷新重试循环解决。**

### 3.3 受影响调用方

- start/append/end typed write 均消费同一种 authority，见 [BackendClient.swift:11843](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:11843)。
- ACK 的 [withFreshRequestAuthority](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:11133) 也构造同一类型；只打通 start、不验证 ACK，会在下一阶段继续失败。
- admit 使用其既有授权路径，不能凭上述缺陷宣称它也有同一构造错误；应在同一场完整链中验证其输入、回执和状态读取。
- BackendClient 在 typed write 边界已经验证 subject、vault 和运行时 lease；[requestJSON](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:16705) 还会按真实 FeatureGate 重验 decision。修复应复用这些合法检查，而不是绕过它们。

## 4. 本轮反例和过去绿测为何漏检

### 4.1 此次实际执行的隔离源码反例

[运行脚本](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-persistence-root-cause-audit/run_authority_probe.py) 从当前文件提取原样生产代码片段：FeatureGate generation 生成器、AccountLease、FeatureDecision、authority 构造、withLiveRequestAuthority、失败迁移及关闭恢复函数。外围使用合成账号、有效 lease 和允许策略，不读真实凭据，不联网。

实际结果见 [authority-probe.stdout.json](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-persistence-root-cause-audit/authority-probe.stdout.json)：

| 同一源码反例 | 生产生成器：24 位摘要 | 既有测试方式：注入 lease UUID |
|---|---:|---:|
| decision.allowed | true | true |
| authority 后写入边界 spy 次数 | **0** | **1** |
| 最终 phase/notice | unavailable / qaOnlyDisabled | starting / none |
| 策略刷新次数 | 0 | 0 |
| 关闭时重读次数 | 0 | 0 |

命令：

```sh
python3 /Users/gaominge/Documents/liftora/outputs/2026-09-17-live-persistence-root-cause-audit/run_authority_probe.py
```

程序正常退出；断言证明缺陷和测试输入偏差。**spy 次数不是 HTTP POST 数；这是源码合同反例，不是完整 Controller/Backend、模拟器、真机或产品持久化 PASS。** [source-manifest.json](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-persistence-root-cause-audit/source-manifest.json) 保存片段行号与哈希。

### 4.2 已有组合测试替换了关键生产输入

[saving 完整链测试](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:3239) 和 [逻辑 20 分钟测试](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:3734) 均包含：

```swift
FeatureGateService.makeQATestService(
    accountGeneration: { lease.generationId.uuidString },
    policy: { _, _ in policy }
)
```

虽然 Controller、Store、BackendClient 或 evaluator 是真实对象，关键身份值却恰好被注入成错误 guard 要求的 UUID，因此永远不会触发生产不兼容。

修复测试必须注入**认证输入与时钟、网络响应**，让 production generation 计算和权威验证照常执行。不能在 fixture 里重新手写一遍 hash 算法当作真实生成器；应让生产函数接收合成认证源，或增加仅用于依赖装配的窄入口，复用同一生成实现。也不能将所有测试统一改成 UUID 后继续宣布绿测证明真机。

原有 finality、音频、覆盖摘要和账号隔离等独立测试仍有其证明范围，不因这个遗漏全部作废。但“完整真实保存链已通过”的结论需要重验。

## 5. 冷启动问题及 localOnly 的正确解释

### 5.1 localOnly 不代表磁盘坐标不存在

[closingProgressDeadlineExceeded](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2589) 的日志是：

```swift
let outcomeUnknown = hasUnknownClosingWriteExposure
"coordinates": outcomeUnknown ? "exactOrPending" : "localOnly"
```

这里根本没有读取磁盘来判断有无坐标。一个已持久化 start 原命令、状态为 `preparedNotExposed` 的场次，也会打印 localOnly。因此不能把该字段当成“原命令丢失”证据。

### 5.2 未进入 end 的合法中间阶段，没有被冷启动扫描充分表示

当前 `willPrepareStartCommand → outbox.prepareStart` 已保存 start；而 completion checkpoint 到 [willSendEndCommand](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2123) 才 `prepareEnd`。

首次写入被挡住时：本机有正文和 close intent，但尚未走到 end，缺少 end completion checkpoint 是自然结果，不能自动解释为被删除。冷启动扫描却把 [无 checkpoint/follow-up 的 closing outbox](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:4372) 直接分类成 `closingOutboxMissingCheckpoint`，不按其中的 preparedStart/投递事实建立精确阶段恢复计划。

这是独立的 **DJ-LIVE-PERSIST-01-R：end 前已落盘工作流的恢复发现缺口**。它解释了为什么被前一个问题挡住的新场在重启后仍不能推进；它不是新场第一次 POST 被拒的原因。不得为消除 blocked 日志而提前伪造 end checkpoint。

### 5.3 历史 UI 覆盖是第三个问题

旧场 pendingReview 覆盖当前失败场，只改变用户看到的结果，不能让 start POST 变为成功，也不能生成本场候选。本轮记录它，保留之前独立问题，不在权限修复中混改历史 UI 仲裁。

“文字回响”入口表示当前麦克风/文字输入交互可用，不是持久化提交回执；其存在不能单独说明写入成功或失败。此次不通过隐藏入口、改音频或新增状态掩盖数据链问题。

## 6. 修复 A：先恢复当前场真正写入，保持局部修改

这是应先交给 Sol 实施和验证的部分。**以一场全新、正常完成的 Live 进入本场待确认记忆为首要目标。** 不混入恢复旧积压、历史 UI 仲裁、B7、音频或新的产品状态设计。

### 6.1 修正身份验证边界

两个身份都要验证，但必须分别对自己的权威来源验证：

- FeatureDecision 的 accountGeneration 对当前 FeatureGate 生成值验证；政策版本、期限、feature、purpose 和 allowed 继续验证。
- AccountLease 的 subject/vault/generation/generationId/authorityEpoch 对 AccountLeaseRuntime 验证。
- 同一次请求把经过上述检查的 decision、lease、operationGeneration、trace 绑定。真实认证主体、目标 vault 与 lease 必须匹配。
- 在取得 decision 的前后以及请求真正发出前，检查账号运行时与操作归属仍有效；回调提交继续检查原 lease/operation。若认证上下文变化，丢弃旧授权并按现有账号生命周期规则处理，不能拼接 A 账号 decision 和 B 账号 lease。
- 同账号合法凭据刷新、政策 TTL 更新与换账号必须按既有规则区分；不要再次引入“长场中的任何更新都使保存永久失效”的限制。

优先复用 `FeatureGateService.revalidateServerPolicyManagedRequest`、BackendClient 的 decision 重验及 `AccountLeaseRuntime.validate`。可在现有 client/request-authority 接缝提供一个窄工厂，返回经过验证的 authority 或明确失败原因；不要在各个 UI/UseCase 重复实现身份算法。

该工厂应统一服务 start/append/end 和 ACK 的现有调用方。删除错误的跨身份域比较，是修复中的必要变化；但**不能只删 guard、同时失去 FeatureGate 当前代次和账号切换的校验**。也不要把全局 FeatureGate 改成 UUID、截断 UUID 或取消 lease 检查来迎合这个错误。

### 6.2 保持原业务命令和曝光边界

正常路径必须恢复为：

```text
真实 SDK 合格正文
  → canonical/outbox 原子持久化
  → 同场 current-session 读取
  → 原 start 命令持久化并取得新鲜 authority
  → start POST 成功及同场回执绑定
  → 顺序 append owner/assistant，按回执确认连续 watermark
  → 手动 close intent 排空合格已登记事件与未投递正文
  → end(lastClientSequenceNumber) 及同场 review batch
  → ACK → admit → Source/effect/Worker
  → 本场状态 GET + 本场候选真实读取
  → 当前页面提交 pendingReview
```

正常会话不应等到用户停止才首次尝试建立服务端会话；持续投递按现有设计进行，停止负责排空和结束。

仅在 `preparedNotExposed` 或 transport 有明确 notSent 证据时，可以取得新鲜权限后对**原命令进行首次有效发送**。曝光边界后的 unknown/mayExpose 仍只能先做同命令精确只读核实；404、超时或无结果均不能自动变成“未发送”。start、message、end、ack、admit 的身份和幂等语义不变。

不要把 `.qaOnlyDisabled` 一律纳入重试，也不要重建新 session 来绕过失败。权限错误需要在生成端修正；真正的 deny 保持拒绝。失败诊断可用内部原因区分政策拒绝、过期、账号变化、authority 合同错误、磁盘失败、已曝光未知，统一映射到已有适当 UI 状态。

### 6.3 本轮代码范围

预期核心落点是 `OwnerTruthContracts.swift` 中的 authority 及其调用方、`DreamJourneyBackendClient.swift` / FeatureGate 的窄验证接缝和对应测试。若证据要求补 Coordinator 内部失败原因传递，只做为真实保存服务的必要变化。

保留 canonical finality、非 ASR 过滤、停止排空、partial 覆盖摘要、独立摘要观察、轮次隔离和音频已修机制。不能恢复旧/新两条采集入口同时写入。第一项是 iOS 授权合同错误，当前没有证据要求修改后端业务合同或部署后端。

## 7. 修复 R：另行处理 end 前磁盘恢复，不与 A 同时展开

修复 A 能恢复新场正常保存，但不能自动修复已经留下的本机积压。A 的代码和最小闭环通过后，再单独处理恢复接缝；本轮分析不操作旧记录。

后续恢复设计应从实际 outbox 原字段重建阶段，不能以“没有 completion checkpoint”覆盖全部判断：

| 真实磁盘事实 | 允许的恢复行为 | 禁止的做法 |
|---|---|---|
| 有正文/close intent、原 start 明确 preparedNotExposed | 识别为未完成工作流，保留准确身份；冷启动先只读核对；后续在明确继续同步的授权与新鲜账号权限下发送原命令 | 假造已 end/admitted；重建另一场 |
| start/append 已 mayExpose 或 unknown | 依据原命令/消息身份读取准确服务端状态；只确认命中的已提交部分 | 因 current 返回空或读超时而重发 |
| session 已创建、部分正文已确认、剩余明确未曝光 | 恢复同场连续序号和待投递尾部；继续同步时保持原消息身份 | 从 1 重新编号、再次投递已确认内容 |
| end/ACK/admit 已有合法 checkpoint | 复用现有本场恢复链，读已发生结果、只推进有明确执行权的下一步 | 回退 phase、重复业务 POST |
| 旧记录字段不足或账号/vault 不匹配 | 保留、准确报告不能安全恢复，等待单独处理方案 | 猜测未发送、清除记录或强制成功 |

冷启动发现工作与发送业务写必须分开；本轮及后续默认只读冷启动不得自动重放未知写。不要为了此恢复设计修改历史 UI 选择优先级，先证明该场有可归属的独立结果。

## 8. 先红后绿：真实链测试与验收门槛

### 8.1 必须先出现的红例

在修复前固定代码指纹，构造合成认证 session 和独立的 AccountLease UUID；真实 FeatureGate 通过生产算法生成 24 位身份，策略 fresh/allowed，QA 旁路关闭。current-session 受控 GET 正常返回本场不存在，不能把这里故意制造成网络失败。

驱动真实 Manager/Controller → Coordinator → 临时磁盘 Outbox → NaturalInputUseCase → BackendClient → FeatureGate → 受控 transport，输入完整一问一答，再真实执行停止路径。断言预期为 start/正文/end/ACK/admit 成功和本场候选；修前应因 authority 不兼容在 start 发出前失败。保留实际失败断言、请求计数和磁盘快照，不能仅靠源码字符串搜索充当红测。

修后以**相同输入、相同断言**通过。测试必须走实际发送层和 BackendClient 的 feature revalidator，不将 authority、receipt、phase 或 UI 状态直接塞到后半段。

### 8.2 修复 A 必测矩阵

| 编号 | 场景 | 必须检查的真实结果 |
|---|---|---|
| P01 | 生产身份源 + 有效独立 lease + 新场一问一答 | current GET 正常；start=1、append=2、end=1、ACK=1、admit=1；完整正文在请求中；同场响应进入真实 decoder/Controller |
| P02 | 正常一问一答立即手动停止 | close intent 先落盘；排空后 end watermark=2；不靠 deadline 达成成功；原地可见本场 pendingReview |
| P03 | ACK 单独使用生产身份源 | 从真实 ended batch 按正常流程取得 authority；ACK 确实发送并被同场回执确认，不只测试 start |
| P04 | 逻辑 20 分钟、多次政策 TTL 更新 | 同 productSession、单一 start；首/中/末正文均 append；每次业务写采用当时可用权限，最终 end watermark 连续正确 |
| P05 | 取 decision 与发送之间换账号/换 vault/旧 lease | 不发送跨账号请求；旧结果不能落盘到新账号或更新新场 UI；包括同主体重登导致原 lease 无效 |
| P06 | 同账号合法凭据刷新、政策变更/明确 deny | 旧 decision 按真实 revalidator 失效，新 decision 在允许条件下继续；明确 deny 零业务写、保留队列，不循环刷新 |
| P07 | start/append/end/ACK/admit 发送前拒绝与曝光后断连 | 分别核对 notSent/unknown；unknown 精确只读；重试不增加未知 POST，不删除坐标 |
| P08 | admit 后 queued/processing，随后迟到 pendingReview | 本场私有读取继续到终态；UI 正确更新；不会额外 ACK/admit；迟到旧 round 不能取消新 poll |
| P09 | 状态读取超时、之后前台/页面重进 | 超时诚实退出等待；恢复仍用原 batch，只读核对；没有固定 sleep 假成功或无限轮询 |
| P10 | A 场结束后开启 B，A 的迟到 read/callback | A 不能取消 B poll、抢 B 状态或触发新写；保留已有 S01-08 隔离规则 |
| P11 | 页面离开再进入、Controller 重新创建 | 场次后台责任与观察者重绑清楚；同场状态立即可读；不会仅因旧观察者销毁遗失保存链 |
| P12 | 已有 ended/admitted 坐标冷启动 | 只读恢复到对应 batch；零重复业务写；本场结果不靠全局历史扫描碰巧提供 |
| P13 | partial 和非 ASR 保持性 | 既有正确 partial 场景仍通过；没有把未完整正文伪装完整；非 ASR 不占 owner 槽位 |

P01 中的确切调用次数针对受控“新场、无故障、1 owner + 1 assistant”场景，不作为所有业务模式的通用次数规则。P08/P12 的只读成功不表示候选已被用户审核成正式记忆。

### 8.3 修复 R 的独立测试

使用真实临时磁盘 Store 创建各阶段记录后，销毁 Controller/Coordinator/Store 内存实例，重新从磁盘构造，禁止把内存 receipt 传给恢复实例。

- prepared start + close intent + 2 条正文、无 end checkpoint：应被按真实阶段发现，而非笼统判成坐标缺失；首轮只读、零业务 POST。
- start 已曝光无回执：只读命中原 session 后绑定；读空/超时不重发。
- append 一条已确认、一条明确未曝光：确认序号不回退，授权继续时只投递原尾部。
- end 已提交但 ACK/admit 未完成或未知：遵循对应原命令曝光边界。
- 记录损坏、缺失旧字段、scope 不匹配：保留真实 blocked，不能把兼容性不足解释成成功。

R 不通过时单独保留恢复缺陷，不能将 A 的正常新场成功扩张成“历史数据也恢复”。

### 8.4 证明服务端真的保存：不能停在 URLProtocol 假响应

受控网络组合测试证明客户端调用与状态传播，不证明后端真的落库。此前已经多次出现本地全绿而实际无候选，因此下一次完整验收必须补一条隔离环境的真实保存链：

1. iOS 真实 BackendClient、生产身份算法、受控合成认证接入隔离本地 API；政策在隔离环境真实获取和验证，不开启客户端 QA 旁路。
2. 调用现有 start/messages/end/acknowledgement/candidate-proposal/admit 路由，不改后端合同来迁就客户端。
3. 使用隔离 PostgreSQL 的真实 repository/transaction/Worker，核对 conversation thread、session、两条正文、连续序号、ended review batch、ACK、admission、Source/effect 和本场 candidate 关联。
4. 仅外部模型服务可按测试分层替换为确定性端口，必须明确记录；Worker、候选写库和查询不可用预置 pendingReview JSON 替代。外部模型替身通过不称真实 Provider 已通过。
5. API/Worker 或读取进程重建后仍能从该隔离数据库查询同一内容和候选；正式记忆审核不在本轮范围内。

现有实现入口可从 [后端真实路由](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:11054)、[Postgres 会话 repository](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_conversation.py:2054)、[admit 写 Source/effect](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_interview_candidate_proposal.py:289) 复用。本轮只读取了这些入口，没有运行该集成或查询生产数据库。

若隔离数据库、认证或 Worker 配置不具备，必须列为 NOT_RUN/BLOCKED，不以 memory store、源码检查或 URLProtocol 绿测冒充。先完成客户端修复和可执行验证，再明确剩余环境项；不要因此转而操作生产数据。

### 8.5 后续真机的最小顺序

本轮不安装或启动真机。Sol 本地交付后，再进入已另行授权的真机验收：

1. 一场新的完整一问一答，使用明确的新合成事实，避免纯提问造成合法 noChange；正常听完再停止。
2. 同场记录生产 authority 接受、start/append/end/ACK/admit 的各阶段、服务端连续序号、batch/source、Worker 结果及候选读取。关联 ID 只保留安全 hash；日志不打印正文或凭据。
3. 当前进程原地进入本场 pendingReview；用户只读核对本场候选。不能只看总数增加、旧 pendingReview 或提示消失。
4. 以上成功后，才做真实 20 分钟跨 TTL，核对开头、中段、末段合格正文和相应候选；再做停止边界/页面重进/冷启动。
5. 如首场仍有队列未投递，停止扩展场景，按第一个缺失里程碑追查；不重复开场堆积未完成任务。

一般内容可能正确地得到 noChange；但为检验“能生成候选”专门选择的新事实用例若无候选，不能拿 noChange 或音频正常替代该用例的成功。不要修改 B7 过滤来制造候选。

## 9. 诊断与完成条件

补足少量内部安全诊断即可，不新增用户状态：

- authority 申请/接受/拒绝：调用阶段、具体 reason、feature、purpose、策略是否过期、lease 验证结果。
- 身份域仅记录来源类型/长度和是否经各自来源验证，不打印 session/token/原始账号代次。
- 业务阶段记录 original command/workflow 的安全关联、prepared/exposure/outcome、HTTP 是否实际发出、decoder 是否接受同场回执。
- 本地记录是否有 preparedStart、session receipt、end checkpoint、follow-up 必须分别从真实字段读取；不要再用 `localOnly` 一个推导标签代替全部事实。
- 当前进程保存失败原因与冷启动 plan 被拒原因分别记录；历史 UI 覆盖单独归档。

修复 A 的交付至少应有：生产身份红绿反例、真实 Controller/Coordinator/磁盘/BackendClient/FeatureGate 组合证据、既有账号/unknown/音频保持性回归、构建、差异检查和新源码指纹。完整服务端集成、真实 SDK、物理 20 分钟、手机测试分别列明。

**产品目标只有在正文真实保存且本场候选可读后才算通过。** 下列情况均不能标“问题已解决”：只是 saving 退出；只是本机有正文；只有模拟响应 pendingReview；只有几百条单测通过；只读到了历史场成功；跳过了生产身份生成器。

建议交付状态以普通说明为主：“授权阻断已本地修复，真实保存链/真机待验证”，并附逐层表。不要再仅沿用 `LOCAL_PASS` 总标签让它看起来等于完整持久化通过。

## 10. 本轮产物与下一步

- [分析目录与复现证据](../../../outputs/2026-09-17-live-persistence-root-cause-audit/README.md)
- [当前源码指纹](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-persistence-root-cause-audit/workspace-baseline.json)
- [原 saving 设计，保留历史，不作为此次根因优先级](2026-09-17-Astra-DJ-LIVE-SAVING-01-会后保存停滞问题分析与局部修复设计.md)

下一步先实施本文件修复 A，恢复新场真实写入。修复 R 和历史 UI 仲裁分别处理，不能合成又一轮全状态改造。保留所有现有正文、恢复记录和曝光事实，不清理、不重放未知业务写。
