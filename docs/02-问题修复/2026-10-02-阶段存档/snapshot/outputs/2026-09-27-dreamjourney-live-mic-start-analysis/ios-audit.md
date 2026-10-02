# DreamJourney Live 开麦 iOS 只读审查备忘

日期：2026-09-27。范围：依据 2026-09-24 事故报告与当前 iOS 工作区，分析开麦调用链、发前失败、耗时边界及最小修复设计；供 GPT6 Sol 主设计参考。

本轮仅写此备忘：未修改产品代码或登记册，未连接手机、运行 Provider、请求后端业务或操作生产，未运行测试。源码审查并非设备运行验证。

## 基线与结论

- 仓库：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`。
- 当前分支：`feature/prd-stitch-ui-adaptation`，HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`。
- 工作区已有大量修改，包括 Echo、BackendClient、FeatureGate/ReleasePolicy、认证和测试。下列行号对应本轮读取时的工作区，不代表事故设备二进制的精确源码指纹。
- 权威现场记录：[incident-report.md](../2026-09-24-dreamjourney-live-mic-start-incident/run-01/incident-report.md)。

当前代码存在确定的逻辑情形：页面早先捕获的 route decision 到期后，即使当前缓存已经换成允许的新策略，Live ticket 仍会复用旧 decision，在创建网络请求之前失败。该情形与 23:04:55 的受控失败相符，但现场没有单次 ticket 的原始错误、请求曝光状态和贯穿 trace，因此只能列为最可信假设，不能升级为已证实根因。

## 精确调用链

1. [EchoViewController.swift:12743](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:12743) 的 `micTapped` 调用 `startVoiceCapture`（14598）；先检查账号租约、绑定 DialogEngine，再申请麦克风权限。
2. 权限允许且账号、生命周期仍有效后，Echo 14644 `prepareVoiceInteraction` 进入 `.starting`；若要交接腾讯音频，14646 固定等待 0.35 秒，然后调用 `configureVoiceRuntimeThenStart`（15582）。否则立即调用。
3. `configureVoiceRuntimeThenStart` 再检查账号租约、绑定和生命周期。`isRealtimeVoiceConfigConfigured` 只判断配置了后端 URL，见 [DreamJourneyBackendClient.swift:7473](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:7473)，不是实时 readiness 检查。
4. Echo 15620 调用 [DreamJourneyBackendClient.swift:8155](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:8155) 的 `fetchRealtimeVoiceConfig`；8181 对 `/voice/realtime-token` 发 user-required POST。该入口没有专门的策略恢复、总 deadline、取消 handle 或 trace 参数。
5. [DreamJourneyBackendClient.swift:16465](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:16465) 的 `requestJSON` 依次检查私有 UI、认证会话、subject、CAS、backend lease、application lease，随后检查 runtime recovery fence（16647）。若 runtime deny，可先 GET `/config/runtime`，成功后递归原请求一次（16736）；这是条件恢复，不是每次开麦都先调用 runtime HTTP。
6. ticket 路径映射为 `.echoTextInput`（BackendClient 769）；16840 默认调用普通 `requestFeatureDecision`。若拒绝，16864 返回 `.featurePolicyDenied`，此时尚未到 16947 创建 ticket 网络请求。
7. ticket 发出且 HTTP 401 后，才进入认证恢复（17105、17172）；成功后原请求重发一次。初次 feature deny 不会触发此认证刷新分支。
8. ticket 解码成功后，Echo 15643 `DialogEngine.configure`，再检查 productSessionID 一致性、音频路由、AVAudioSession，15677 调用 `startDialog`。
9. [DialogEngineManager.swift:3656](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:3656) 初始化 SDK；必要时补等 0.5 秒（3721）。4736 发送 StartEngine；收到 `SEEventSessionStarted`（5599）后经 5638 回调 `onDialogStarted`；[EchoViewController.swift:15984](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:15984) 再验证租约和音频会话，15994 才切换 listening。

## expired policy 的确切来源

- Echo `viewDidLoad` 6381 捕获 `.echoTextInput` route；入口策略刷新成功时也会捕获（13896–13902）。点击开麦本身没有 fresh 捕获或策略刷新。
- BackendClient 555 取 `routeDecisions[feature]`；同账号 generation 时 559–565 revalidate 旧 route。
- [ReleasePolicyStore.swift:396](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/ReleasePolicyStore.swift:396) 先判断旧 `captured.expiresAt`，到期直接返回 `capturedPolicyExpired`，然后才会检查 currentPolicy 的版本和允许状态。当前新策略允许，不会自动替换旧 captured decision。
- 若旧 captured 本来已经 deny，384–386 更早原样保留 deny；同样不能期待刷新缓存自然救活它。
- BackendClient 621 已有 `freshServerPolicyManagedRequestDecision`，它从当前策略创建新 request decision。候选/正式记忆读取和部分 Live 记忆状态读取已使用；ticket 未接入。`requestJSON` 的 `freshFeatureDecisionAfterRecovery` 默认 false（16486），ticket 没覆盖。

可确定的本地场景是：同账号页面 route 允许 → route 到期 → 当前缓存换成有效允许策略 → 点击开麦 → requestJSON 使用旧 route → ticket 零网络曝光 → Echo 通用失败。

过期保护本身应保留。局部修复应在新一次用户开麦授权准备阶段重新获取当前 request authority，必要时有界刷新；不可删除 evaluator 过期检查、固定 gate 为 true，或通过 QA 分支绕过生产 gate。沿用 `.echoTextInput` 是当前产品契约事实；若要另立 voice 权限，需独立产品/后端契约决定，不能在本修复中悄悄扩权。

## backendVoiceRuntimeRequestFailed 与诊断盲区

[EchoViewController.swift:15688](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:15688) 将 fetch 全部失败统一映射到 `backendVoiceRuntimeRequestFailed`（15706）。它可来自本地 feature/runtime/auth/lease deny、网络、HTTP 或 JSON 解码，不等于服务端错误，更不等于 SDK 失败。

`EchoRealtimeVoiceRuntimeFailureDiagnostic`（Echo 48–88）只对白名单后端 business code 分类。`ClientError.featurePolicyDenied` 虽符合 backend failure protocol，但 status/code getter 只处理 `.backendError`，见 BackendClient 17887–17894；本地 deny 因此变成 `stage=unknown/businessCode=unknown`，原始 policy reason 丢失。普通网络 Error 只剩 `stage=request/businessCode=unknown`。

Live 未传 `diagnosticTraceID`；BackendClient `logRequestStage` 在 9212 遇 nil 直接返回，16915 的 task exposure probe 也不创建。现有阶段日志基础设施不能视为 Live 已有同等可观察性。

持久 `EchoRuntimeDiagnosticsSnapshot`（BackendClient 4823）保存 fallback 与 feature decision 列表，没有本次 ticket 原始 error、曝光和阶段时间。Echo 9957 获取 FeatureGate 全局 `latestDecisions`（BackendClient 675–679），未绑定某一 ticket，因此报告中的 `capturedPolicyExpired` 只能是关联线索。

## 两次观察必须分开

| 观察 | 已知 | 仍未知 |
| --- | --- | --- |
| 22:57 慢成功 | 22:57:49 ticket 200；22:57:50 SDK callback；22:58 listening | 点击时点、ticket 发起/首字节/处理时长、前置恢复等待、同 trace；不能把此前认证完成到 ticket 完成的约 51 秒直接当作 ticket 耗时 |
| 23:04:55 明确失败 | fallback 为 backendVoiceRuntimeRequestFailed；snapshot 中旧 policy 23:04:36 到期；服务端窗口未见新增 ticket/auth/policy/runtime 日志 | 单次原始 error、task 是否创建/恢复、是否到达服务器；不能单独排除网络未抵达或日志缺口 |

慢成功证明至少一次进入后续阶段，不能写成始终无法开麦。明确失败没有支持 SDK 或音频权限错误的直接证据，不能用第一次后续 provider callback 替第二次补因果。

## 权限、网络、SDK 与云会话的时间边界

- [MicrophonePermissionManager.swift:13](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/MicrophonePermissionManager.swift:13)：已授权立即 `completion(true)`（20–21）；未决定时等待系统权限响应（25），没有应用内权限等待截止。`.starting` 是 granted 后才设置，故“正在准备”截图不支持把这段等待归给权限弹窗。
- 显式固定等待只见腾讯交接 0.35 秒与 SDK 初始化后补查 0.5 秒。它们不能解释几十秒慢启动。
- SDK 初始化补查在 3722–3728 guard 不通过时直接 return；要在组合测试确认“SDK 始终未 ready”有终态，不能依赖单次 0.5 秒补查作为启动总截止。
- SDK 4736 StartEngine 返回成功后，4769 等待回调；本次检查的启动路径没有从点击贯穿 ticket 与 SessionStarted 的总截止。现有 45 秒 livePlaybackTerminalWatchdog（Echo 6074、8741）属于回复播放终态，不能当作开麦启动 watchdog。
- runtime GET、首次 ticket、401 后等待认证 single-flight/队列（BackendClient 17542–17562）、auth refresh、ticket 重发、SDK ready/SessionStarted 均可串行累加。认证队列 17567 仅在没有 active group 时启动下一组；Live 没有意图级等待预算。
- 生产 transport 为 `AF`（BackendClient 7641），Alamofire `Session.swift:197` 用默认 URLSession 配置；`URLSessionConfiguration+Alamofire.swift:31` 仅加默认 headers。本机 Xcode SDK `NSURLSession.h:1315–1340` 说明默认等待新数据超时 60 秒，收到新数据会重置，resource timeout 默认 7 天。这里能证明没有项目指定的短总时限，不能证明事故命中某个 60 秒超时，更不能解释为每次业务总时限。
- 客户端持有 backend proxy ticket，实际云会话在 ticket 后才开始；单次 ticket 创建与 proxy/Provider 建连必须分开计时。本轮没有查看或执行云会话服务端超时，后端部分由主审查补齐。

## 给 GPT6 Sol 的最小改动边界

以下为设计建议，未实现或验证：

1. **每次开麦一个 LaunchAttempt。** 保存单调时钟起点、attemptID、账号 lease、生命周期 token、当前阶段、terminal 标志。点击即建 trace；系统首次权限等待单独记录；实际启动 deadline 在权限允许后起算，避免把用户阅读系统弹窗时间误报成网络超时。若产品要求包含权限阶段的墙钟截止，应明确规定超时后迟到授权不能自动开麦。
2. **一个总预算，阶段不续命。** policy/auth/runtime/ticket/SDK/audio session 全部消耗同一实际启动截止时间；重试或刷新不得重置总预算。具体秒数由产品验收目标确定，不以默认 60 秒倒推。到期一次性进入可重试终态并解除灰按钮。
3. **局部授权准备。** 捕获当前 request decision；只有允许恢复的 stale/missing 等原因可触发至多一次策略刷新。刷新后重验 lease、generation、lifecycle、deadline，重新捕获 decision；真实 deny 保持关闭。不要全局改变既有写请求 gate 语义。
4. **取消 handle 与作用域。** Live ticket 请求返回可取消 handle；停止、离页、账号切换、supersede、deadline 都使 attempt 终止并取消属于该 attempt 的 transport/SDK 启动。共用认证或策略 single-flight 应只撤销该 waiter，不能误杀其他业务等待者。ticket 是否已曝光、是否已经发行未知时，不自动新建另一个票据请求“碰运气”。
5. **晚到回调只做安全收尾。** 每个异步入口统一检查 attempt 仍当前且未终止；旧 ticket completion 不 configure SDK；旧 SDK SessionStarted 不进入 listening；旧 audio lease 释放不得作用于新 attempt。现有 lease/lifecycle 验证继续保留，新增 terminal gate 只补截止/取消维度。
6. **可复核诊断。** 本次 attempt 保存白名单 error family/domain/code、HTTP 可选值、policy reason、requestCreated/taskResumed/responseReceived、各阶段单调时长、最终阶段与 outcome；不得记录 token、正文、完整响应和原始业务 hash。preflight deny 明确 exposure=false，未收到 HTTP 不填伪造状态码。

## 现有测试覆盖与必须补的红绿组合

已经存在但本轮未运行：

- [OwnerTruthContractsTests.swift:32438](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:32438)：ticket 路径映射 `.echoTextInput`。
- 同文件 32321、32404：proxy-only ticket 合同、有效期、绑定与失败关闭。
- [AudioOwnerLeaseModelTests.swift:10](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/AudioOwnerLeaseModelTests.swift:10)、26：snapshot 503 分类、未知后端 code 脱敏；没有本地 feature deny、exposure 或启动 deadline 覆盖。
- FeatureGate evaluator smoke 160–168：旧 route 到期必须拒绝。
- OwnerTruthContractsTests 35937：正式记忆 fresh request decision 不复用旧 route。
- 同文件 4974 等 Live policy/auth/ACK 测试验证访谈记忆命令或状态恢复，不等于开麦 ticket。

全局检索没有发现测试实际调用 `fetchRealtimeVoiceConfig`。现有 QA 脚本对 `configureVoiceRuntimeThenStart` 的检查是源码字符串检查，不能替代 Controller→Client→Gate→Transport 的组合验收。

必须先红后绿的本地组合，要求真实 Controller 入口、真实 BackendClient、真实 FeatureGate/evaluator、URLProtocol mock transport；仅替换权限、时钟、SDK/音频边界以确保零外部业务请求：

| 场景 | 必须断言 |
| --- | --- |
| 旧 route 到期，当前缓存新策略允许 | 修复前 ticket 零曝光且失败；修复后至多一次 ticket，SDK 恰好启动一次，无多余刷新 |
| 当前策略也过期，刷新后允许 | 一次策略恢复，fresh 捕获后一次 ticket；各步共用 attempt 与总截止 |
| 当前策略明确 deny 或刷新后 deny | ticket/SDK/麦克风启动均为零；保留准确 deny 原因 |
| policy/runtime/auth/ticket 任一阶段悬挂 | 总截止产生一次终态；UI 可恢复；取消该 attempt；不会无限 `.starting` |
| policy/auth 响应后账号切换或退出页面 | 旧 attempt 无 ticket 曝光或 SDK 启动；新会话无污染 |
| deadline/取消后 ticket 晚到 | 不 configure/start SDK，不进入 listening，不重新设置 active session |
| deadline/取消后 SDK SessionStarted 晚到 | 丢弃或安全停止旧 SDK generation，不能停掉新 attempt |
| SDK setup 长期未 ready、StartEngine 拒绝、永无 SessionStarted | 都有准确阶段终态，不能只依赖 0.5 秒补查 |
| ticket 401 后认证恢复 | 原意图有界重发；重新检查当前 gate/lease；不续总预算；不绕过真实 deny |
| 本地 policy deny、DNS、HTTP 503、解码失败 | 四类诊断可区分；HTTP 缺席时保留 nil；exposure 真实；无敏感值 |

测试名称必须明确是 microphone/ticket launch，避免再用记忆 start/append 测试代替语音传输开麦覆盖。以上未实施，状态均为 `NOT_RUN`。
