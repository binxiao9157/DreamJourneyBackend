# DreamJourney Live 答案已生成但未朗读详细修改指导

> 文档日期：2026-08-28（Asia/Shanghai）\
> 文档用途：交由独立 Codex 实施、测试和验证\
> 修复对象：iOS Live 回响的答案文本到火山 TTS 播放链路\
> 文档结论：回答 A 已生成，问题位于火山指令提交之后、实际合成与播放之前\
> 后端影响：无，默认禁止修改和部署后端

## 1. 文档优先级

本文是 [`2026-08-27-dreamjourney-live-echo-query-and-audio-bugfix-guide.md`](../2026-08-27-dreamjourney-live-echo-query-and-audio-bugfix-guide.md) 的 TTS 专项收敛文档。

2026-08-27 文档继续约束：

1. Live ASR 的 Canonical Query；
2. 文字和 Live 共用 `/echo/answers`；
3. 正式记忆 Projection 是唯一事实源；
4. 音频路由必须只有一个所有者；
5. 播放必须具备 started/finished 回执。

本文覆盖旧文档中对以下问题仍保留的开放假设：

- Live 回答无声的直接修复方式；
- `ChatTtsText + UseClientTriggerTts` 是否继续保留；
- 同供应商降级究竟使用什么指令。

如有冲突，Live TTS 提交方式以本文为准。

## 2. 已确认的问题边界

### 2.1 已经正常的部分

当前问题发生时，以下步骤已经完成：

1. 用户在 Live 中说出问题；
2. Fire ASR 形成最终文本；
3. iOS 调用 `POST /echo/answers`；
4. 后端从当前身份的正式记忆 Projection 检索；
5. DeepSeek 根据正式事实组织回答 A；
6. iOS 收到非空回答 A；
7. UI 可以显示或持有 A；
8. iOS 调用 `DialogEngineManager.submitLiveAnswerText(A)`。

因此本次禁止继续调查或修改：

- 正式记忆里有没有学校信息；
- `/echo/answers` 是否统一；
- DeepSeek 是否生成了另一段回答；
- Prompt 是否需要改写；
- 回答是否要进一步润色。

### 2.2 唯一需要修复的部分

现象是：

```text
答案 A 已存在
→ A 已交给 SpeechEngine 的本地 send 接口
→ send 返回 SENoError
→ 没有收到可证明朗读开始的事件
→ 用户听不到 A
```

故障边界是：

```text
engine.send(...) 接受指令
        ↓
火山服务是否开始合成
        ↓
SDK 是否收到 TTS 音频
        ↓
内建 Player 是否开始播放
```

`SENoError` 只能证明本地 SDK 接受了指令，不能证明后三步成功。

## 3. 当前代码证据

核对本文时的 iOS 基线：

```text
branch: feature/prd-stitch-ui-adaptation
HEAD:   843ecb19 fix: centralize echo audio session ownership
```

当前工作树并不干净。执行者必须先重新查看：

```bash
git status --short --branch
git diff -- DreamJourney/Sources/Services/DialogEngineManager.swift
git diff --cached -- DreamJourney/Sources/Services/DialogEngineManager.swift
git diff -- DreamJourney/Sources/Modules/Echo/EchoViewController.swift
git diff --cached -- DreamJourney/Sources/Modules/Echo/EchoViewController.swift
```

### 3.1 Live 回答交付

文件：

```text
DreamJourney/Sources/Modules/Echo/EchoViewController.swift
```

函数：

```text
deliverLiveEchoAnswer
```

当前行为：

1. 将回答保存为 `pendingAIText`；
2. 创建 `EchoLivePlaybackReceipt`；
3. 调用 `DialogEngineManager.shared.submitLiveAnswerText(...)`；
4. 方法返回 `true` 后显示“正在回响”；
5. 等待 `onTTSPlaybackStarted` 才确认实际播放开始；
6. 超时后执行 `handleLivePlaybackStartTimeout`。

这一层已经具备基本的播放回执和超时模型，不应删除或绕过。

### 3.2 当前 Live TTS 提交方式

文件：

```text
DreamJourney/Sources/Services/DialogEngineManager.swift
```

函数：

```text
submitLiveAnswerText
```

当前依次发送：

```text
SEDirectiveEventChatTtsText
SEDirectiveDialogUseClientTriggerTts
```

对应纯逻辑计划：

```text
DialogDelegatedLiveTTSSubmissionPlan.steps
    [.answerText, .clientTrigger]
```

当前代码把第二条指令解释为“关闭文本输入并开始 TTS”，但本地 SDK 头文件只表明它选择“由客户端触发 TTS”，并没有给出足以证明当前调用顺序正确的运行时契约。

当前单元测试：

```text
DialogDelegatedLiveTTSSubmissionPlanTests.testAnswerTextIsClosedByClientTrigger
```

只验证了代码自己定义的数组顺序，没有验证火山真的开始合成或播放。这个测试把未经真机证明的假设固化成了测试，不构成有效证据。

### 3.3 已验证能出声的同会话路径

同一个 `DialogEngineManager` 已有两处使用：

```text
SEDirectiveEventSayHello
```

1. `sendGreetingIfNeeded()`：Live 会话建立后播放开场白；
2. `submitPendingTextReplyPlayback()`：文字回响把任意后端回答交给 Fire 播放。

用户已经确认 Live 开场白能够正常出声。由此可知，在当前账号、票据、音色、Live 会话和本地播放器配置下，`SayHello(content:)` 是当前项目中已经得到运行时证明的“文本进入火山并产生声音”路径。

`SayHello` 只是 SDK 事件名称。现有文字回响代码已经用它提交任意回答文本，而不是只提交固定问候语。

### 3.4 工作模式必须保留

引擎在 `configureEngine` 中使用：

```text
SEDialogWorkModeDelegateChatTtsText
```

它用于保证 DreamJourney 后端是唯一回答权威，避免火山 Provider LLM 抢先回答。

本次不得因为改用 `SayHello` 就切回 `SEDialogWorkModeDefault`。Live 开场白已经证明 `SayHello` 可以在当前委托式会话中工作。

## 4. 当前链路与目标链路

### 4.1 当前失败链路

```mermaid
flowchart LR
    A["后端答案 A"] --> UI["iOS 收到 A"]
    UI --> C["ChatTtsText(A)"]
    C --> T["UseClientTriggerTts"]
    T --> OK["send 返回 SENoError"]
    OK --> X["没有 TTS Start"]
    X --> Y["没有 Player Start"]
    Y --> Z["用户听不到 A"]
```

### 4.2 目标链路

```mermaid
flowchart LR
    A["后端答案 A"] --> UI["iOS 收到 A"]
    UI --> S["当前 Fire Live 会话"]
    S --> H["SayHello(content: A)"]
    H --> SUB["submitted"]
    SUB --> TS["TTS Sentence Start"]
    TS --> PS["Player Start"]
    PS --> PF["Player Finish"]
    PF --> NEXT["保持会话并继续下一轮"]
```

目标链路的语义是：

1. DeepSeek 只负责生成回答 A；
2. Fire 不生成、不改写 A；
3. Fire 只把 A 合成为当前角色音色；
4. 使用现有 Live 会话，不新建第二条并发 Fire 会话；
5. 播放完成后继续使用同一个用户控制的 Live 会话。

## 5. 强制不变量

### INV-TTS-001：回答文本唯一

一次 `/echo/answers` 返回的 A 是该轮唯一语义回答。TTS 失败时不得重新请求 LLM，也不得生成 B。

### INV-TTS-002：火山只负责朗读

交给火山的 payload 必须是：

```json
{"content":"A"}
```

不得把 A 放进 `ChatTextQuery`、`ChatRagText` 或任何会触发 Provider LLM 的入口。

### INV-TTS-003：保持同一个 Live 会话

提交 A 时必须满足：

```text
isDialogActive == true
sessionLifetimePolicy == userControlledLive
answerAuthority == dreamJourneyBackend
engine != nil
config.enablePlayer == true
```

不得为了朗读 A 临时创建第二个 Fire DialogEngine。

### INV-TTS-004：提交成功不等于播放成功

状态必须区分：

```text
answerReceived
submitted
ttsStarted
playerStarted
playerFinished
timedOut/failed
```

### INV-TTS-005：回答只展示一次

`pendingAIText`、`onTTSStarted` 和 `viewModel.receiveAIReply` 必须继续避免重复插入同一回答。修改指令后不得让 UI 同时展示 API 回答和 TTS 回调文本两次。

### INV-TTS-006：一轮只有一个音频所有者

选择 `.volcengineLocalTTS` 后必须保持 Fire Player 开启；选择 `.tencentDigitalHuman` 后不得再走 Fire 本地播放。不得破坏现有 `EchoLiveAudioRouteLease`。

### INV-TTS-007：失败不关闭 Live

朗读失败时保留文字 A，清理本轮播放回执，恢复可听状态，并允许用户继续下一轮。不得重新请求回答，不得把 Live 会话直接销毁。

### INV-TTS-008：正式记忆与回答生成不可变

本次修改不得触碰 Source、Candidate、MemoryVersion、Projection、Citation、DeepSeek Prompt 或 `/echo/answers`。

## 6. 详细实施方案

### 6.1 修改 `submitLiveAnswerText`

文件：

```text
DreamJourney/Sources/Services/DialogEngineManager.swift
```

保留现有前置 guard 和 JSON 编码，删除两步循环：

```text
ChatTtsText
UseClientTriggerTts
```

改为在当前活动会话中单次发送：

```swift
let result = engine.send(SEDirectiveEventSayHello, data: payload)
```

要求：

1. payload 继续使用 `{"content": normalized}`；
2. `result != SENoError` 时立即返回 `false`；
3. `result == SENoError` 时只记录 `submitted`，不得记录“已播放”或“朗读成功”；
4. 日志事件名称必须反映 `SayHello` transport；
5. 日志只能记录长度、hash、source 和 traceID，不记录完整回答；
6. 不调用 `SEDirectiveDialogUseClientTriggerTts`；
7. 不调用 `SEDirectiveEventChatTtsText`；
8. 不切换 `answerAuthority`；
9. 不重建引擎；
10. 不停止当前会话。

建议日志：

```text
liveAnswerTTSSubmissionAttempt
liveAnswerTTSSubmitted
liveAnswerTTSSubmissionRejected
```

### 6.2 删除错误的提交计划

删除或停止使用：

```text
DialogDelegatedLiveTTSSubmissionStep
DialogDelegatedLiveTTSSubmissionPlan
```

不要为了给一个常量写单元测试而新增同等作用的空壳抽象。直接调用一条已经验证的 SDK 指令更清晰。

同时删除或替换：

```text
DialogDelegatedLiveTTSSubmissionPlanTests
```

该测试不能证明运行时音频结果，不应继续保留。

### 6.3 保留播放回执

文件：

```text
DreamJourney/Sources/Modules/Echo/EchoViewController.swift
```

以下结构和方法原则上不重写：

```text
EchoLivePlaybackReceiptState
beginLivePlaybackReceipt
acknowledgeLivePlaybackStarted
completeLivePlaybackReceipt
handleLivePlaybackStartTimeout
```

继续使用：

```text
SEEventTTSSentenceStart
SEPlayerStartPlayAudio
SEPlayerFinishPlayAudio
SEEventTTSEnded
```

作为真实运行时证据。

如果现有事件顺序造成 `TTSEnded` 早于 `PlayerFinish`，以 `PlayerStart` 作为“用户确实开始听到声音”的成功回执，以 `PlayerFinish` 作为本轮播放完成回执。

### 6.4 麦克风与打断策略

Live 会话下不要复用文字回响的 `PauseRecorder` 逻辑。

原因：

1. 文字回响是一次性播放会话，没有持续麦克风需求；
2. Live 必须保持用户控制的单次会话；
3. 当前 Live 已配置 AEC，并已有用户打断 AI 的逻辑；
4. `sendGreetingIfNeeded` 在 Live 会话中直接调用 `SayHello`，没有为开场白新建会话。

本次先保持 Live 现有 Recorder 策略不变。只有真机证据显示 A 被麦克风重新识别为用户输入时，才单独修复回声/打断，不得在本任务中预先关闭 Live 麦克风。

### 6.5 超时处理

当前 `livePlaybackStartTimeout` 为 6 秒。实施者先保留或通过真机数据调整，不得只凭主观直接删除。

最低要求：

1. 从 `SayHello` 返回 `SENoError` 开始计时；
2. `SEPlayerStartPlayAudio` 到达后取消启动超时；
3. 超时后显示文字 A；
4. 不重新调用 `/echo/answers`；
5. 不再次提交同一 A，除非产品明确增加“重试播放”命令；
6. 清理本轮 receipt 和 `pendingAIText`；
7. Live 会话继续可用。

如果要调整阈值，建议先记录：

```text
answerReceivedAt
directiveAcceptedAt
ttsSentenceStartAt
playerStartAt
playerFinishAt
```

再根据至少 20 轮真机样本调整。目标不是靠延长超时掩盖没有朗读。

## 7. 诊断证据要求

### 7.1 必须记录的非敏感信息

每轮记录：

| 阶段 | 字段 |
|---|---|
| 回答收到 | `turnId`、`contextTraceId`、answer length、answer hash |
| 路由 | `routeLeaseId`、`volcengineLocalTTS/tencentDigitalHuman`、player enabled |
| 提交 | directive=`SayHello`、SDK return code、elapsed ms |
| TTS | sentence start、TTS ended、elapsed ms |
| Player | player start、player finish、elapsed ms |
| 失败 | timeout、engine error、session failed、route mismatch |

禁止记录：

- 完整问题；
- 完整回答；
- 正式记忆正文；
- 手机号、token、票据、appKey；
- 未哈希的用户和家庭身份。

### 7.2 必须区分的结果

```text
submissionRejected   SDK 本地拒绝指令
submittedNoTTS       指令接受但没有 TTS Start
ttsNoPlayer          有 TTS Start 但没有 Player Start
playerStarted        用户已经开始听到声音
playerCompleted      播放完成
engineFailed         SDK/连接/会话明确报错
```

不得再用一个“提交成功”覆盖以上所有状态。

## 8. 测试修改

### 8.1 删除无效测试

删除：

```text
DialogDelegatedLiveTTSSubmissionPlanTests.testAnswerTextIsClosedByClientTrigger
```

原因：该测试只验证自定义数组，不能证明 SDK 契约。

### 8.2 保留和补充纯状态测试

保留：

```text
EchoLiveAudioRoutePolicyTests
EchoLivePlaybackReceiptState 相关测试
DialogAudioSessionOwnershipPolicyTests
```

补充至少以下状态测试：

1. 没有匹配的 `PlayerStart` 时不能完成 receipt；
2. Fire receipt 拒绝腾讯回调；
3. 腾讯 receipt 拒绝 Fire 回调；
4. 已开始后允许匹配的 `PlayerFinish` 完成；
5. 超时清理后，迟到的旧回调不能完成新一轮 receipt；
6. 连续两轮使用不同 receipt id，不串轮。

如果现有私有类型不便测试，只进行最小可见性调整，不要为测试引入新的全局框架。

### 8.3 编译验证

至少完成：

1. iOS Debug 真机目标编译；
2. 受影响单元测试；
3. 全量 DreamJourneyTests 在环境允许时执行；
4. 检查 `rg` 不再发现生产路径调用 `SEDirectiveDialogUseClientTriggerTts`；
5. 检查 `submitLiveAnswerText` 不再调用 `SEDirectiveEventChatTtsText`。

建议检查命令：

```bash
rg -n "SEDirectiveDialogUseClientTriggerTts|SEDirectiveEventChatTtsText" DreamJourney DreamJourneyTests
rg -n "submitLiveAnswerText|SEDirectiveEventSayHello" DreamJourney DreamJourneyTests
```

SDK 头文件中的枚举声明可以继续存在，不属于产品代码残留。

## 9. 真机验收

### 9.1 前置条件

1. 使用当前可登录测试账号；
2. 身份切换为“自己”；
3. 正式记忆中存在毕业学校；
4. 文字问答能返回该学校；
5. Fire Live 开场白能正常出声；
6. 音频路由选择 `.volcengineLocalTTS`；
7. 当前测试不启用腾讯数字人音频所有权。

### 9.2 TC-TTS-001：事实问题有声回答

提问：

```text
我是哪个大学毕业的？
```

验收：

1. `/echo/answers` 返回与文字问答相同事实；
2. UI 展示回答 A；
3. 火山朗读的语义内容就是 A；
4. 不出现火山生成的第二段回答 B；
5. 日志依次出现 submitted、TTS start、Player start、Player finish；
6. 播放后可继续下一轮。

### 9.3 TC-TTS-002：连续五轮

连续进行五轮问答。

验收：

1. 每轮只调用一次 `/echo/answers`；
2. 每轮只提交一次 A；
3. 不重复展示；
4. 不重复播放；
5. 不串用上一轮 A；
6. Live 会话不中断。

### 9.4 TC-TTS-003：用户打断

在 A 播放过程中说出新问题。

验收：

1. 现有打断策略仍有效；
2. 不把 A 的扬声器声音当成新用户问题；
3. 新问题形成新的 turnId；
4. 上一轮迟到回调不能完成新一轮 receipt。

如果当前 SDK 的 `SayHello` 不支持打断，记录为明确限制，不得私自退回 Provider LLM 或 Apple TTS。

### 9.5 TC-TTS-004：播放启动超时

使用测试注入或断开播放回调模拟“提交成功但不出声”。

验收：

1. 进入 `submittedNoTTS` 或 `ttsNoPlayer`；
2. 文字 A 仍保留；
3. 不重新生成回答；
4. 不重复提交 A；
5. Live 可以恢复下一轮。

### 9.6 TC-TTS-005：重新打开 Live

关闭再重新进入 Live。

验收：

1. 开场白正常；
2. 新回答正常朗读；
3. 不播放上一会话残留 A；
4. 不继承上一会话 receipt 或 timeout。

## 10. 性能指标

本次至少采集：

```text
T_submit = directiveAcceptedAt - answerReceivedAt
T_tts    = ttsSentenceStartAt - directiveAcceptedAt
T_audio  = playerStartAt - directiveAcceptedAt
T_play   = playerFinishAt - playerStartAt
```

建议验收目标：

| 指标 | 目标 |
|---|---:|
| `T_submit` | 本地 p95 < 50 ms |
| `T_tts` | 真机 p95 < 1.5 s |
| `T_audio` | 真机 p95 < 2.0 s |
| 无声超时率 | 20 轮中 0 次 |
| 重复播放率 | 0 |
| 回答被重新生成率 | 0 |

这些指标只衡量 A 到语音播放，不包含 ASR 和 `/echo/answers` 生成耗时。

## 11. 失败分支和决策门

### 11.1 `SayHello(A)` 正常出声

按本文完成，删除旧两步指令，保留回执、超时和日志。

### 11.2 `SayHello(A)` 返回非零错误

记录：

- SDK code；
- session state；
- answer authority；
- player enabled；
- engine/session error callback。

先核对调用线程和会话状态。不得修改回答 A 或后端。

### 11.3 `SayHello(A)` 返回成功但仍没有 TTS Start

这说明问题已经从旧指令组合进一步收窄为供应商会话能力或 SDK 版本问题。此时：

1. 保留完整非敏感日志；
2. 使用同会话开场白做 A/B 对照；
3. 比较开场白和回答提交时的会话、Player、AudioSession 状态；
4. 将最小复现提交给火山技术支持；
5. 不在本任务中自动引入独立 TTS WebSocket。

### 11.4 是否引入独立火山流式 TTS

只有在以下条件同时成立时，才另立架构任务评审：

1. 同会话 `SayHello(A)` 经真机证明不可用；
2. 火山确认委托式 Dialog SDK 不支持该场景；
3. 产品接受维护 ASR 会话和 TTS 连接两条供应商通道；
4. 已设计音频所有权、打断、AEC、延迟和重连策略。

本次不得直接实施。

## 12. 明确非目标

本次不得顺带修改：

1. 正式记忆结构；
2. 检索、Citation 或 Projection；
3. DeepSeek API、模型或 Prompt；
4. 回答风格和措辞；
5. ASR Canonical Query；
6. Candidate 整理和待确认记忆；
7. 腾讯数字人口型协议；
8. Voice Clone；
9. Apple TTS；
10. 登录、白名单、家人管理；
11. 后端部署；
12. Git 分支整理或历史重写。

## 13. 推荐实施顺序

1. 阅读当前 Git 状态和两层 diff；
2. 记录当前旧链路真机事件，确认缺少哪一级回调；
3. 在 `submitLiveAnswerText` 中把旧两步指令替换为单次 `SayHello(A)`；
4. 删除错误的两步计划和无效测试；
5. 保留并补齐 playback receipt 测试；
6. 补齐非敏感诊断事件；
7. 运行定向测试；
8. 编译 iOS；
9. 用户要求后再安装真机；
10. 执行 TC-TTS-001 至 TC-TTS-005；
11. 输出实际时序日志和性能数据；
12. 未经用户要求不 commit、不 push、不部署。

## 14. 完成定义

只有全部满足以下条件才能宣布任务完成：

1. Live 回答 A 仍来自 `/echo/answers`；
2. `submitLiveAnswerText` 不再使用 `ChatTtsText + UseClientTriggerTts`；
3. A 通过当前 Fire Live 会话提交；
4. 不创建第二个 Fire 会话；
5. 火山没有生成 B；
6. 真机收到 TTS Start、Player Start 和 Player Finish；
7. 用户实际听到 A；
8. UI 不重复展示 A；
9. 播放完成后 Live 可继续下一轮；
10. 失败时保留文字 A 且不重新生成；
11. 单元测试和编译通过；
12. 没有修改或部署后端；
13. 没有回退用户已有工作树修改；
14. 报告 Git diff 和残余风险。

## 15. 源码索引

| 目的 | 文件/符号 |
|---|---|
| Live 回答交付 | `EchoViewController.swift` / `deliverLiveEchoAnswer` |
| 播放回执创建 | `EchoViewController.swift` / `beginLivePlaybackReceipt` |
| 播放开始确认 | `EchoViewController.swift` / `acknowledgeLivePlaybackStarted` |
| 播放超时 | `EchoViewController.swift` / `handleLivePlaybackStartTimeout` |
| Live TTS 提交 | `DialogEngineManager.swift` / `submitLiveAnswerText` |
| 委托式工作模式 | `DialogEngineManager.swift` / `configureEngine` |
| Live 开场白 | `DialogEngineManager.swift` / `sendGreetingIfNeeded` |
| 文字回答 Fire 播放 | `DialogEngineManager.swift` / `submitPendingTextReplyPlayback` |
| TTS 事件 | `DialogEngineManager.swift` / `SEEventTTSSentenceStart`、`SEEventTTSEnded` |
| Player 事件 | `DialogEngineManager.swift` / `SEPlayerStartPlayAudio`、`SEPlayerFinishPlayAudio` |
| 音频路由 | `EchoViewController.swift` / `EchoLiveAudioRoutePolicy`、`EchoLiveAudioRouteLease` |
| 当前无效测试 | `AudioOwnerLeaseModelTests.swift` / `DialogDelegatedLiveTTSSubmissionPlanTests` |
| SDK 指令定义 | `Pods/SpeechEngineToB/pod/classes/SpeechEngine/SpeechEngineDefines.h` |
