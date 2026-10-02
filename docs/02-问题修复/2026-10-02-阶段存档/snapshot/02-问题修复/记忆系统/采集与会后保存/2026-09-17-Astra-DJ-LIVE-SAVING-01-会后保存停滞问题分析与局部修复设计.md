# DJ-LIVE-SAVING-01：Live 会后长期停留“正在保存本次对话”

问题分析与局部修复设计 · 2026-09-17 · 供 Sol 分步开发

## 1. 结论与交付状态

独立问题编号：**`DJ-LIVE-SAVING-01`**。

现象是：完整 Live 会话手动停止后，页面至少 45 秒停留“正在保存本次对话”，没有给出完成或暂停原因。本问题独立于非 ASR 误登记、覆盖摘要刷新和历史恢复任务争用页面。

当前代码存在一个明确可达的推进缺口：**同步已经暂停时，停止仍会发布 `.saving`，但关闭交接仍依赖原 NaturalInputUseCase 处于 `.ready`；原用例如果已失败，创建逻辑又因用例非空而返回，随后可能没有请求、回调或有效截止时间负责让 saving 结束。**现场的 `syncPaused → saving`、队列仍有两条 delivery、服务端确认数为零，与这一分支相符。它应作为第一优先级反例，而不是把 admission、轮询或 UI 回调直接定为现场唯一根因。

重启后出现 pendingReview 文案，尚不能证明本场在停止时或那 45 秒内已经到达 pendingReview。需要补齐本场 `productSessionID → thread/session → batch → read result → UI` 的同一性证据。已有日志中还包含历史 workflow 的 pendingReview，不能借此判定历史任务就是本场根因。

本次交付状态：**`ANALYSIS_COMPLETE / DESIGN_ONLY / FIX_NOT_IMPLEMENTED / NEW_TESTS_NOT_RUN`**。本次只读审查记录及代码，输出设计和脱敏证据索引；没有修改业务代码，没有运行新增测试、安装手机、访问生产、清理历史或 commit/push。本问题尚未关闭。

## 2. 阅读材料、代码基线与证据边界

已完整阅读用户指定的三份材料：

1. [覆盖摘要 UI 刷新补充修复报告](../../../outputs/2026-09-17-dreamjourney-live-nonasr-capture-fix/run-2026-09-17-02/reports/2026-09-17-DreamJourney-Live覆盖摘要UI刷新-补充修复报告.md)。
2. [最小真机观察记录](../../../outputs/2026-09-17-dreamjourney-live-nonasr-capture-fix/run-2026-09-17-02/evidence/device/2026-09-17-minimal-ui-refresh-observation.md)。
3. [D1-01 至 D1-06 校正版执行清单](../../../outputs/2026-09-17-dreamjourney-live-nonasr-capture-fix/run-2026-09-17-02/reports/D1-01-D1-06执行清单-校正版.md)。

审查工作区：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`。HEAD 为 `11d0d0051b9be3cce57822dd059472d1e2536866`，分析包含工作区尚未提交的修改，不能只用 HEAD 复现。

| 当前文件 | SHA-256 |
|---|---|
| EchoViewController.swift | `58fd0f38452c113f04c23542844bafc47d0911d0f30437f0d809ed664a91ef9a` |
| OwnerTruthContracts.swift | `7ad24e61a794e65d73864ae4b147eae95e3dc224c7b403505500afbbbc919b30` |
| DreamJourneyBackendClient.swift | `3014beeb51af7a7d40186a1991deccdaff56d746f1abdb0d9b9be385089578f6` |
| OwnerTruthContractsTests.swift | `e5c32ef7714af45df65c47e6fb898c9d4d6e3e5c36d759fb43d0914954451115` |

补充审查使用了 Sol 当次工具执行中已经保存的控制台 stdout，未重新连接手机取证。原目录的 console 文件只有启动记录，不能当完整日志使用。已从既有执行记录提取白名单诊断字段，正文和实际身份标识不进入本分析证据；同一标识在两个导出中使用同一别名。

- [第一次尝试的安全诊断导出](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-saving-analysis/attempt-1.safe.log)。
- [第二次启动及尝试的安全诊断导出](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-saving-analysis/attempt-2.safe.log)。
- [导出来源、原 stdout 摘要与提取位置](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-saving-analysis/provenance.json)。

导出中的 `Lxxxx` 是原 stdout 行号，不是时间戳。保存时间也不是单条事件发生时间。“至少 45 秒”来自真机观察，不是据这些行号算出的时长。导出不是完整网络审计；某事件未出现，只能记为“本段日志未记录”。

## 3. 两次现场必须分别判定

### 3.1 第一次：本问题的异常样本

| 已证实的事实 | 可以说明什么 | 不能推出什么 |
|---|---|---|
| 用户正常完成一轮并听到 AI 回答，随后手动停止 | 本次应走完整会话的会后保存路径 | 听到声音不等于后台会话已建立或正文已同步 |
| saving 文案至少 45 秒不变；文字回响入口同时可见；无报错、无持续转圈 | 页面没有及时给出保存进展或失败收尾 | 无 spinner 不能证明没有请求；有文字入口也不能证明保存已完成 |
| `registeredMembers=2, membersWithoutBody=0, partialTurns=0, hasPersistedLocalText=true` | 当前 canonical 快照有正文，没有本轮覆盖缺口 | 本地正文持久化不等于服务端确认、admit 或候选生成 |
| 原 stdout L1398：`deliveryCount=2, serverConfirmedCount=0` | 两个回合已经形成可投递记录，尚未记录服务端确认 | 不能把 deliveryCount 当成服务端成功计数 |
| L1440：`syncPaused → saving; ownerTurnCount=1; persistedOwnerTurnCount=0; queuedTurnCount=2` | 停止时已有同步暂停，队列仍待处理 | 这里的 persistedOwnerTurnCount 是服务端 receipt 后本地 acknowledge 路径维护的计数，不是“磁盘无正文” |
| L1443：`closeIntentPersisted`，完整覆盖摘要仍成立 | 用户关闭意图已经持久化 | 不等于 `markClosing(N)` 已完成，更不等于 end/ack/admit 已完成 |
| 重启后只读恢复显示“上次对话已进入待确认记忆” | 某次只读恢复能得到 pendingReview 并显示 | 未核对同场绑定前，不能证明那就是本场；也不能倒推它在停止时已经完成 |

更早的 L0283 已出现 `.live → .syncPaused`，当时 owner 与待发送计数尚为零。因此应首先审查 current-session/start 等早期失败如何留下暂停用例，而不是只从最后一步轮询倒查。暂停发生的具体 transport reason、用例 phase、start 暴露状态没有在这段安全日志中完整记录，仍待组合反例区分。

### 3.2 第二次：正确的 partial 分支

本次必须保持以下独立结论：

| 验收项 | 状态 | 理由 |
|---|---|---|
| 停止时仍在说最后几个字，显示“已保存收到的内容，部分内容尚未完整记录” | **PASS** | `registeredMembers=1, partialTurns=1, membersWithoutBody=0, unsealedTurnCount=1, hasPersistedLocalText=true` 与页面一致 |
| 15 秒内文案稳定、无报错 | **PASS** | 真实未完成内容没有被伪装成完整保存或错误空会话 |
| 肉眼捕获“无正文文案 → partial 文案”瞬时变化 | **NOT_OBSERVED** | partial 已在页面首次显示前落盘，不是刷新失败 |
| 相同 coverageGap 数量下，仅覆盖摘要变化驱动页面刷新 | 保留原本地红绿证据 | 真实 Controller、Coordinator、磁盘测试已证明该变化；本次真机未观测不否定本地结论 |

原清单的 D1-DEVICE 单元格尚写整体 NOT_RUN，而后续报告已细分 partial PASS 与精确切换 NOT_OBSERVED。本设计按较新的细分证据引用，不将清单旧单元格解释为 partial 失败，不改动原文件。

原报告的 1/1 定向通过、14/14 回归通过及此前完整回归均为已有交付证据，本次没有重新执行或扩展其证明范围。

## 4. 当前真实调用链

下表中的行号对应上面的工作区指纹。后续代码变化时应按函数重新定位。

| 环节 | 当前真实路径 | 推进条件与结果 |
|---|---|---|
| 手动停止的保存入口 | [finishLiveMemoryCaptureIfNeeded](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:12819) | 关闭 canonical ingress，清空 active capture 指针，刷新文字入口，再调用 coordinator.finish。保留字典仍持有该 coordinator |
| finish 与关闭意图 | [finish](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1721)、[persistCloseIntent](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1969) | 标记 finishing；完整分支置 saving；等待停止前已登记的 handoff 与磁盘写排空；requestClose 落盘后更新摘要并尝试关闭水位 |
| 关闭水位 N | [persistCloseRequestIfReady](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1927) | 有 close intent、无待落盘/冻结交接/覆盖缺口、有 owner 后 markClosing(N)；完成后 ensure session、advance pipeline |
| 后台自然输入会话 | [ensureNaturalInputSession](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2018) → [NaturalInputUseCase.start](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:14731) | 仅在用例为 nil 时创建；先 current-session GET，缺失且合同允许时才准备并发送 start；所有命令保持原 productSession 和持久化身份 |
| 连续投递 | [receiveNaturalInputState / advanceNaturalInputPipeline](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2097) | 必须 phase=ready、无 inflight/ack 落盘；preparedNotExposed 回合依序提交；未知暴露转精确只读 delivery-status |
| 服务端确认回合 | [acknowledgePersistedTurn](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2238) | receipt 匹配后本地 acknowledge，移除待发送项、增加确认计数，继续下一条。这里的 acknowledge 与会后 review-batch ACK 是两个不同动作 |
| end | [advanceNaturalInputPipeline](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2171) → NaturalInputUseCase.endLive(N) | 队列空、关闭水位已落盘、有服务端确认的 owner、尚未请求 end；发送前 prepareEnd，typed write 维护暴露分类 |
| ended receipt | [clearCompletedOutboxThenBeginOrganization](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2472) | 校验 ended receipt 并持久化 checkpoint；此函数名称中的 clear 不能理解为这里已删正文；随后 beginPendingMemoryOrganization 将状态置 queued |
| review-batch ACK | [beginAcknowledgement / receiveAcknowledgementState](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2522) → [AcknowledgementUseCase](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:10868) | fresh authority → pending inbox GET → 精确 thread/session 唯一匹配 → prepareAcknowledgement → ACK POST → receipt 落盘 |
| admit | [beginAdmission / receiveAdmissionState](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2596) → [AdmissionUseCase](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:11641) | prepareAdmission、fresh authority、admit POST、acceptAdmission；失败与未知保留各自暴露状态 |
| 即时读取交接 | [persistFollowUpThenBeginObservation](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2747) → [beginCandidateReadinessObservation](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2779) | follow-up 落盘后建立本场私有 RecoveryCoordinator；follow-up 写失败时仍允许依据已 admitted checkpoint 读取；不能依赖全局历史扫描来完成当前交接 |
| 只读状态及轮询 | [RecoveryCoordinator](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:3431) → [BackendClient.fetchEchoLiveMemoryRecoveryStatus](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:9403) | GET 精确 review-batch 的 candidate-proposal/status；现有每轮 30 秒预算、最多 6 次资源 GET、取消 handle、round/trace/generation 校验 |
| 当前页面提交 | [receiveSameSessionStatus](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2913) → [retainLiveMemoryCaptureCoordinator](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:12622) → [renderLiveMemoryCaptureState](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:12902) | 校验私有 reader、generation、账号与本场/batch；主队列接收 capture state 并渲染。覆盖摘要有独立观察通知，必须保留 |

真实网络/授权层也已有必要保护，不能为了让测试通过替换掉：

- [current-session GET](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:11684) 带 `productSessionId`，Live 分支已使用 `.echoTextInput` fresh decision；过期类原因允许一次 policy refresh。**不能把“没取 fresh decision”写成已经查明的根因。**
- [live-delivery-status GET](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:11580) 校验 lease/vault/session/productSession，重新取 fresh decision。
- [NaturalInput typed writes](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:11903) 与 [ACK typed write](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:12068) 经过暴露 tracker，禁止该层自动 auth/recovery 重发业务 POST。
- [bounded status read](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:9517) 对 `.liveMemoryRecoveryStatus` 取 `.ownerTruthCandidateReview` fresh decision；仍经过 auth、runtime、FeatureGate、绑定解码和原读预算。

## 5. 问题定位：已成立的缺口与待验证假设

### 5.1 优先反例 H1：暂停用例被 saving 文案覆盖，但没有重获推进能力

可由真实代码直接连成如下路径：

```text
current-session GET 或早期 start/投递发生失败（该分支未判为未知暴露）
  → NaturalInputUseCase.phase = failed/unavailable
  → receiveNaturalInputState：未 finishing 时进入 syncPaused
  → 用例仍非 nil；canonical 正文仍正常持久化
  → 用户 finish：完整覆盖分支进入 saving
  → closeIntent 和 markClosing 回调尝试 ensure + advance
      ensure：用例非 nil，返回
      advance：用例非 ready，返回
  → 没有新失败回调触发 statusUnknown，也没有 finish 级别截止时间兜底
```

这不是“没有保存正文”，而是**关闭意图已登记，后台投递与会后交接未必被继续驱动，UI 却保持正在执行的状态**。现场两个 queued turn 与 serverConfirmed=0，使该反例比“只缺最后一次页面刷新”更值得先验证。

初次 current-session GET 失败与 start 已暴露但结果未知必须分别构造。前者可有界重读同场 current；后者只可按原 start 命令和持久化坐标做精确只读核实，不能通过新建用例、新建 session 或重发 start 来求成功。

### 5.2 现有超时未覆盖所有 saving 状态

`deliveryStatusTimeout` 的 15 秒计时器只在 [verifyDeliveryStatusIfNeeded](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2283) 真正通过前置 guard 后启动，要求有可读的 session/start 坐标及相应待核实工作。没有精确坐标或根本没进入这个函数时，不会保护 saving。

`organizationStatusTimeout` 的 30 秒预算保护 admission 后的状态读取轮次，不是从手动停止开始的总交接时限。

[receiveLifecycleEvent](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:3028) 在前台恢复时处理等待整理的 reader 或 unavailable/statusUnknown 的 delivery 核实，未覆盖 `.saving` 的无工作悬停，也不直接恢复一个已失败的 current-session 用例。给 `.saving` 加到旧 if 条件里仍不够：精确坐标可能不存在，调用只读核实会再次返回。

### 5.3 后段假设要用证据分叉，不能代替 H1

| 假设 | 当前审查结论 | 决定性检查 |
|---|---|---|
| H2：end/ACK/admit 某步实际成功，但下一步未被驱动 | 可能；本场缺完整阶段证据。代码有逐步 checkpoint 与用例回调，不能只看 pendingReview 文案定位到某一步 | 按本场命令、receipt、checkpoint、暴露状态核对每一跳；受控网络分阶段悬停/迟到 |
| H3：admitted 后私有 reader 未建立或观察器丢失 | 已有明确 reader 建立、订阅、绑定检查及失败状态；不是普遍缺实现。需检查本场是否走到 admitted、是否有启动事件及订阅 | admitted checkpoint、follow-up 回调、reader identity、observer generation、首个 GET 与页面提交各自留证 |
| H4：旧轮次 poll 清除新轮次工作 | 当前已有 pollID + roundID + trace + generation 校验，校验失败后才记录并返回；现有 S01-08 不应推倒重做 | 人为晚交付同一场 A 轮 poll/GET，在 B 轮中证明零额外 GET、零预算/状态污染 |
| H5：服务端 pendingReview 已被当前进程正确读取，但页面未更新 | 待验证。当前存在 reader → capture → Controller 接线；没有证据证明本场收到且接受过 pendingReview | 精确读取结果 accepted 后，观察 capture 状态、Controller 接收、render 的同场关联；若被丢弃记录具体 guard reason |
| H6：晚到的 close/ready/摘要回调覆盖更后面的状态 | 防回归场景，尚未证明现场发生。close 回调和 finishing 时 canonical 回调有直接置 saving；ready 恢复分支有直接置 live，需检查合法调度下是否能回退 | 用真实队列可产生的延迟顺序建反例；不要强行调用不可能顺序的私有回调来制造失败 |
| H7：页面离开后观察停止、回来未续接 | 需验收；仅清 active 指针不会释放 retained coordinator；Controller 真正销毁与单纯切页要分开 | 同 Controller 切页、同场新 Controller 重新挂接、进程冷启动三种生命周期分别测试 |

正常路径在进入 ACK 之前就会把 capture state 改为 queued。因此，如果后续补证表明本场确实进入 ACK/admit，而屏幕仍是 saving，应同时检查“协调器已前进、页面未提交”的分叉；不能把页面文字直接当作后端或协调器阶段。本段安全日志没有记录本场这个 queued 转换，仍以 H1 为优先反例。

### 5.4 条件成立时，如何回答“服务端已经 pendingReview，为何本进程没显示”

只有先得到**同场**服务端状态，才能沿下面的边界逐段定位：

1. 服务端结果属于本场，但客户端还处于 unknown end/ack/admit：查看持久化命令与只读核实是否缺交接；不要把 pendingReview 改写成伪造的原写 receipt。
2. admitted 已落盘，但 `sameSessionStatusObservationStarted` 未出现：查 follow-up 回调、局部 reader 建立条件和状态收尾。
3. reader 已建立，但没有首个资源 GET：查 FeatureGate/auth/runtime 与 deadline；明确 deny 不能绕过。
4. GET 已发出但没有结果：在原读轮次截止时进入可核实状态，保留坐标。
5. 结果到达但未接受：查 account/vault/productSession/thread/session/batch、request/round/generation；旧结果被拒绝本身是正确行为。
6. 结果已接受但 capture 没更新：查私有 reader 的订阅及 observer generation。
7. capture 已 pendingReview，当前页面仍 saving：才进入 Controller 接收、可见性与渲染接线问题。

本场目前不能跳过 1—6 直接选择 7，也不能因为冷启动有效就跳过前半条持久化链。

## 6. 产品状态与明确排除项

“文字回响”由 `EchoInteractionState` 是否 idle/replied/error、产品策略和当前入口模式决定；saving 由独立的会后捕获协调器决定。手动停止释放 Live 交互后，允许开启文字输入，而上一场继续保存，在当前状态划分下并不矛盾。**同时出现这两个元素，不单独立为缺陷，也不通过隐藏文字入口掩盖 saving。**

局部验收应证明：文字入口不暗示上一场成功；打开新文字交互不会重用上一场的 productSession/命令，也不会清掉上一场保存任务的持久化坐标。历史多个任务选择谁占据全局页面，仍属原历史 UI 仲裁问题，本设计不修改其排序、选择器或恢复注册表。

以下事项排除出本次修复：

- 第二次 partial 分支、未肉眼捕获的瞬时切换；它们不是 DJ-LIVE-SAVING-01 的失败证据。
- `onCoverageSummaryChange`、摘要计算、D1 非 ASR 资格分类、冻结交接和封存规则；保留原实现和测试。
- Live 音频、TTS、主动打断、蓝牙/车机、CarPlay；本场 AI 能出声不能作为持久化成功依据，但无需改声音链。
- B7 语义过滤、正式记忆写入、候选是否应生成的语义规则。
- 历史 blocked workflow 的修复、清理和重放。日志出现 blocked 不足以证明与本场绑定。
- 直接删除恢复坐标、放宽 scope/CAS/hash/revision 校验、忽略 FeatureGate deny、重放未知业务写。

## 7. 局部修复设计

### 7.1 用事实决定推进动作，不靠 saving 枚举驱动下一步

在 `EchoLiveMemoryCaptureCoordinator` 内增加小范围的关闭推进判定，例如 `reconcileClosingProgress(trigger:)`。这是建议的局部方法名，不是要求新增通用工作流框架。只整理本场关闭后的推进责任；保留现有 end/ACK/admit 用例与 typed transport。

判定输入至少包括：本场 lease/productSession、canonical coverage、待落盘/冻结 handoff、closeIntent 与 close watermark、原 start 命令及暴露分类、回合队列/inflight/确认水位、NaturalInputUseCase phase、completion checkpoint、私有 reader/active read、当前意图的时间预算与 generation。

| 当前可证明的事实 | 唯一允许的局部动作 | UI 约束 |
|---|---|---|
| 已登记的正文/close intent 尚在磁盘队列中 | 等待已有工作并绑定完成通知；本次关闭预算覆盖遗漏回调 | 有真实工作时 saving；磁盘失败明确告知未完成，不能声称已保存 |
| canonical 有缺口 | 继续原覆盖摘要分支；只接收原来允许的迟到事件 | 维持原 partial/无正文文案，不套用 saving 超时去改写 partial |
| current GET 失败，但没有任何未知业务写、没有 active read | 对原 productSession 发起一次有界的 current 重读；完整通过授权与同场合同后，恢复原待投递工作 | 请求执行期间可 saving；失败后停止悬挂并显示真实暂停/待核实状态 |
| 原用例失败且存在 preparedNotExposed 命令 | 依据原持久化命令恢复安全发送能力；保留 ID、版本、水位、绑定 | 禁止为了让 phase=ready 而伪造 receipt 或换一个新命令 |
| start/append/end 已 mayExpose 或 outcomeUnknown | 按原精确坐标走只读核实；无坐标则明确待核实，不新建业务会话 | 不能保持没有工作负责的 saving，也不能仅因网络恢复就重发 POST |
| 队列与本地 acknowledge 均完成，原 end 尚未暴露 | 通过既有 endLive(N) 发出原关闭操作 | N 必须是完整连续覆盖水位，不跳过缺口 |
| ended/acknowledged/admitted 有权威 receipt 与对应 checkpoint | 只推进尚未完成的下一步；admitted 立即交给本场私有只读 reader | 不回退 admission、重复 ACK/admit 或等待下一次全局扫描 |
| ACK/admit 结果未知且本场 checkpoint 含精确 batch/绑定 | 为该 checkpoint 建立只读核实，不使用未知命令重放来确认结果 | 查询结果与原写结果分开记；readyForAdmission 不等于已 admit |
| 当前已有有效请求/磁盘操作/reader | 复用，不创建第二份工作，不重置原截止时间 | saving/queued/organizing 对应真实阶段 |

调用位置应覆盖 finish、close 落盘、回合落盘/确认、NaturalInput 用例状态、明确的本场核实意图，以及前台/页面重新接入。重复触发必须幂等。对每次“无法推进”的 return 给出原因和后继责任，不能只返回而继续展示 saving。

第一优先实现是 H1：在用户停止这一明确新意图下，为“早先只读失败、无未知写”恢复一次同场 current 读取。不是每次 partial/摘要回调都 retry；也不是清空 `naturalInputUseCase` 就盲目 start。若原接口不足以区分暂停阶段，给用例增加窄的恢复入口或结构化暂停原因，保持旧 operationGeneration 与暴露保护。

### 7.2 saving 必须有实际工作和有界收尾

为关闭后的未完成推进建立可注入的时间预算与调度器。建议无进展预算默认复用现有 15 秒量级；已进入 candidate-status reader 时继续沿用其现有 30 秒/6 GET 读意图预算。具体常量写入执行报告，不能静默增大到覆盖现场时长。

这里的截止时间用于判断请求/交接是否失去推进能力，**不是 sleep 后重试或 sleep 后显示成功**。每次已确认的回合水位推进、权威 receipt/持久化阶段推进可视为新进展；相同状态通知、重画页面、重复唤醒和重试本身不能反复续期。大场次真实持续进展不应因一个从停止开始的固定总时长被误报为失败。

到期处理：停止本轮自动推进、使旧回调失效；按当前暴露事实进入可解释的暂停/结果未知状态，保留所有尚需恢复的坐标。已暴露写不能因“超时”改回 notSent。后台挂起时不要求计时器执行，但回到前台必须检查已消耗预算，再决定收尾或开启一个明确的新只读意图。

对完整正文已落盘但尚未同步的分支，可使用“本次对话已保存在本机，暂未完成同步”等准确文案；没有正文落盘不能使用它。复用或补充**本场**“继续保存/核实保存状态”动作时，分别遵守 notSent 的安全续传与 unknown 的只读规则，不借用历史任务仲裁入口。第二次 partial 文案不变。

当前 `.unavailable` 被列为 capture terminal，Controller 收到后会从 retained 字典移除协调器。因此不要把可继续的暂停简单映射成 unavailable，再承诺自动续传。局部状态收尾必须明确谁继续持有本场恢复责任；不要为此全局改写历史终态或恢复仲裁规则。

不能仅把 saving 改成 statusUnknown 就交付。受控网络恢复且授权允许时，同一场必须能够从本地正文走到 end/ACK/admit/状态读取，并使当前 Controller 正常完成展示。

### 7.3 即时只读交接与单场观察器

保留当前 admitted 后的私有 reader。确认成功后，调用建立 reader 的路径必须不依赖切页、重启或历史恢复扫描。follow-up 落盘失败可以使用现有 admitted checkpoint 回退读取；计划缺绑定则明确 statusUnknown，保留磁盘证据。

对于 unknown ACK/admit，不能直接放宽 `makeSameSessionRecoverySetup` 的 admitted 守卫。若组合测试证明需要补当前进程核实入口，应使用独立的只读计划构建路径：从本场原 checkpoint 取准确 thread/session/batch，保留原 phase 和未知暴露，不合成 admission receipt，不调用 end/ACK/admit。可复用现有只读计划验证逻辑，但不进入全局历史选择器。

只读 pendingReview 的接受条件为：lease/account/vault 合法、plan 是本场、batch 与计划相符、读轮次未过期。缺 session/batch 时只能依据已有合同做同场唯一匹配；无法唯一匹配保持待核实，不能用候选总数增长或任意 pendingReview 替代。

对 terminal 结果沿用现有“先持久化观察、失败保留恢复坐标”的合同。若原写的命令结果仍未知，不能仅根据页面成功文案删除该命令/阶段证据。本修复不通过清空 outbox/checkpoint/follow-up 来消除 saving。

### 7.4 回调隔离、状态前进与页面重进

保留已有 `reader identity + observerGeneration` 和 `pollID + roundID + trace + generation` 校验。新关闭调度也需绑定同场身份及自己的意图/操作 generation。旧回调不得清除新工作、修改新预算、触发额外 GET/POST 或提交页面。

将 UI 状态看作当前真实阶段的投影。恢复 `.ready` 时如果 `isFinishing=true`，继续关闭流程，不能用 `.live` 表示重开采集。close/摘要回调是否可能覆盖更后面的阶段，需要合法调度反例证明；只对被证明可达的回退点做局部守卫，不重新设计 canonical ingestion。

保留 retained capture coordinator 的会后存活关系。页面离开且 Controller 仍在时，不能把“页面不可见”当作本场写已失败或已成功；重新可见应读取该 coordinator 当前状态/摘要，并按有效预算续接本场观察。若 Controller 被销毁，磁盘仍是恢复基础；新的 Controller 挂接同一个已选定本场计划后只读核实。冷启动同样只读，不能自动重放未知写。

本节只解决指定本场的订阅、重接和迟到回调。多个历史 workflow 谁占用 Echo 顶层提示、历史排序和跨任务 UI 归属，不在修改范围。

### 7.5 增加能区分失败阶段的安全诊断

使用现有 PrivacySafeDiagnostics，记录小量阶段事件，不记录对话正文、原始响应、token 或真实账号标识。建议事件：

| 事件 | 关键安全字段 |
|---|---|
| `closingProgressEvaluated` | 本场关联 hash、phase、trigger、useCasePhase、queue/inflight/pendingPersistence/handoff 计数、start/写暴露分类 |
| `closingProgressBlocked` | 同场 hash、reason，例如 currentReadFailed/noExactCoordinates/authorityDenied/waitingForDisk；nextOwner 是 active request、deadline 或明确待用户意图 |
| `closingProgressDeadlineExceeded` | 意图 hash、generation、最后进展阶段、elapsed、保留坐标类型 |
| `sameSessionReadAttached` | 本场及 batch hash、reader/observer/round 关联、来源 checkpoint phase |
| `sameSessionResultAccepted/Discarded` | 读关联、结果状态或 guard reason |
| `currentCaptureUICommitted/Discarded` | 同场 hash、capture phase、display state、页面订阅关联、丢弃原因 |

close intent、水位、end/ACK/admit receipt、状态 GET、UI 提交应能按同场关联串起来。不要依靠只有 trace 而没有场次映射的零散日志把历史事件拼到本场。

## 8. 先红后绿的真实组合测试设计

### 8.1 装配要求

核心反例必须贯穿：**真实 EchoViewController → 真实 EchoLiveMemoryCaptureCoordinator → 临时磁盘 Store → 真实 OwnerTruth 用例 → 真实 DreamJourneyBackendClient → 真实 FeatureGateService/Evaluator → 受控 URLProtocol 网络**。必要时从既有 Manager frozen ingress 测试入口投递合成 canonical 事件，但不启麦、不运行真实 SDK 音频。

可参考现有 [真实 Gate/Backend 20 分钟组合测试](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:3215) 与 [临时 URLProtocol Session 装配](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:29120)。

- Controller 必须真实挂载 view、绑定 coordinator、经过真实 finish 和通知，再断言实际标签/可访问性标识；不能直接调用 renderer 或写 `state=.pendingReview` 充当端到端完成。
- 使用独立临时目录的 outbox、checkpoint、follow-up；检查落盘快照，并从同一路径创建新 Store/Controller 验证恢复。隔离 UserDefaults、账号 runtime 和 synthetic identity，禁止加载真实用户数据。
- BackendClient 使用测试工厂及 ephemeral URLSession，保留 typed decoder、scope、feature 和 exposure 逻辑。所有 URL 由 fail-closed 的 URLProtocol 截获，未注册路径立即使测试失败，禁止落入真实网络。
- FeatureGateService 通过测试 policy snapshot 和 clock 驱动真实 evaluator；分别注入普通 decision 与 fresh decision。不能全部闭包恒 true、只用 Spy Backend，或开启 QA 绕过授权来证明修复。
- Backend 当前某些 refresh 路径直接使用 `FeatureGateService.shared`；组合测试须显式注入可控 refresh transport/窄适配，保留生产默认值和真实判定，防止单例触网。需要的 clock、调度、磁盘完成回调暂停点只增加最小可测试接缝，不替换生产状态机。
- 用持有/释放网络响应、磁盘串行队列 barrier、主队列排空、可注入 clock/scheduler 精确控制时序；不用 45 秒 sleep 或实际等待 20 分钟证明本地行为。
- 受控服务端按实际请求维护 session/batch/version/水位。不得预设某个与请求无关的 batch 永远 pendingReview；同时校验 HTTP 路径、命令 ID、payload/绑定及 POST 次数。

所有下列新增用例当前均为 **NOT_RUN**。单元测试可补充细节，但不能替代以下核心组合。

### 8.2 用例与决定性断言

| 编号 | 受控时序 | 关键断言及修前预期 |
|---|---|---|
| SV-01（首个红测） | current GET 在 Live 采集中失败；随后完整 owner/assistant 各一条落盘；网络恢复；用户停止 | 修前真实 UI 仍 saving、用例非 ready、两条队列滞留、无有效后续工作。修后最多一次本意图 current 重读，通过同场响应恢复原投递，最终本 Controller pendingReview；start/end/ACK/admit 各按原合同一次，不新增场次 |
| SV-02 | 初次 current GET 一直不回调，正文完整后停止 | 不靠任何新响应也能在关闭无进展预算结束时退出 saving；正文/关闭坐标保留；没有 start/end/ACK/admit 偷发。超时后释放旧 GET，不能启动旧 start 或覆盖新的本场核实结果 |
| SV-03 | 正常完整会话，响应立即按顺序返回 | 当前进程完成 close intent → watermark → 两次 append 确认 → end → inbox/ACK → admit → status → UI。无需切页/重启；候选结果必须属于该场的精确 batch |
| SV-04 | 首个 append notSent 与 mayExpose/outcomeUnknown 两个变体；停止时还有队列 | notSent 仅在真实授权允许时续传原命令；未知只发精确只读 GET，不重发原/新 append。只读证明后保持连续水位并继续；无法证明则有界待核实，不永久 saving |
| SV-05 | start、end、ACK、admit 分别模拟“服务端执行，但响应丢失” | 每种原 POST 次数不增加；原命令/暴露记录保留；有精确只读合同才接纳结果。admit 未知可以核实本场状态，但不能把 readyForAdmission 当成 committed；没有足够证据时不得自动显示成功 |
| SV-06 | 已 admitted；status 先 queued/organizing，再在同一有效轮次迟到 pendingReview | 同一 reader、同一 batch；deadline 内更新当前页面，结果提交一次。原轮次 deadline 和 GET 预算不因 queued 重置 |
| SV-07 | 已 admitted；首个或后续 status GET 不返回；到期后旧响应才返回 | 原 30 秒读意图与最多 6 GET 预算有效；到期显示可核实状态并保留坐标；旧响应零 UI/预算影响。一次新的本场只读意图才可采纳新 pendingReview |
| SV-08 | 同一场 A 读轮次被结束，B 已拥有自己的 poll/GET；随后释放 A poll 和 A HTTP 回调 | A 零额外 GET、零 POST、不能清 B poll 或改 B attempt/trace/state；B 后续正常完成。保留并复跑已有 S01-08 断言 |
| SV-09 | 对当前读取、policy refresh、append/end、ACK inbox/ACK POST、admit POST、follow-up 回调分别悬停 | 每个暂停点都有实际工作责任和截止/明确等待结果；不出现已无工作却无限 saving。未知写保留未知；磁盘失败不能伪称正文已保存。参数化记录各阶段，不能一个 timeout 用例代替全链 |
| SV-10 | 实际已 admitted，但 follow-up upsert 失败；以及本场读取计划缺绑定 | 前者按已 admitted checkpoint 建立私有 reader，无重发写；后者明确待核实、保留坐标、不退回 admission 或任意读取历史 batch |
| SV-11 | 同 Controller 离开/重进；另一个变体在关闭后销毁并重建 Controller，显式选回本场计划 | 回来展示当前状态而非缓存 saving；有效观察恰好一次，无重复 POST；旧 Controller 回调不能写新页面。实例销毁恢复必须走真实临时磁盘 |
| SV-12 | 模拟进程冷启动，从同一磁盘构建全新对象；另加错 account/session/batch 的响应 | 正确同场只读 pendingReview 可显示；unknown 写不重放；错绑定结果被拒绝。测试指定本场计划，不改全局历史任务选择策略 |
| SV-13 | 暂停 close/磁盘/ready/Controller 提交，让合法的晚到回调与更后阶段交错 | 状态不因旧 saving/live 回调倒退；每个结果只属于原意图。必须给出真实可达调度证据，不可人为绕过业务守卫制造回归 |
| SV-14 | saving 或待核实时文字入口可见并打开新的文字交互 | 上一场工作仍归原 productSession，原磁盘记录未删除；新文字不误用旧命令。只测本场存活/绑定，不修改历史 UI 仲裁 |
| SV-15 | 新鲜 allow、旧 route deny；TTL 刚过期后 refresh allow；明确 deny；refresh 挂起；账号变化 | 走真实 FeatureGate/Backend；allow 可有界推进；deny 不发业务写；refresh 不无限续期；账号变化后旧结果不提交。与原 fresh authority 保护同时成立 |
| SV-16（保留性） | 原 D1 完整两问两答、identifier-only→late partial、partial 停止、相同 gap 数摘要变化 | 封存、水位、正文与 UI 摘要断言保留；不降低现有本地绿测。第二次真机 partial 仍 PASS，精确视觉切换仍 NOT_OBSERVED |

SV-01 至少必须先在当前修前业务代码上稳定失败，再用同一装配和同一最终断言变绿。SV-02/04 中若发现新的无进展路径，也分别留下反例。其余保护项允许修前已通过，不得为凑“红测数量”强造失败。

首个红测应核对队列静止原因，而不是只断言标签文本：完成受控事件排空后，检查真实 Controller 是 saving、磁盘有两条完整 delivery、现有用例已失败、网络 harness 无 active 请求、无当前读轮次/有效收尾动作。随后打开下一次同场 GET 的受控响应条件，修前不会自动推进、修后会推进，由此证明修复了交接而不只是改文案。

### 8.3 为什么现有绿测没有覆盖本问题

- [testLiveCaptureDeliveryFailureDoesNotStopDurableCaptureOrCloseIntent](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:514) 主要证明网络失败后还能落盘并写关闭水位；使用 Spy，未验证从失去 ready 的早期读取继续到当前页面完成。
- [testLiveCaptureStatusTimeoutDropsLateResultWithoutInfiniteSaving](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:6937) 从 `resumeOrganization(reviewBatchID:)` 开始，覆盖后段读取超时，不覆盖 close 前已暂停的 NaturalInputUseCase。
- [覆盖摘要真实 Controller 测试](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:1043) 有意悬停 current-session，隔离覆盖摘要刷新；它正确证明摘要观察，而不声称证明会后网络交接。

这些测试仍有价值，不能因本问题出现而否定；需要补上未覆盖的组合，而不是继续仅增加相似状态单测。

## 9. 开发顺序、修改边界与交付门槛

1. 固定本工作区基线，保留既有未提交修改。先完成 SV-01 的真实组合红测和原阶段诊断；记录具体失败的 current/start/append 路径，避免仅依据本设计猜测实施。
2. 最小修复暂停后关闭的推进、无进展收尾及本场重接。按新增反例决定是否需要窄的用例/Backend 测试接缝；不先重构整个保存架构。
3. 逐项执行即时完成、未知写、迟到、超时、旧轮次、页面及冷启动组合。只有反例证实相关分支存在缺陷才扩大局部修改；未改的保护逻辑以回归验证。
4. 执行现有 D1、S01-08、未知写/绑定/TTL 与音频保持性回归，构建 Simulator 和通用 iOS 设备目标；报告具体场景结果、源码与构建指纹。
5. 本地交付标 `LOCAL_PASS / DEVICE_PENDING` 的前提是新组合用例通过且没有遗留 saving 无责任分支。不能以测试总数或编译成功替代这一步；真机仍保持本问题未关闭。

允许的修改集中在本场 CaptureCoordinator、必要的 NaturalInput 恢复入口、当前 Controller 接线、窄的 Backend/FeatureGate 注入接缝及测试。磁盘 Store 原 schema/命令合同优先复用；如发现确实缺少必须持久化的字段，应在局部补充设计中先说明迁移与旧记录只读兼容，不顺手修历史数据。

禁止修改历史 UI 仲裁、Live 音频、B7 和本轮覆盖摘要修复。不能通过增大固定延时、提前成功、删除恢复记录、将 unknown 降为 notSent 或重发未知业务 POST 通过验收。当前没有证据要求修改后端业务或部署。

Sol 本地修复报告需交付：每个新增反例的修前失败与修后同断言通过证据、可解析 xcresult 与实际 summary、组合测试网络/磁盘/UI 关联摘要、差异与指纹、尚未执行项。若结果包不完整，应如实提供原日志且标明证据性质，不把文件名当通过结论。

## 10. 后续最小真机验证设计（本次不执行）

本地门槛完成后另行进行一次短场验证：使用合成个人经历，完整一问一答，AI 结束后手动停止；保持当前页面，记录从 stop 到各阶段及 UI 的时间，确认是本场 batch。不要先切页或重启来替代即时完成。

通过条件：本场内容正确持久化并完成真实 end/ACK/admit/只读结果交接，当前页面根据权威状态及时退出 saving；网络或权限不可用时在有界时间内明确待同步/待核实，并保留本场坐标。正常条件下只改成待核实、始终不能继续保存，不能算通过。

若重现 saving，停止继续创建新 Live。保留本场的脱敏阶段关联；使用本场精确只读结果定位阶段。冷启动验证单独记录，只有绑定核对成功才可写“本场冷启动恢复 PASS”，不得用历史 pendingReview 或候选总数变化替代。

本次不安排再制造 partial 场景来证明 saving 修复。第二次尝试已通过的 partial 展示与未观测到的精确切换，继续保持各自原结论。

## 11. 复核索引

- [用户指定的 D1 校正版清单](../../../outputs/2026-09-17-dreamjourney-live-nonasr-capture-fix/run-2026-09-17-02/reports/D1-01-D1-06执行清单-校正版.md)。
- [第一次安全日志：同步暂停](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-saving-analysis/attempt-1.safe.log:229)。
- [第一次安全日志：完整正文、两条 delivery](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-saving-analysis/attempt-1.safe.log:1343)。
- [第一次安全日志：停止进入 saving、关闭意图落盘](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-saving-analysis/attempt-1.safe.log:1380)。

上述日志中，第一次 Live 之前已经有历史 workflow 的 pendingReview；第二次启动读出的 pendingReview 也包含同一批历史别名。它们只说明本场绑定证据必须补齐，**不构成本场被历史任务影响的根因判定，也不授权修改历史 UI 仲裁。**
