# DreamJourney Live 播放回执误判与连续会话中断修复指导

> 文档日期：2026-09-01（Asia/Shanghai）\
> 缺陷编号：ECHO-LIVE-PLAYBACK-002\
> 执行对象：GPT-5.6 Luna / 独立 Codex 开发任务\
> 修复范围：iOS Live 回响的火山 TTS 播放状态机\
> 后端影响：无，默认禁止修改和部署后端\
> 本文只定义修复任务，不包含源码修改

## 1. 执行摘要

用户在持续打开 Live 的情况下完成一轮提问，后端回答文本已经生成，火山语音也已经在扬声器中播放，但界面随后提示：

```text
回响语音未能开始播放，请再试一次
```

同时停止按钮变回麦克风按钮，连续 Live 交互被动中断。用户没有手动关闭 Live。

这是 iOS 播放状态机的错误，不是后端回答失败，也不是用户主动结束会话。当前实现把“是否真正出声”只绑定到火山 SDK 的 `SEPlayerStartPlayAudio` 回调，并设置固定 6 秒超时。SDK 内置播放器已经出声但开始回调缺失、迟到或被过滤时，客户端仍会走播放失败分支。

本次必须修复：

1. 播放已经发生时不得误报“未能开始播放”；
2. 单轮语音播放失败不得关闭用户控制的 Live 会话；
3. AI 音频仍在播放时不得提前恢复麦克风；
4. 回调缺失、迟到、乱序和重复时，状态机仍必须收敛到唯一正确状态；
5. 不得通过单纯增大 6 秒、删除超时或静默吞错来掩盖问题。

## 2. 用户场景与影响

### 2.1 复现场景

1. 用户进入“回响”；
2. 用户点击麦克风，打开持续 Live 会话；
3. 用户提出一个需要较长回答的问题；
4. `/echo/answers` 返回回答，界面显示完整文字；
5. 扬声器开始播放回答；
6. 播放尚未结束，界面提示“回响语音未能开始播放，请再试一次”；
7. Live 停止按钮变回麦克风按钮；
8. 用户无法自然接着说下一句话。

### 2.2 产品影响

- 一个话题会被系统被动切断；
- 用户误以为语音没有播放，实际声音仍在继续；
- UI、录音状态和真实音频状态互相矛盾；
- 自动恢复麦克风可能把扬声器中的 AI 回答重新录入；
- 长回答更容易触发，破坏“类似 ChatGPT Live / 豆包”的连续交流体验。

## 3. 当前源码基线

执行前必须重新核对，不能假设本文记录仍然最新。

### 3.1 iOS 仓库

```text
/Users/gaominge/Documents/Codex/Video/DreamJourney_dev
```

本文核对时：

```text
branch: feature/prd-stitch-ui-adaptation
HEAD:   439eeb2e fix: recover live echo playback state
remote: 与 origin/feature/prd-stitch-ui-adaptation 同步
```

### 3.2 当前工作树保护

本文核对时工作树存在其他正在开发的 Narrative 修改：

```text
M  DreamJourney.xcodeproj/project.pbxproj
M  DreamJourney/Sources/App/AccountLifecycleRuntimeRegistry.swift
M  DreamJourney/Sources/App/FeatureFlagService.swift
M  DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift
M  DreamJourney/Sources/Services/DreamJourneyBackendClient.swift
M  Package.swift
?? DreamJourney/Sources/Domain/Narrative/
?? DreamJourney/Sources/Modules/Narrative/
?? DreamJourney/Sources/Services/NarrativeBackendService.swift
?? DreamJourney/Sources/Services/NarrativeReaderStateStore.swift
?? DreamJourneyTests/Fixtures/
?? DreamJourneyTests/NarrativeContractsTests.swift
```

执行者必须：

1. 先运行 `git status --short --branch`；
2. 分别查看目标文件的 staged 和 unstaged diff；
3. 不得覆盖、回退或顺手整理上述修改；
4. 禁止 `git reset --hard`、`git checkout -- <file>` 和全量覆盖本地；
5. 未经用户明确要求，不 pull、不 stash、不切分支、不 commit、不 push；
6. 不得回退 `309c58a2`、`6af12fc5`、`99667057`、`f2b61f38`、`439eeb2e` 已建立的路由、音频所有权和回执约束。

### 3.3 后端仓库

```text
/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend
```

本次已确认 `/echo/answers` 能返回并在 UI 中显示回答。默认禁止修改和部署后端。

## 4. 已确认的技术根因

### 4.1 固定 6 秒播放开始超时

文件：

```text
DreamJourney/Sources/Modules/Echo/EchoViewController.swift
```

当前常量：

```swift
private static let livePlaybackStartTimeout: TimeInterval = 6.0
```

`beginLivePlaybackReceipt(...)` 在提交回答前创建回执并立即启动 6 秒计时器。计时器并不知道火山处于以下哪一步：

- 指令已接受；
- TTS 正在合成；
- 音频数据已经到达；
- 内置播放器已经出声；
- 播放完成。

### 4.2 播放开始只依赖单一回调

文件：

```text
DreamJourney/Sources/Services/DialogEngineManager.swift
```

委托模式下，目前主要依靠：

```text
SEPlayerStartPlayAudio
    -> delegate.onTTSPlaybackStarted()
    -> EchoViewController.acknowledgeLivePlaybackStarted(...)
    -> 取消 6 秒超时
```

`SEEventTTSSentenceStart` 已能证明供应商接受了本轮 TTS 并开始处理，但没有反馈给 Echo 播放回执。`SEPlayerAudioData` 当前直接忽略。只要 `SEPlayerStartPlayAudio` 缺失、迟到或回调上下文不再匹配，客户端就会认为没有播放。

### 4.3 超时分支错误地破坏连续会话

`handleLivePlaybackStartTimeout(...)` 当前会：

```text
清除 pending receipt
-> interruptAI()
-> failLiveEchoAnswer(...)
-> viewModel.fail(...)
-> EchoInteractionState.error
-> 尝试恢复录音，失败时退回 idle
```

这把“单轮音频播放异常”和“整个用户控制的 Live 会话失败”混为一谈。即使真实音频还在播放，UI 也会先进入失败态。

### 4.4 结束看门狗仍存在提前完成风险

`439eeb2e` 增加了 `DialogEngineDelegatedPlaybackCompletionPolicy`，用于供应商缺少结束回调时恢复状态。这解决的是“完成回调缺失”，没有解决“开始回调缺失”。

当前看门狗根据字符数等待 1.5 至 5 秒，然后可能补发 started 和 finished。必须核实 `SEEventTTSEnded` 在当前 SDK 中表示“合成流结束”还是“扬声器播放结束”。如果只是合成流结束，按最多 5 秒提前完成会导致声音仍在播、麦克风已经恢复。

### 4.5 为什么长回答更容易触发

回答长度不是根因，但会放大：

- 服务端合成首包时间；
- SDK 事件调度延迟；
- 内置播放器启动延迟；
- 合成完成与实际播放完成之间的时间差；
- 固定 6 秒和固定 5 秒看门狗的不准确性。

## 5. 修复原则与状态机

### 5.1 必须区分的状态

不得再用一个 `didStart` 布尔值承载全部播放生命周期。建议新增可单元测试的纯状态模型，至少包含：

```swift
enum DelegatedLivePlaybackPhase: Equatable {
    case idle
    case submitted
    case providerAccepted
    case synthesizing
    case audiblePlaybackStarted
    case synthesisEnded
    case audiblePlaybackFinished
    case interrupted
    case failed
}
```

命名可以按现有风格调整，但语义不能合并。

### 5.2 事件映射

建议把 SDK 事件映射为强类型进度事件：

| SDK/本地事件 | 状态含义 | 是否证明已出声 | 是否允许恢复麦克风 |
| --- | --- | --- | --- |
| `submitLiveAnswerText` 返回成功 | submitted | 否 | 否 |
| 接受本轮 client-triggered `SEEventTTSSentenceStart` | providerAccepted / synthesizing | 否 | 否 |
| 首个可验证播放器音频活动 | synthesizing / audioReady | 不一定 | 否 |
| `SEPlayerStartPlayAudio` | audiblePlaybackStarted | 是 | 否 |
| `SEEventTTSEnded` | synthesisEnded，需按 SDK 实测确认 | 否 | 否 |
| `SEPlayerFinishPlayAudio` | audiblePlaybackFinished | 是 | 是 |
| 用户主动打断 | interrupted | 已停止后才算终态 | 是 |
| 供应商明确失败 | failed | 否 | 完成清理后允许 |

禁止把 `SEEventTTSSentenceStart` 直接伪装成“已经出声”；它只能证明供应商已接受和处理，从而阻止错误的“无响应”判定。

### 5.3 回执与关联字段

每轮必须绑定：

- Echo `turnID`；
- Live receipt ID；
- 火山 `replyID`；
- account generation；
- lifecycle token / generation；
- 固定音频路由。

旧轮次、旧账号、旧生命周期或不同路由的回调必须被忽略。重复事件必须幂等。

## 6. 必须实施的修改

### 6.1 将超时拆成两个阶段

#### 阶段 A：供应商接受超时

从 `submitLiveAnswerText` 成功后开始等待本轮 client-triggered TTS 的接受事件。只有完全没有供应商进展时，才可以判定提交/服务异常。

#### 阶段 B：播放进展与终态看门狗

一旦收到本轮 TTS 接受或合成事件：

1. 取消“未接受”超时；
2. 进入合成/等待播放状态；
3. 启动单独的整体终态看门狗；
4. 看门狗必须结合回答字符数、已观测事件和真实设备数据；
5. 不得继续使用固定 6 秒直接报“未开始播放”。

不要直接拍脑袋写一个更大的固定值。先增加时间差诊断，再用保守上限。整体看门狗只负责避免永久卡死，不负责猜测真实播放完成时刻。

### 6.2 增加委托播放进度通知

优先在 `DialogEngineManager` 增加仅用于 backend-answer delegated Live 的强类型进度回调，例如：

```swift
enum DialogDelegatedPlaybackProgress: Equatable {
    case providerAccepted(replyID: String)
    case audioStarted(replyID: String)
    case synthesisEnded(replyID: String)
    case audioFinished(replyID: String)
    case interrupted(replyID: String?)
    case failed(replyID: String?, code: String)
}
```

具体 API 可以调整，但不得靠散落的多个布尔值继续修补。

现有 `onTTSPlaybackStarted()`、`onTTSFinished()` 可为其他模式保留。新增进度只影响 `answerAuthority == .dreamJourneyBackend` 且 `sessionLifetimePolicy == .userControlledLive` 的路径。

### 6.3 修正 Live 失败语义

单轮播放失败时：

- 保持 `isUserControlledLiveSessionOpen == true`；
- 不调用会让整个会话进入永久错误态的通用 `viewModel.fail(...)`；
- 显示短暂状态，例如“本轮语音播放未完成，已继续聆听”；
- 只有确认播放器已停止或引擎已完成受控重置后，才恢复录音；
- 恢复后状态必须为 listening，停止按钮必须保持可见；
- 用户主动停止 Live 时，所有自动恢复必须立即失效。

可以复用现有 `replyInterrupted -> listening` 意图，也可以新增明确的“playback failure recovered”意图。不得绕过 `EchoTurnIntentReducer` 直接篡改 UI。

### 6.4 禁止播放和录音重叠

当开始回调缺失但真实音频仍在播放时，不能在 0.35 秒后盲目 `resumeRecorder()`。

恢复录音必须满足至少一项可靠条件：

1. 收到同一 replyID 的 `SEPlayerFinishPlayAudio`；
2. 用户打断已得到停止确认；
3. 执行了能够保证播放器停止的受控引擎重置，并重新建立同一用户 Live 会话；
4. 经过有证据的 provider idle 状态确认。

如果当前 SDK 没有可靠停止确认，执行者必须在代码注释和结果报告中说明，并使用“重置引擎后恢复 Live”作为最终兜底，而不是录音与残余播放并行。

### 6.5 修正结束看门狗

不得在无法证明音频结束时直接补发 `onTTSFinished()`。

执行者必须先通过诊断确认：

- `SEEventTTSEnded` 的发生时刻；
- `SEPlayerStartPlayAudio` 的发生时刻；
- `SEPlayerFinishPlayAudio` 的发生时刻；
- 真实可听开始和结束时间；
- 不同长度回答的事件顺序。

如果 finish 回调可能缺失，看门狗到期时应先确保停止播放器，再进入终态。看门狗不得一边让声音继续，一边恢复麦克风。

### 6.6 开始回执创建时机

将回执与 `submitLiveAnswerText` 的成功结果绑定：

1. 创建 turn/reply correlation；
2. 提交回答；
3. 提交被同步拒绝时直接清理，不留下计时器；
4. 提交成功后正式进入 submitted 并启动阶段 A 超时。

当前“先开始倒计时，再提交”的时间差虽然很小，但会让状态语义不准确，应一并收敛。

## 7. 诊断与可观测性

必须增加不包含用户正文的隐私安全日志。每轮至少记录：

```text
liveAnswerSubmitted
delegatedTTSProviderAccepted
delegatedTTSAudioActivityObserved
delegatedTTSPlayerStarted
delegatedTTSSynthesisEnded
delegatedTTSPlayerFinished
delegatedTTSInterrupted
delegatedTTSFailed
livePlaybackWatchdogAdvanced
livePlaybackWatchdogExpired
liveCaptureResumed
```

公共关联字段：

```text
receiptID
turnID
replyID
route
lifecycleGeneration
answerCharacterCount
elapsedMsSinceSubmit
```

禁止记录回答正文、用户问题、正式记忆内容、手机号或 Provider 凭证。

## 8. 预期修改文件

主要文件：

```text
DreamJourney/Sources/Services/DialogEngineManager.swift
DreamJourney/Sources/Modules/Echo/EchoViewController.swift
DreamJourneyTests/AudioOwnerLeaseModelTests.swift
```

只有状态转换确实无法表达“单轮播放失败但 Live 仍打开”时，才允许小范围修改：

```text
DreamJourney/Sources/Modules/Echo/EchoViewModel.swift
```

原则上禁止修改：

```text
DreamJourneyBackend/**
DreamJourney/Sources/Services/DreamJourneyBackendClient.swift
正式记忆、Projection、/echo/answers 和 DeepSeek Prompt
音色选择和 Voice Clone 路由
腾讯数字人业务逻辑
Narrative / 自传开发文件
```

腾讯数字人路线必须保持现有行为。若提取公共纯状态模型，不得改变其路由判定和音频所有权。

## 9. 测试要求

### 9.1 纯状态机单元测试

至少覆盖：

1. submitted -> providerAccepted -> audioStarted -> audioFinished；
2. providerAccepted 在 6 秒内到达，但 audioStarted 晚于 6 秒，不得失败；
3. audioStarted 回调缺失，audioFinished 到达，状态幂等收敛且不误报；
4. finish 回调早于 start 回调，旧 start 不得复活已终止回执；
5. 重复 start、finish 不得重复恢复录音；
6. 不同 replyID、route、lifecycle 的回调被拒绝；
7. 用户主动停止后，迟到回调不得自动恢复 Live；
8. 真正无供应商进展时，超时进入可恢复失败；
9. 播放失败后 Live 会话仍打开；
10. 播放未确认停止时不得进入 listening；
11. 20、100、220 个汉字下的看门狗策略不提前完成；
12. Tencent 路由不接受 Volcengine 回调，反之亦然。

### 9.2 回归测试

必须保留并通过现有测试：

```text
EchoLiveAudioRoutePolicyTests
EchoLivePlaybackReceiptState 相关测试
DialogEngineAudiblePlaybackPolicyTests
DialogEngineDelegatedPlaybackCompletionPolicyTests
Audio owner / lease / account generation 相关测试
```

如果旧测试把不正确行为固化为预期，应说明原因后更新，不得只删除测试。

### 9.3 编译验证

先列出可用 scheme 和设备：

```bash
xcodebuild -list -workspace DreamJourney.xcworkspace
xcrun simctl list devices available
```

无可用模拟器时，至少执行无签名编译：

```bash
xcodebuild \
  -workspace DreamJourney.xcworkspace \
  -scheme DreamJourney \
  -configuration Debug \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO \
  build
```

不得因为当前存在 Narrative 未提交文件而删除或回退它们。

### 9.4 真机验收矩阵

未经用户明确要求，本修复任务不要自行安装或真机测试。获得授权后，必须覆盖：

| 场景 | 轮数 | 预期 |
| --- | ---: | --- |
| 20 字左右短回答 | 5 | 每轮播放后自动继续聆听 |
| 100 字左右回答 | 5 | 不出现误报，Live 不关闭 |
| 200 至 220 字长回答 | 5 | 播放期间停止按钮保持，结束后恢复聆听 |
| 连续 10 轮问答 | 1 组 | 无会话被动结束、无重复声音 |
| 播放中用户打断 | 3 | 音频停止后立即继续聆听 |
| 网络变慢或模拟迟到 start | 3 | 不因 6 秒固定阈值误报 |
| 用户主动关闭 Live | 3 | 不自动重启麦克风 |
| App 前后台切换 | 3 | 不出现残余播放与录音重叠 |

真机日志必须能够通过 receiptID/turnID/replyID 还原完整事件顺序。

## 10. 验收标准

以下条件必须全部满足：

1. 火山语音已经出声时，不再出现“回响语音未能开始播放”；
2. 回答较长时，Live 不会被固定 6 秒超时切断；
3. 用户未关闭 Live 时，单轮播放异常不会把停止按钮变回麦克风按钮；
4. AI 播放期间麦克风不恢复，避免回声重录；
5. 播放完成后麦克风只恢复一次；
6. 用户主动关闭后，迟到回调不会重启会话；
7. 文本回答内容、正式记忆检索和 DeepSeek 组织逻辑不变；
8. 火山仍是 Live 的语音播放路线，不接入 Apple TTS；
9. 后端无代码改动、无部署；
10. 新增测试能够稳定复现旧 Bug，并在修复后通过；
11. iOS 编译成功；
12. 执行结果列明修改文件、测试、未验证项和残余风险。

## 11. 明确禁止的伪修复

以下做法不能作为完成：

- 把 6 秒简单改成 10 秒、30 秒或更长；
- 直接删除所有超时；
- 隐藏错误 Toast，但状态仍进入 error/idle；
- 播放失败后要求用户重新点击麦克风；
- 一到超时就立即恢复录音，不管扬声器是否仍在播；
- 把回答截短来规避；
- 改 DeepSeek Prompt 或 `/echo/answers`；
- 改用 Apple 系统 TTS；
- 同时启动火山内置播放器和 App PCM 播放器；
- 取消 route、replyID、lifecycle 和 account generation 校验；
- 为了让测试通过而删除原有回执测试。

## 12. 推荐执行顺序

1. 核对分支、HEAD、dirty files 和目标文件 diff；
2. 用现有日志与源码确认 screenshot 文案只来自 `livePlaybackStartTimedOut`；
3. 提取纯播放阶段 reducer / policy，先写失败测试；
4. 增加 DialogEngine delegated progress 事件；
5. 把 TTS 接受、播放器开始、合成结束、播放器结束映射到 reducer；
6. 把 Echo 的单一 6 秒回执改为分阶段 watchdog；
7. 修正单轮播放失败的 Live 恢复语义；
8. 修正 completion watchdog，禁止未停止就恢复麦克风；
9. 运行目标单测和完整相关测试；
10. 无签名编译；
11. 输出变更摘要和残余风险；
12. 等待用户明确授权后再 commit、push、安装或真机测试。

## 13. 交给 GPT-5.6 Luna 的执行指令

可将下面内容作为任务开场：

```text
请严格按照《2026-09-01-DreamJourney-Live-播放回执误判与连续会话中断-修复指导.md》实施。

先读取当前源码、git status、目标文件 staged/unstaged diff，并验证文档中的根因。不要直接把 6 秒改大。先为播放阶段、回调迟到/缺失/乱序、Live 会话保持和录音恢复建立失败测试，再做最小范围修复。

保持 /echo/answers、正式记忆、DeepSeek、火山音色和腾讯数字人路线不变。不要修改或部署后端，不要覆盖当前 Narrative 工作，不要 commit、push、安装或真机测试，除非用户另行明确授权。

完成后必须报告：
1. 最终根因；
2. 修改文件和关键状态转换；
3. 新增/更新测试；
4. 编译结果；
5. 未验证项与残余风险；
6. 是否仍存在声音播放与麦克风录音重叠的可能。
```

## 14. 执行结果模板

```markdown
# ECHO-LIVE-PLAYBACK-002 执行结果

## 最终根因

## 修改文件

## 状态机变化

## 测试结果

## 编译结果

## 未执行事项

## 残余风险

## 是否建议进入真机测试
```
