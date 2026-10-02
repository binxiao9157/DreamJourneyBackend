# DreamJourney：iPhone 自动化短场实测与阻塞分析

日期：2026-09-22。性质：真实测试结果与交接依据，不是全部修复完成报告。

## 1. 结论

**自动化短场 FAIL；20 分钟长场 NOT_RUN。** 本轮不是因手机未连接或用户未配合停止。签名信任与媒体音量均已解决，真实 iPhone 已接收合成语音，并取得真实火山 ASR、回复文字和非静音解码音频。

发现两个独立阻塞点：

1. **DJ-LAB-ADM-01：已部署后端的 admission 代码读取不存在的字段，返回 HTTP 500。** 本场正文、end、ACK 已成功，但没有创建候选整理任务，候选和正式记忆均为 0。对应修复已经在本地工作区，当前服务器仍运行旧写法。本场没有进入 DeepSeek 整理，因此不能归因于 DeepSeek 容量或超时。
2. **DJ-LAB-SDK-01：当前 iOS 与实际 SDK 的播放事件约定不匹配。** 第一条用户语音已识别、已答复，但没有收到 App 所等待的原生播放开始/结束事件，页面未恢复聆听，第二条合成语音没有发送。SDK 二进制分发路径也存在对应缺口，需独立修复验证。

工具具备真实故障复现与证据采集能力；“无人参与直到审核、正式记忆、重启读取全部通过”尚未完成真机验收。不得把工具可构建、局部链路通过描述为完整工具真机 PASS。

## 2. 测试范围与场次

- 当前测试/演示账号，由用户授权；只允许处理本次合成数据。没有操作历史候选或正式记忆。
- 真实设备：iPhone 14 Pro Max，iOS 27；有线连接、已解锁、开发者验证完成，实测媒体音量 0。
- iOS 在隔离 `stage-12` 中构建并安装；未修改原 iOS 或后端工作区，未部署、commit、push。
- 本场 runID：`lab-92e44dddd0df449b96e6e642954829d5`。
- 产品场次：`echo_live_0d6dd6b7b4b9c339832d5922aee9a51e`。
- 证据目录：[short-08](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08)。错误与数据库时间使用 UTC；北京时间加 8 小时。
- 输入为 Mac 本地合成的全新读书角事实，经手机实际 SDK 的 STREAM 音频入口按 20 ms 帧发送。没有直接写入 ASR、对话正文、候选或数据库来伪造成功。
- 保留实际 `.provider` 回答模式与 SDK 内置播放器；最终测试仅增加 decoder PCM 的只读观察，player 回调配置保持产品原值。

| 环节 | 本轮结论 | 证据边界 |
|---|---|---|
| 工具本地保护测试 | PASS，16/16 | 包括场次、音频篡改、短场门禁、实际 dylib/资源篡改；最后一项本地补强未重新上手机 |
| 签名构建、安装、实际启动 | PASS | stage-12 实际运行；签名有效期需每次预检 |
| 第一条输入的 ASR、页面文字、解码音频 | PASS | 只有一条实际用户输入，不能写成两轮完成 |
| 实际播放完成、自动恢复聆听 | FAIL | 没有原生播放完成，页面停在“回响正在抵达” |
| 本场正文持久化与同步 | PASS，2 个消息 | 1 owner + 1 assistant；非完整计划短场 |
| 停止、end、ACK | PASS | 服务端 ended、batch acknowledged；ACK HTTP 201 |
| admission、进入待确认记忆 | FAIL | HTTP 500；无 admission、无 job、本场候选 0 |
| 本场候选审核、正式记忆、冷启动正式记忆回查 | NOT_RUN | 前置候选不存在；未调用审核或激活写操作 |
| 物理至少 20 分钟 | NOT_RUN | 短场完整门禁未通过，无 short receipt |
| 65 分钟、独立键盘文字/档案录入 | NOT_RUN | 不在本轮已执行范围 |
| 麦克风拾音、扬声器听感/回声 | NOT_RUN | 静音数字音频输入不等于声学硬件验收 |
| 本场 DeepSeek 候选整理 | NOT_RUN | admission 在创建任务前失败 |
| 部署、历史任务恢复/清理 | NOT_RUN | 没有扩大授权范围 |

## 3. DJ-LAB-ADM-01：服务器尚未运行本地修复

### 已证实事实

停止后的只读核实结果与最初失败快照分开保存：

- 手机 outbox：`closeIntentPersisted=true`，停止水位 2，`pendingTurnCount=0`。
- canonical 两个成员均 `complete`、已 sealed、有正文；issueCount 为 0。
- completion：`phase=admissionPrepared`；end、ack outcome 均 committed；admission 为 outcomeUnknown；保留原 admission command，没有 receipt。
- 服务端 session ended；2 个消息；batch acknowledged，尚未 admitted；本场 jobs、candidates、formal memories 均为 0。
- `2026-09-22T13:28:08.730378806Z`：ACK POST 返回 201。
- `2026-09-22T13:28:08.788683752Z`：`/candidate-proposal/admit` POST 返回 500。
- 服务端异常明确为 `AttributeError: 'OwnerTruthCommandContext' object has no attribute 'authority_epoch'`，位置 `/app/app/services/owner_truth_interview_candidate_proposal.py:369`。

证据：[HTTP/堆栈摘要](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/post-stop-server-http-summary.json)、[类型错误](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/admission-typed-error.json)、[数据库摘要](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/post-stop-server-summary.json)、[outbox 摘要](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/post-stop-outbox-summary.json)、[completion 摘要](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/post-stop-completion-summary.json)。

### 代码差异与根因

运行中的服务仍执行：

```python
authority_epoch=context.authority_epoch
```

但 [OwnerTruthCommandContext](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/domain/owner_truth/source_commands.py:160) 没有这个字段。

本地 [admit_review_batch](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_interview_candidate_proposal.py:330) 已从 `prepared.authority_epoch` 取值并校验，构建 Live identity 时使用 `raw_authority_epoch`。本地不能通过“再增加一个默认字段”掩盖部署错位，更不能把权限 epoch 默认成 0。

| 文件版本 | SHA-256 |
|---|---|
| 运行中容器 | `f6a9c7c7033945a6555da241d978ffbfb8317564992ecbc9b5a5b44d0e3f30f9` |
| 当前本地工作区 | `f89610ac523030ac85d6c85ea706e45ff3951de859255c0c0c213cd867573130` |

来源：[部署文件片段](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/deployed-admission-source-fragment.json)、[本地文件指纹](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/local-admission-source-fingerprint.json)。

这解释了“本地多次通过，真机短对话仍没有候选”：实际调用后端没有使用同一修复版本。该 Live 分支也覆盖短场，所以不是只有长场受影响。这里只定位本轮场次；不能据此重写历史所有失败的根因。

### 后续修复与验收要求

1. 保留并复核现有本地 authority 绑定修复，使用真实 Live admission 路径和隔离 PostgreSQL 复现旧版字段错误、验证新版建立 run/source/admission/job 关系。
2. 必须覆盖短场、长场、合法 epoch、过期 epoch、账号变化、幂等重复及 unknown-write 只读核实；不得去掉权限校验或重发未知 POST。
3. 发布前生成明确的 API、Worker、配置和迁移版本清单。获部署授权后核对实际容器版本与文件/镜像指纹；仅本地 PASS 不代表线上生效。本轮未执行发布。
4. 以全新合成短场验证 admission 真正成功、候选可见；不拿本次未知写场次作为自动恢复试验对象。

## 4. DJ-LAB-SDK-01：真实 SDK 完成事件缺口

### 已证实现场与时序

第一条输入的两个 ASR 检查词均匹配，用户和助手页面文字均出现。实际解码音频非静音，但 `playbackCompletions=0`；TTS 合成结束后 speaking 未归零，最终报 `timeout_ASR_answer_TTS_listening`。

| 指标 | 相对本次运行起点 |
|---|---:|
| 第一条输入开始 | 1.671 s |
| 第一条语音发送完成 | 10.201 s |
| 实际 ASR final | 11.863 s |
| 用户文字显示 | 11.883 s |
| 助手文字显示 | 11.898 s |
| 本轮首段解码 PCM | 11.874 s |
| 工具报告失败 | 103.147 s |

语音发送完至 ASR final 约 1.662 秒，final 至用户文字约 20 毫秒。只有一个样本，不能宣称全部实时性达标。记录的非静音 PCM 合计 816,336 字节包含开场白，不是仅本轮回复的独立字节量。`completedTurns=0` 表示没有一轮通过完整“回答并恢复聆听”断言，不表示完全没有输入和回复。

证据：[原始失败结果](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/result.json)、[本场脱敏事件](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/short-08/diagnostic-events.json)。

### 真实依赖与代码定位

锁定依赖为 `SpeechEngineToB 0.0.14.6.1-bugfix`。头文件声明原生 `SEPlayerStartPlayAudio=3019` 与 `SEPlayerFinishPlayAudio=3020`；实际本场未观察到这两个事件。

[DialogProviderReplyPlaybackState](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1685) 只有在 synthesisEnded、实际 playerStarted，以及活动播放段归零后才返回 drained。缺少 start/finish 时，产品一直 waiting。这与真实页面停住吻合，不能把 TTSEnded 直接等价为实际扬声器播完。

对 SDK arm64 二进制及最终 App 的 `DreamJourney.debug.dylib` 核对发现：

- 内部 PlayerProcessor 产生 `2001/2002` 开始/结束信号。
- 最终链接的 `DialogProcessor::SpeechMessageCallback` 所核对分发路径处理 player PCM 2000 与 decoder PCM 4000 等事件，2001/2002 落入返回分支，没有被转成 App 等待的 3019/3020。
- 最终场次恢复了产品原 player 回调配置，仍复现缺失，不能简单归因于工具新增 player PCM 观察。decoder 观察只记录已解码数据。

证据：[最终链接分发反汇编](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/sdk-inspection/linked-app-dialog-dispatch.asm.txt)、[PlayerProcessor 反汇编](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/sdk-inspection/player-processor.asm.txt)、[真实链接文件指纹](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/sdk-inspection/linked-library-fingerprints.json)。

结论为当前实际 SDK 与 App 依赖的回调约定不兼容，分发缺口是最强定位证据。仍需修复后实测证明原生完成与恢复聆听正常；不能未经验证承诺升级 SDK 就会解决，也不能把该静音 STREAM 现场直接等同于所有普通麦克风场景。

官方 [iOS SDK 接口文档](https://docs.volcengine.com/docs/DoubaoVoice/End-to-endiOSSDKinterfacedocumentation?lang=zh) 可用于核对 STREAM、decoder/player 观察接口；文档或头文件存在某个回调不等于本次链接二进制确实提供该回调。

### 后续修复与验收要求

1. 先固定实际 SDK 包与最终链接指纹，验证回调从 SDK 到桥接层、Manager、Controller 的完整流向。
2. 在受控分支评估供应商兼容版本或正确的真实播放器完成来源。保留 replyID/generation、打断、迟到事件隔离和播放队列排空语义。不得伪造完成、固定延迟置空 speaking，或关闭播放器来使断言通过。
3. 本地必须覆盖分段 TTS、TTSEnded 先/后于实际播放完成、缺失/重复事件、打断、新旧问题交错、停止与账号变化；受影响音频模块加入回归。缺失事件允许明确报错并留证，不能提前声称成功或默默允许下一轮。
4. 真实 SDK 验证必须使用最终签名包，记录真实事件；随后短场两次输入均恢复聆听并完整进入候选、正式记忆，才能开始长场。

## 5. 排除项、限制和早期工具校准

- **不属于本场容量证据**：只有一条实际用户输入。火山已返回 ASR、文字与音频；DeepSeek 尚未接管候选任务。
- **不是正文丢失**：停止后手机和服务器均保留 owner/assistant 两条完整消息。
- **不是候选列表刷新造成当前 0 条**：本场服务端 admission/job 尚不存在。
- **不是 close intent 没落盘**：`result.json` 捕获早于异步关闭链完成；后续只读文件证明 close/end/ACK 已完成。保留两时点证据，不修改原失败文件。
- **不是同一个根因**：SDK 的恢复聆听失败与 admission 500 是两个故障。解决其中之一不能宣称另一项已恢复。
- 初期校准发现 SDK `feedAudio` 以 Int16 样本数计长度，STREAM 要持续发送音频时钟；最终使用单任务持续 20 ms 静音/语音帧，最大观测间隔约 33.35 ms。此前校准场不能合计成计划的多轮短场通过。
- 开场白只有合成结束、非静音解码数据与页面可输入状态；原生播放完成和静音尾段均未观察到。为定位正式用户问答，最终只把开场白当作输入准备条件，**没有**把它标成播放排空 PASS；每条正式用户问答的播放完成断言保持不变并实际失败。
- 早期预检曾有 `database_pool_exhausted` 503。本地同步连接池在 async 中间件内阻塞事件循环的探针可复现风险，但尚不能证明它是线上所有池耗尽的唯一原因；它也不是本次已确认的 admission AttributeError。独立背景证据为 [预检诊断](/Users/gaominge/Documents/liftora/outputs/2026-09-22-live-device-lab/backend-preflight-diagnosis.json)。

## 6. 工具交付与下一轮顺序

工具：[live_device_lab](../../../tools/live_device_lab/README.md)。操作指导：[Sol 真机测试指导](../../../02-问题修复/测试与验收/2026-09-22-Sol-iPhone-Live自动化真机测试操作指导.md)。

本次在真机失败后仅补强主机端校验：schema 2 将完整 App bundle（含真正业务代码所在 debug dylib、SDK/framework、资源和签名材料）纳入指纹，同时核对工具及隔离源代码；旧 schema 1 不可改写后复用。无有效短场凭证时，在安装/复制/启动手机前即拒绝长场。16 项本地测试通过。未再启动真机，因此该校验补强不改变本次短场 FAIL。

下一轮按以下顺序执行：

1. 本地复核 admission 现有修复及 SDK 兼容修复，完成受影响模块回归；设备缺席不阻塞本地工作。
2. 获用户发布指令后更新并核验实际 API/Worker 版本。当前报告不授权部署。
3. 用户主动发起真机后，检查签名有效性与媒体音量，创建新构建、新场次。
4. 同一版本先过真实短场两轮、待确认候选、审核、正式记忆、冷读取；之后才能运行物理至少 20 分钟。本次 `long20-01` 仅准备了音频，没有启动，也没有短场凭证。
5. 未来 65 分钟必须重新执行独立短场门禁。硬件麦克风与扬声器单列声学验收。

本轮 Live 已停止，未自动创建替代场景、重放未知业务写、审核历史候选或清理恢复坐标。保留最小脱敏证据供修复，手机无需为分析继续连接。
