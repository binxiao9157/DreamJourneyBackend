# DreamJourney Live 播放完成回执缺失与假恢复修复指导

> 文档日期：2026-09-02（Asia/Shanghai）\
> 缺陷编号：ECHO-LIVE-PLAYBACK-003\
> 执行对象：GPT-5.6 Luna / 独立 Codex 开发任务\
> 适用范围：iOS 回响 Live、火山 SpeechEngine 内置播放器、连续会话恢复\
> 后端影响：无，默认禁止修改和部署后端\
> 与前置文档关系：本文件是 `ECHO-LIVE-PLAYBACK-002` 真机验证后的增量修正；当前源码已包含前置方案的未提交实现，不得回退后重做\
> 本文只定义修复任务，不包含源码修改

## 1. 执行摘要

上一轮已经修复“语音实际正在播放，却在 6 秒后提示未能开始播放并终止 Live”的问题。新实现引入了更完整的播放阶段模型和 45 秒终态看门狗，但真机验证暴露出两个串联缺陷：

1. 火山语音实际已经播放结束，客户端却一直显示“正在回响”或“回响正在抵达”；
2. 约 45 秒后提示“本轮语音播放未完成，已继续聆听”；
3. 提示恢复后，用户说话无法形成下一轮有效问题，也不会再次调用 `/echo/answers`。

这不是 DeepSeek、正式记忆检索、后端回答或用户发音的问题。当前 iOS 实现一方面关闭了火山播放器事件回调，另一方面又把 `SEPlayerFinishPlayAudio` 作为唯一成功终点；终点必然缺失。看门狗触发后，恢复代码只改了界面文案和音频会话，没有把 `EchoTurnIntentReducer` 从 `speaking` 合法迁移到 `listening`，形成“界面已恢复、业务状态未恢复”的假恢复。

本次必须修复：

1. 建立当前火山内置播放器路径上真实可达的播放开始与完成契约；
2. 不再等待一个由本地配置关闭、真机也未出现的终态事件；
3. 正常完成、用户打断、供应商失败和看门狗恢复必须各自执行合法状态迁移；
4. 恢复成功必须同时满足 UI 状态、业务状态、录音状态和当前 Live 生命周期一致；
5. 下一句用户语音必须形成 final ASR，并能再次进入 `/echo/answers`；
6. 不得重新使用按文字长度猜测播放完成的旧方案。

## 2. 用户可见问题

### 2.1 复现步骤

1. 进入“回响”；
2. 点击麦克风，打开用户控制的持续 Live 会话；
3. 提出一个问题；
4. `/echo/answers` 返回文字答案；
5. 火山语音在扬声器中正常播放；
6. 实际声音结束后，页面仍显示“正在回响”或“回响正在抵达”；
7. 约 45 秒后显示“本轮语音播放未完成，已继续聆听”；
8. 用户继续讲话，页面没有形成下一轮回复。

### 2.2 产品影响

- 连续 Live 实际只能完成一轮；
- UI 与真实声音状态不一致；
- 用户被迫等待一个无意义的 45 秒超时；
- 页面声称“继续聆听”，实际上业务状态仍停留在上一轮；
- 用户后续表达可能只产生临时 ASR，无法成为正式问题；
- 长回答更容易让问题显得严重，但回答长度不是根因。

## 3. 当前源码基线与工作树保护

### 3.1 iOS 仓库

```text
/Users/gaominge/Documents/Codex/Video/DreamJourney_dev
```

本文核对时：

```text
branch: feature/prd-stitch-ui-adaptation
HEAD:   439eeb2edb96049ccd764bb6370bbe85ade2e9ee
commit: 439eeb2e fix: recover live echo playback state
```

目标文件当前已有上一轮 Luna 的未提交修改：

```text
DreamJourney/Sources/Modules/Echo/EchoViewController.swift
DreamJourney/Sources/Services/DialogEngineManager.swift
DreamJourneyTests/AudioOwnerLeaseModelTests.swift
```

本文核对时，上述三处合计约为：

```text
820 insertions, 187 deletions
```

这些修改就是本次需要继续修正的基线，不是可丢弃的临时文件。

### 3.2 其他并行修改

工作树还存在 Narrative、自传、Feature Flag、Archive、BackendClient、Package 等并行修改。执行者必须：

1. 先运行 `git status --short --branch`；
2. 分别查看目标文件的 staged 和 unstaged diff；
3. 在当前目标文件修改之上做小范围修复；
4. 不得覆盖、回退或格式化无关修改；
5. 禁止 `git reset --hard`、`git checkout -- <file>`、全量覆盖和重建工程文件；
6. 未经用户明确要求，不 pull、不 stash、不切分支、不 commit、不 push；
7. 未经用户明确要求，不安装 App、不真机测试、不部署后端。

### 3.3 默认禁止触碰的范围

```text
DreamJourneyBackend/**
DreamJourney/Sources/Services/DreamJourneyBackendClient.swift
正式记忆、Projection、/echo/answers、DeepSeek Prompt
Voice Clone 与音色选择
腾讯数字人业务路线
Narrative / 自传开发文件
```

本轮日志已经证明 `/echo/answers` 返回成功，回答文本也已交给火山 TTS。后端不在故障链路中。

## 4. 真机证据

### 4.1 已确认事件

一次复现中，约在 `00:46:18` 记录到：

```text
event=delegatedTTSSynthesisEnded
answerCharacterCount=135
elapsedMsSinceSubmit=2967
event=livePlaybackWatchdogAdvanced stage=terminal
```

该轮语音随后在扬声器中正常播完。

### 4.2 缺失事件

同一 receiptID / turnID / replyID 下没有出现：

```text
delegatedTTSPlayerStarted
delegatedTTSPlayerFinished
livePlaybackCompleted
```

因此当前回执停在 `synthesisEnded`，直到 45 秒终态看门狗到期。

### 4.3 麦克风并非完全失效

看门狗恢复后，约在 `00:47:02`，火山仍上报了用户语音的临时识别：

```text
ASRResponse: text=你, isFinal=false
ASRResponse: text=你好, isFinal=false
ASRResponse: text=你好还, isFinal=false
ASRResponse: text=你好还能, isFinal=false
```

这证明：

- 音频输入链路仍可能收到 PCM；
- “无法继续对话”不能简单归因于麦克风硬件或权限；
- 真正的问题是上一轮播放状态没有正确收尾，下一轮没有形成被业务层接受的 final ASR。

## 5. 已确认根因

## 5.1 根因一：配置关闭播放器回调，状态机却依赖播放器终态

文件：

```text
DreamJourney/Sources/Services/DialogEngineManager.swift
```

当前初始化明确设置：

```swift
engine.setBoolParam(
    false,
    forKey: SE_PARAMS_KEY_DIALOG_ENABLE_PLAYER_AUDIO_CALLBACK_BOOL
)
```

相邻注释说明，当前 SDK 开启播放器音频回调时曾持续产生静音缓冲，因此保留火山内置扬声器路径并关闭该回调。

但新状态机只有在收到下面事件时才完成：

```swift
case SEPlayerFinishPlayAudio:
    emitDelegatedPlaybackProgress(.audioFinished(...))
```

火山头文件对这些事件的定义为：

```text
SEPlayerStartPlayAudio  = 3019
SEPlayerFinishPlayAudio = 3020
```

真机中声音正常播放，但没有出现 3019/3020。无论原因是回调开关、当前 SDK 版本还是 `SayHello` client-triggered 路径，这两个事件在当前配置下都不能被当作已验证可达的成功契约。

结论：当前实现关闭了自己等待的终态来源，45 秒误判是确定性结果，不是偶发现象。

## 5.2 根因二：`SEEventTTSEnded` 只代表合成结束

当前日志显示 `delegatedTTSSynthesisEnded` 在回答提交约 3 秒后出现，而真实声音之后仍继续播放。因此：

```text
SEEventTTSEnded != 扬声器播放完成
```

不得把 `SEEventTTSEnded` 直接改成 `audioFinished`。否则会重现上一版“声音还在播，麦克风和页面已提前恢复”的问题。

## 5.3 根因三：超时恢复绕过业务状态机

文件：

```text
DreamJourney/Sources/Modules/Echo/EchoViewController.swift
DreamJourney/Sources/Modules/Echo/EchoViewModel.swift
```

播放失败分支当前大致执行：

```text
handleLivePlaybackFailure
-> 显示“本轮语音播放未完成，已继续聆听”
-> recoverLiveCaptureAfterPlaybackFailure
-> prepareEchoCaptureAudioSession
-> DialogEngineManager.resumeRecorder()
-> viewModel.beginVoiceInteraction()
```

此时 ViewModel 仍处于 `speaking`。`beginVoiceInteraction()` 会提交：

```text
EchoTurnIntent.voiceCaptureStarted
```

而 reducer 明确只允许从 `idle`、`starting`、`replied` 进入 `listening`，从 `speaking` 提交 `voiceCaptureStarted` 会被拒绝。

因此页面文案虽然被手工改为“已继续聆听”，实际 `EchoTurnPhase` 仍可能是 `speaking`。后续 final ASR 即使抵达，`userTurnAccepted` 也会被拒绝。

## 5.4 根因四：`resumeRecorder()` 的成功值不能证明发送了恢复指令

`DialogEngineManager.resumeRecorder()` 当前逻辑为：

```swift
guard isDialogActive, isRecorderPaused, let engine else {
    return isDialogActive && !isRecorderPaused
}
```

火山本地 TTS 路径没有通过 App 的 `pauseRecorder()` 设置 `isRecorderPaused = true`。因此恢复路径调用 `resumeRecorder()` 时，可能直接返回 `true`，但没有发送 `SEDirectiveResumeRecorder`。

这意味着当前 `liveCaptureResumed` 日志只能证明调用链走完，不能证明：

- SDK 收到了 ResumeRecorder；
- Recorder 确实重新运行；
- 下一句能形成 final ASR。

## 5.5 根因五：正常完成和失败恢复使用了两套不完整路径

正常完成路径会：

```text
markEchoReplyDelivered
-> replied
-> beginVoiceInteraction
-> listening
```

但它没有统一确认 recorder 是否可接收下一轮。

失败路径会尝试恢复 recorder，却没有先执行：

```text
replyInterrupted -> listening
```

两个路径各完成了一半，导致状态、音频和 UI 不能原子收敛。

## 6. 修复目标与不变量

### 6.1 最终目标

每一轮必须满足：

```text
用户 final ASR
-> /echo/answers
-> 回答文本
-> 火山 TTS
-> 可验证的真实播放终态
-> 合法结束 speaking
-> 恢复同一 Live 会话的 listening
-> 下一句 final ASR
```

### 6.2 必须保持的不变量

1. `/echo/answers` 是 Live 与文字问答共同的唯一语义回答入口；
2. 正式记忆只由后端回答链路读取，火山不直接生成事实答案；
3. 火山继续负责 Live ASR 和语音输出，不改成 Apple TTS；
4. 同一时刻只能有一个可听音频所有者；
5. 不允许火山内置播放器和 App PCM 播放器同时出声；
6. `SEEventTTSEnded` 不得直接等同于播放完成；
7. timer 只能用于发现卡死，不得伪造播放成功；
8. 用户未手动关闭 Live 时，单轮异常不得关闭整个 Live；
9. 用户主动关闭后，任何迟到回调都不得重新打开麦克风；
10. UI 显示 listening 前，业务状态必须已经是 listening；
11. 同一 receipt 的恢复动作最多执行一次；
12. 旧 replyID、旧 lifecycle、旧 account generation 的回调不得影响当前轮次。

## 7. 必须先完成的技术决策门

当前不能继续假定 3019/3020 可用。执行者必须从下面两条路线中选择一条，并通过对应证据门；不得混搭。

### 7.1 路线 A：继续使用火山内置播放器，恢复可靠状态回调

这是对当前低延迟路径改动最小的优先验证路线。

执行要求：

1. 仅在 `answerAuthority == .dreamJourneyBackend`、`sessionLifetimePolicy == .userControlledLive`、火山本地可听路线下，验证开启 `SE_PARAMS_KEY_DIALOG_ENABLE_PLAYER_AUDIO_CALLBACK_BOOL` 后是否稳定产生 3019/3020；
2. `SEPlayerAudioData` 只作为观测，不得在 App 再次播放；
3. 如果该回调持续产生静音 buffer，可以忽略 payload，但必须确认不会引起重复声音、CPU 异常、内存增长或音频会话抖动；
4. 真机必须确认 3019 在可听开始附近出现、3020 在真实声音结束后出现；
5. 只有满足上述条件，3019/3020 才能作为正式终态契约。

路线 A 验证失败时，不允许保留“偶尔能收到”的实现进入验收。

### 7.2 路线 B：App 接管本轮 PCM 播放并拥有确定终态

如果当前 SDK/指令路径无法稳定提供播放器终态，应改为 App 自己播放火山返回的解码 PCM：

1. 关闭火山内置可听播放器，避免双重出声；
2. 开启 decoder PCM 回调；
3. 收集并校验当前 replyID 的 PCM；
4. 使用一个 App 播放器播放；
5. 以 `AVAudioPlayerDelegate` 或 `AVAudioPlayerNode` completion 作为真实终态；
6. 结束、失败和打断必须从同一个 player owner 收敛；
7. 复用现有 `DialogPCMPlaybackController` 前，先验证 PCM 非空、非静音、采样率正确；
8. 不得同时保留火山内置声音。

当前文件中已存在未接入主路径的 `DialogPCMPlaybackController`、`appendDelegatedClientDecodedPCM(...)` 和 `playDelegatedClientDecodedPCM(...)`。可以小范围复用，但不得仅为了使用旧代码而扩大修改。

### 7.3 路线选择建议

1. 先做路线 A 的最小配置实验和事件日志；
2. 如果 3019/3020 稳定且不影响音频质量，采用路线 A；
3. 如果任一核心条件不满足，停止修补内置播放器回执，转路线 B；
4. 不允许用文字长度、固定延迟或 `SEEventTTSEnded` 冒充路线 A/B 的终态。

## 8. 必须实施的状态恢复修改

## 8.1 建立统一终态恢复入口

正常完成、用户打断、供应商失败和看门狗中断应最终进入一个受控恢复协调器，例如：

```swift
enum EchoLivePlaybackTerminalCause {
    case finished
    case userInterrupted
    case providerFailed(code: String)
    case watchdogInterrupted
}
```

名称可调整，但必须按 cause 执行不同 reducer 意图。

### 正常播放完成

```text
验证 receipt / route / replyID / lifecycle
-> 结束播放回执
-> markEchoReplyDelivered()
-> speaking -> replied
-> 确认/恢复 recorder
-> beginVoiceInteraction()
-> replied -> listening
-> 启动 60 秒用户无输入计时
```

### 用户打断或可恢复播放失败

```text
验证 receipt / route / replyID / lifecycle
-> 确保播放器停止或中断指令已接受
-> viewModel.resumeVoiceInteractionAfterReplyInterruption(...)
-> speaking -> listening
-> 确认/恢复 recorder
-> 保持 listening，不再调用 beginVoiceInteraction()
-> 启动 60 秒用户无输入计时
```

注意：`replyInterrupted` 已经直接进入 `listening`。随后再次调用 `beginVoiceInteraction()` 会从 `listening` 提交 `voiceCaptureStarted`，仍会被 reducer 拒绝，因此不能机械复用正常完成分支。

### 不可恢复失败

只有在 Provider 会话或音频会话确实无法继续时，才允许：

```text
failure -> failed/error
```

此时 UI 不得显示“已继续聆听”。

## 8.2 UI 文案只能由真实状态驱动

禁止先调用：

```swift
renderVoiceStatus(text: "本轮语音播放未完成，已继续聆听", ...)
```

再尝试恢复状态。

正确顺序：

1. 完成 reducer 迁移；
2. 确认音频会话与 recorder 可用；
3. 确认当前 lifecycle 仍有效；
4. 再渲染 listening 文案。

如果任何步骤失败，应显示真实失败状态，不得宣称“已继续聆听”。

## 8.3 修正 recorder 恢复契约

不要再用一个 `Bool` 同时表达：

- 已发送 ResumeRecorder；
- recorder 本来就未暂停；
- Provider 会话仍活动。

建议返回强类型结果，例如：

```swift
enum DialogRecorderResumeOutcome: Equatable {
    case directiveSent
    case alreadyRunning
    case sessionInactive
    case directiveRejected(code: Int)
}
```

如果不希望新增类型，至少必须分别记录上述状态并在调用方正确判断。日志中的 `liveCaptureResumed` 只有在业务状态和 recorder 契约都成功后才能输出。

## 8.4 看门狗语义

45 秒看门狗可以保留为“永久卡死保护”，但不能把到期当作播放失败后立即开麦的充分条件。

到期后必须：

1. 发送中断；
2. 确认中断指令被接受；
3. 清理当前 reply 的播放状态；
4. 执行 `replyInterrupted -> listening`；
5. 恢复/确认 recorder；
6. 才显示继续聆听。

如果中断指令被拒绝，应停止或轮换 Provider 会话，不能在未知播放状态下直接恢复麦克风。

## 8.5 播放中插话

播放开始终态修复后，`isAISpeaking` 必须与真实可听播放一致。用户在播放中产生有效语音时：

1. 打断当前 reply；
2. 停止旧音频；
3. 旧 reply 的迟到 finish 不得结束新轮次；
4. 用户语音形成 final ASR；
5. 新问题进入 `/echo/answers`。

不要仅根据 `isDelegatedClientTTSSubmissionPending` 接受所有临时 ASR 为插话，否则扬声器回声可能误中断自己。应结合可靠的 audible-start 状态和 AEC 结果。

## 9. 预期修改文件

主要文件：

```text
DreamJourney/Sources/Services/DialogEngineManager.swift
DreamJourney/Sources/Modules/Echo/EchoViewController.swift
DreamJourneyTests/AudioOwnerLeaseModelTests.swift
```

允许小范围修改：

```text
DreamJourney/Sources/Modules/Echo/EchoViewModel.swift
```

只有确实需要给 reducer 增加可表达的恢复意图时才修改 ViewModel。现有 `replyInterrupted` 已满足主要需求，优先复用。

## 10. 单元测试与集成测试

## 10.1 播放终态测试

至少覆盖：

1. submitted -> providerAccepted -> audioStarted -> synthesisEnded -> audioFinished；
2. synthesisEnded 不等于 audioFinished；
3. 未收到可靠 audioFinished 时，不得调用正常完成；
4. audioFinished 只恢复一次；
5. 重复 finish 幂等；
6. finish 早于 start 时终态仍唯一，迟到 start 被忽略；
7. 不同 replyID、route、lifecycle 的 finish 被拒绝；
8. 用户主动关闭 Live 后，迟到 finish 不恢复麦克风；
9. provider callback 关闭时，不能把依赖 3019/3020 的路径标记为可用；
10. 路线 B 若启用，App PCM 播放完成是唯一 finish 来源。

## 10.2 恢复状态测试

至少覆盖：

1. `speaking -> replyInterrupted -> listening`；
2. 播放失败恢复不得从 `speaking` 直接提交 `voiceCaptureStarted`；
3. `speaking -> replyDelivered -> replied -> voiceCaptureStarted -> listening`；
4. failed recovery 后下一条 `userTurnAccepted` 必须成功；
5. UI 只有在 reducer 为 listening 时才显示继续聆听；
6. recorder 恢复失败时不得显示 listening；
7. 同一 receipt 的多个终态只能恢复一次；
8. 看门狗和真实 finish 同时到达时只能有一个胜者。

## 10.3 连续链路测试

使用 fake DialogEngine / fake playback progress 建立最小集成测试：

```text
第一轮 final ASR
-> 回答提交
-> 播放完成或可恢复中断
-> listening
-> 第二轮 final ASR
-> userTurnAccepted == true
-> requestLiveEchoAnswer 被调用一次
```

这条测试是本次最重要的回归用例。只测试 phase enum 不足以证明连续 Live 已修复。

## 10.4 必须保留的现有测试

```text
EchoTurnIntentReducerTests
EchoLivePlaybackReceiptState 相关测试
DialogEngineDelegatedPlaybackStateTests
DialogEngineAudiblePlaybackPolicyTests
AudioOwnerLease / account generation / lifecycle 相关测试
```

如果现有测试把不可达的 3019/3020 当作事实，应调整测试前提并记录原因，不得只删除测试。

## 11. 日志与可观测性

必须继续使用不含用户正文的隐私安全日志。

### 11.1 播放事件

```text
liveAnswerSubmitted
delegatedTTSProviderAccepted
delegatedTTSAudioActivityObserved
delegatedTTSPlayerStarted
delegatedTTSSynthesisEnded
delegatedTTSPlayerFinished
livePlaybackWatchdogExpired
livePlaybackTerminalWon
```

### 11.2 恢复事件

```text
liveRecoveryStarted
liveTurnPhaseTransitioned
liveRecorderResumeRequested
liveRecorderResumeOutcome
liveCaptureResumed
liveRecoveryFailed
```

### 11.3 必要字段

```text
receiptID
turnID
replyID
route
lifecycleGeneration
phaseBefore
phaseAfter
terminalCause
recorderResumeOutcome
answerCharacterCount
elapsedMsSinceSubmit
```

禁止记录回答正文、用户问题、正式记忆内容、手机号、token、API key 或 Provider 凭证。

## 12. 编译与验证顺序

### 12.1 修改前

```bash
git status --short --branch
git diff -- DreamJourney/Sources/Modules/Echo/EchoViewController.swift
git diff -- DreamJourney/Sources/Services/DialogEngineManager.swift
git diff -- DreamJourney/Sources/Modules/Echo/EchoViewModel.swift
git diff -- DreamJourneyTests/AudioOwnerLeaseModelTests.swift
```

### 12.2 修改后

1. 运行目标单元测试；
2. 运行 Echo/AudioOwner/TurnIntent 相关测试；
3. 执行 iOS 无签名编译；
4. 输出目标文件最终 diff；
5. 列明选择路线 A 还是路线 B及证据；
6. 未经用户明确授权，停在这里，不安装、不真机测试、不 commit、不 push。

可使用的无签名编译形式：

```bash
xcodebuild \
  -workspace DreamJourney.xcworkspace \
  -scheme DreamJourney \
  -configuration Debug \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO \
  build
```

执行者应先通过 `xcodebuild -list -workspace DreamJourney.xcworkspace` 核实 scheme，不得盲目复制命令。

## 13. 真机验收标准

真机测试需由用户另行明确授权。授权后至少覆盖：

| 场景 | 数量 | 必须结果 |
| --- | ---: | --- |
| 20 至 40 字短回答 | 5 轮 | 播放结束后 1 秒左右恢复聆听 |
| 100 至 150 字回答 | 5 轮 | 不进入 45 秒看门狗 |
| 200 字左右长回答 | 5 轮 | 声音结束前不抢开麦，结束后能继续 |
| 连续问答 | 10 轮 | 每轮下一句均产生 final ASR 和新回答 |
| 播放中用户插话 | 3 轮 | 旧音频停止，新问题被接受 |
| 播放失败/中断模拟 | 3 轮 | Live 保持，状态真实恢复 |
| 用户主动关闭 Live | 3 轮 | 迟到回调不重启麦克风 |
| 前后台切换 | 3 轮 | 无残余播放、无自动开麦 |

每个正常轮次日志必须出现：

```text
liveAnswerSubmitted
-> 可验证的 audio start
-> 可验证的 audio finish
-> livePlaybackTerminalWon cause=finished
-> phase speaking->replied->listening
-> liveCaptureResumed
-> 下一轮 ASR isFinal=true
```

可恢复中断轮次必须出现：

```text
terminal cause=interrupted/watchdogInterrupted
-> phase speaking->listening
-> recorder outcome 可解释
-> 下一轮 ASR isFinal=true
```

以下任一情况都视为未通过：

- 声音结束后仍等待 45 秒；
- 显示“继续聆听”但 reducer 仍是 speaking；
- 只有 interim ASR，没有 final ASR；
- 下一句没有调用 `/echo/answers`；
- 声音仍在播放时麦克风提前恢复；
- 同一回答播放两遍；
- 通过固定延迟伪造完成。

## 14. 明确禁止的伪修复

以下做法不能作为完成：

- 把 45 秒改短或改长；
- 在 `SEEventTTSEnded` 处直接调用 `audioFinished`；
- 恢复按字符数计算 1.5 至 5 秒的 completion fallback；
- 只修改 Toast 文案；
- 只调用 `viewModel.beginVoiceInteraction()` 而不检查 reducer 接受结果；
- 把 `resumeRecorder() == true` 当作 recorder 指令已发送；
- 在播放器状态未知时直接恢复麦克风；
- 同时播放火山内置音频和 App PCM；
- 改 DeepSeek、正式记忆或 `/echo/answers`；
- 改用 Apple TTS；
- 删除 replyID、route、lifecycle 或 account generation 校验；
- 为通过测试而删除现有状态机测试；
- 覆盖 Narrative 或其他并行修改。

## 15. 推荐实施顺序

1. 核对分支、HEAD、dirty files 和目标文件 diff；
2. 用现有代码复核本文五项根因；
3. 先补“speaking 假恢复”和“第二轮 final ASR 可被接受”的失败测试；
4. 增加播放器回调配置与终态来源的一致性测试；
5. 完成路线 A 的最小事件验证实现；
6. 路线 A 不满足契约时，停止继续打补丁并转路线 B；
7. 建立统一终态恢复入口；
8. 修正正常完成、打断、失败、看门狗四条状态迁移；
9. 修正 recorder resume 的可观测结果；
10. 运行目标测试和相关回归；
11. 无签名编译；
12. 输出实施结果和残余风险；
13. 等待用户授权后再进行真机验证、commit、push 或部署。

## 16. 交给 GPT-5.6 Luna 的直接执行指令

```text
请严格按照《2026-09-02-DreamJourney-Live-播放完成回执缺失与假恢复-修复指导.md》继续修复当前工作区。

当前 EchoViewController.swift、DialogEngineManager.swift 和 AudioOwnerLeaseModelTests.swift 已包含上一轮 Luna 的未提交实现，必须在其上继续修改，不得 reset、checkout 或覆盖。先读取 git status、目标文件完整 diff 和当前测试，再核实文档中的五项根因。

本次首先修复两个确定问题：
1. 当前配置关闭火山 player callback，却依赖 SEPlayerFinishPlayAudio 作为唯一终点；
2. 超时恢复从 speaking 直接调用 beginVoiceInteraction，被 EchoTurnIntentReducer 拒绝，造成 UI 假恢复。

先写能够复现以下行为的失败测试：播放终态缺失不得永久卡住；可恢复中断必须 speaking->listening；恢复后第二轮 final ASR 必须被接受并再次进入回答请求。随后做最小范围实现。

不要把 SEEventTTSEnded 当作播放完成，不要恢复按文字长度猜时长，不要只改 45 秒，不要同时启动火山内置播放器和 App PCM 播放器。必须在路线 A（可靠启用 3019/3020）与路线 B（App 单一 PCM 播放并拥有 completion）之间选择一个具有证据的单一音频所有者方案。

保持 /echo/answers、正式记忆、DeepSeek、火山音色、腾讯数字人和 Narrative 代码不变。默认不修改或部署后端，不 commit、不 push、不安装、不真机测试，除非用户另行明确授权。

完成后必须报告：
1. 最终确认的根因；
2. 采用路线 A 还是路线 B及证据；
3. 修改文件和每个关键状态迁移；
4. 新增/更新测试及结果；
5. 编译结果；
6. 未执行事项；
7. 残余风险；
8. 为什么下一轮 final ASR 现在能够被业务层接受。
```

## 17. 执行结果模板

```markdown
# ECHO-LIVE-PLAYBACK-003 执行结果

## 最终根因

## 终态路线选择

## 修改文件

## 播放状态机变化

## Turn 状态机变化

## Recorder 恢复契约

## 测试结果

## 编译结果

## 未执行事项

## 残余风险

## 是否建议进入真机测试
```
