# DreamJourney Live 长回答朗读中断本地交付报告

## 1. 状态

- 已确认代码机制修复：PASS。
- 音频/OwnerTruth 及全测试目标回归：PASS，最终全目标 620/620。
- 模拟器 UIQA：PASS，但无真实 provider 和可听音频。
- 真实 SDK 静默长答、iPhone 扬声器、车载蓝牙：NOT_RUN。
- 历史停音唯一根因：仍未知，不能归因于蓝牙、回声或文字翻页。
- 综合结论：`A_LOCAL_INCOMPLETE`。不标 `READY_FOR_DEVICE_RETEST`，不宣布现场停音已关闭。

## 2. 已确认机制与修改

### 2.1 迟到事件与回复身份

- `DreamJourney/Sources/Services/DialogEngineManager.swift`
  - `DialogProviderInterruptionState`：同一已完成 Q 的 ASRInfo/ASRResponse/Confirmed 迟到事件不再发 `ClientInterrupt`；真正的新 Q 仍只打断一次。
  - `DialogProviderTurnCorrelationState`：question/reply 独立绑定，迟到 Q1 reply 不得把当前 Q2 回退；缺身份事件保持不确定，不强绑当前 reply。
  - `DialogProviderCanonicalReplyTextState`：每个 reply 独立累计，避免旧答文本污染新答。

### 2.2 句段与整答完成分离

- `DialogProviderReplyPlaybackState` 分开记录 synthesis terminal 与 player segment 活动；只有合成可靠结束且所有已登记 segment drain 后，才产生一次 reply drain。
- 单个 SentenceEnd/PlayerFinish、文字生成完成、固定 sleep 或 UI 更新都不能单独结束整答。
- canonical assistant 文本完成与 playback outcome 分离；声音后续中断不改写已确定文本，也不重复提交 assistant turn。

### 2.3 canonical 接缝与 UI

- `DialogEngineManager` 通过 typed delegate 输出 `NativeLiveCanonicalTranscriptEvent`。
- Owner 事件由同 question accumulator 归一，assistant 由 reply accumulator 归一；provider 原生路径关闭旧 `onASRResult`/`onTTSStarted` 双重采集，delegated/文字路径保持原行为。
- `DreamJourney/Sources/Modules/Echo/EchoViewController.swift`
  - canonical 先进入 durable 接缝，再独立更新 UI。
  - 用户活动、TTS 开始和生命周期变化会取消旧 inactivity timer；timer fire 复核 lease、lifecycle、generation 和仍为 listening。
  - provider `onTTSFinished` 只消费 manager 已确认的整答完成事件，不根据字数或 UI 分页推断。

### 2.4 可持久安全诊断

- `DreamJourney/Sources/Services/ConversationMemoryManager.swift`
  - 新增账号作用域、容量有界的 native Live 诊断环形记录。
  - 仅记录事件序号、脱敏关联、阶段、回调 ordinal、状态和计数，不记录正文、PCM、token、完整 payload 或车机名称。
- `DialogEngineManager.swift`
  - 记录 callback、接受/丢弃原因、interrupt、synthesis/player/timer 相关安全阶段，供后续真机取证。

## 3. 红绿证据

- 修复前：`red/ios-capture-audio-red.log`。
  - 同问题迟到事件仍会取得中断权。
  - 迟到旧 reply 会把当前 Q2 关联回 Q1。
- 修复后定向证据：
  - `green/ios-capture-correlation-green.log`
  - `green/ios-capture-playback-green.log`
  - `green/ios-canonical-gap-dedup-green.xcresult`
- 最终全 iOS 测试目标：`green/ios-full-test-target-final.xcresult`，620/620，0 fail，0 skip。
- 模拟器页面：`green/echo-continuous-turn-uiqa-smoke/live-fix-uiqa/`，冷启动和进程重建均通过；不作为声音证据。

## 4. A01-A19 结论

- 本地完整 PASS：A04、A05、A06、A07、A15、A18、A19。
- 其余完整场景：NOT_RUN；其中 A01/A02/A08/A09/A10/A11/A12/A16/A17 有相邻机制绿测或代码门禁，但缺设计指定的真实 manager/SDK/可控时钟组合，不升级为 PASS。
- A13/A14 必须在 iPhone 和原车机执行，当前 NOT_RUN。
- 逐项依据见 `reports/A01-A19-checklist.md`。

## 5. 编译、发布与回退

- 模拟器编译与通用 iOS 设备目标编译均通过，证据位于 `build/`。
- 本音频修复自身只需要 iOS 发布，不要求后端音频变更；但若与整场记忆修复组合发布，仍须先发布其只读状态 GET，再发布 iOS。
- 回退只针对 interruption/correlation/canonical text/playback state、typed delegate、UI timer guard 和诊断环形记录代码块。
- 回退不得恢复原生 generic 双入口采集、基于句段 finish 的整答完成、旧 reply 强绑当前问题或 App PCM 第二播放器。

## 6. 最少后续验收

1. iPhone 扬声器：连续三轮“短答 → 至少三句长答 → 短答”，其中一条可听回答超过 60 秒；全程静默无操作，核对首句、中段、尾句和无异常 stop。
2. 独立正向场景：长答中真人说“停一下”，确认立即停音、自动恢复聆听、后续短答正常。
3. 原车蓝牙重复同一组场景三轮；记录实际 route 类型、系统 interruption/route change 和脱敏 SDK 事件，不记录车机名。
4. SDK 无 reply ID、player drain 缺失、断网/恢复分别验证明确 unknown/failed，不允许假完成或整答重播。

这些步骤完成前，真实长回答朗读问题保持现场 `FAIL / DEVICE_RETEST_NOT_RUN`。
