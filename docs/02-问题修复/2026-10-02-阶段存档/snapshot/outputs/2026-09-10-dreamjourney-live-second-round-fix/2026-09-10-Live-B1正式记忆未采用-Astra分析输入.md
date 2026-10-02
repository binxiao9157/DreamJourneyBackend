# DreamJourney Live B1 正式记忆未采用问题单

日期：2026-09-10\
用途：提交 Astra 进行根因复核与修改方案设计\
当前结论：B0 通过，B1 失败，B2-B8 暂停；本轮没有修改代码。

## 1. 用户反馈

在同一场原生火山 Live 会话中，用户连续询问本科、硕士、职业和饮食偏好四项已经存在于正式记忆中的事实。

四轮均完成识别、生成和语音播放，但回答全部为“不知道”。这说明声音、麦克风和基本对话链路可用，正式记忆并未在实际回答中生效。

## 2. 已证实事实

1. B0 正常：有开场白、有声音、进入持续聆听，手动停止正常返回。
2. B1 四个问题均产生有声回答，但四项正式事实均未回答出来。
3. Live 当前答案权威为火山 Provider；四轮日志均进入 `providerOwnedLiveTurnAccepted`。
4. 会话启动前，iOS 成功解码 `formal-memory-conversation-v3` 快照，`contextHashMatch=true`，快照含 65 条事实。
5. iOS 生成的 Live role prompt 非空，日志计数为 8,088 字符、15,310 字节、65 条事实。
6. T06 已证明真实 SDK 最终出站控制帧携带合成 role，实际观察路径为 `dialog.dialog.system_role`，正文和哈希一致。
7. 当前代码中存在逐轮上下文构建函数 `recordEchoContextPacketForUserTurn(...)`，也存在通过 SDK `ChatRagText(3009)` 提交上下文的实现。
8. 当前 Provider-owned Live 的最终 ASR 分支在记录 `providerOwnedLiveTurnAccepted` 后直接返回，没有调用逐轮上下文构建函数。
9. 全仓搜索只找到 `recordEchoContextPacketForUserTurn(...)` 的定义，没有当前调用点。
10. 旧历史版本曾在最终 ASR 后调用该函数；后续在统一文字/Live 答案及恢复原生 Provider Live 的变更中，调用点没有恢复。

## 3. 代码证据

### 3.1 当前 Provider 分支提前返回

[EchoViewController.swift](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:11126)

关键行为：

```swift
self.captureLiveOwnerTurn(text)
if DialogEngineManager.shared.answerAuthority == .provider,
   self.isUserControlledLiveSessionOpen {
    // 记录 providerOwnedLiveTurnAccepted 后直接 return
    return
}
```

该分支保持了火山原生连续 S2S，不会调用 `/echo/answers`，这是正确的产品边界；但它也没有触发当前已经存在的逐轮正式记忆检索/注入。

### 3.2 逐轮上下文构建函数存在但无调用点

[EchoViewController.swift](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:7068)

函数具备账号租约、Owner/Family 身份路由、生命周期和陈旧上下文保护，并最终进入 `submitEchoTurnKnowledgeContext(...)`。

### 3.3 SDK 逐轮 RAG 指令已经实现

[DialogEngineManager.swift](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:2184)

当前实现将查询相关内容编码为：

```json
{"content":"..."}
```

并调用 SDK 的 `SEDirectiveEventChatRagText`（事件 3009）。SDK 头文件也声明了该事件：

[SpeechEngineDefines.h](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/Pods/SpeechEngineToB/pod/classes/SpeechEngine/SpeechEngineDefines.h:1335)

### 3.4 会话启动 role 的实际嵌套

[DialogEngineManager.swift](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:2864)

代码先构造一个内部带 `dialog` 的 `dialogConfig`，随后又包装为：

```swift
let startConfig = ["dialog": dialogConfig]
```

所以 T06 捕获到最终路径为 `dialog.dialog.system_role`。T06 已证明真实发送，不足以证明上游把该嵌套路径作为有效角色字段消费。

### 3.5 历史线索

- `8f0a9aad fix: unify live and text echo answers` 之前，最终 ASR 后曾调用 `recordEchoContextPacketForUserTurn(...)`。
- `ba3c6955 Stabilize Live memory grounding and audio lifecycle` 恢复 Provider-owned 原生 Live 后，Provider 分支直接返回，但没有恢复该调用。

历史只用于定位回归窗口，不直接作为根因结论。

## 4. 尚未证实的假设

以下均需要 Astra 结合锁定版 SpeechEngineToB 合同和代码继续核实，当前不能直接写成根因：

1. **逐轮 RAG 调用缺失**：四个问题没有触发查询相关上下文，只有会话启动时的全量快照。
2. **配置字段嵌套不被上游识别**：SDK 发出了 `dialog.dialog.system_role`，但 Provider 可能要求不同路径。
3. **全量 prompt 采用失败**：8,088 字符、65 条事实可能被截断、稀释或没有获得足够检索优先级。
4. **发送时序问题**：即使恢复 `ChatRagText`，也必须确认它在当前用户轮生成回答前到达并绑定同一轮，不能晚到下一轮。
5. **快照事实选择问题**：日志证明有 65 条事实，但出于隐私未记录正文；仍需用安全方式验证四个查询对应事实确实包含在当前 Owner 的快照/查询结果中。

## 5. 请 Astra 输出的修改方案

请逐项给出结论、证据及修改位置：

1. 锁定版 SpeechEngineToB `StartEngine` 的真实配置合同，确认 `system_role` 的正确最终路径；不得仅凭自建解析器或 SDK 接受返回值判断。
2. 判断是否恢复 Provider-owned Live 的逐轮正式记忆查询，并通过 `ChatRagText(3009)` 注入查询相关上下文。
3. 如果恢复逐轮注入，设计明确的同轮时序、超时、陈旧结果隔离、连续提问和打断行为，不能破坏原生 Live 的低延迟、持续聆听和随时打断。
4. 正式记忆仍是唯一事实权威；不得让未审核 Source、Live 新对话或模型猜测成为正式事实。
5. 文字问答继续由 DeepSeek 回复且不朗读；不得把 Live 恢复为 ASR → DeepSeek → TTS 串行旧链路。
6. 增加可核验但不泄露正文的逐轮证据：query hash、context hash、上下文字节数、SDK 指令返回码、发送时间、首个回答时间及轮次绑定标识。
7. 增加反例回归：会话快照存在但逐轮上下文未发送时，不能把“SDK 已启动”误判为正式记忆采用成功。

## 6. 建议验收条件

修复后重新执行 B1，并同时满足：

1. 四个问题均以当前正式记忆为依据回答，不猜测、不改写事实值。
2. 四轮均有声音，仍由火山原生 Live 输出。
3. 每轮日志可证明查询相关上下文在回答生成前绑定到同一轮，但不记录问题正文和记忆正文。
4. 不能只用 token 200、SDK ACK、快照非空或 T06 发送成功代替实际回答验收。
5. B1 通过后再继续 B2-B8；B1 未通过时保持 `B1_FAIL`。

## 7. 关联证据

- [B1 脱敏设备日志](evidence/live-b1-formal-memory-failure-sanitized-20260910.md)
- [T06 真实 SDK 最终控制帧证据](evidence/t06-real-sdk-final-frame-20260910-2326.md)
- [原修复指导](../../02-问题修复/记忆系统/采集与会后保存/2026-09-10-Sol-Live第二组真机问题设计与修复指导.md)

## 8. 当前停止点

- `T06`: PASS，仅代表真实 SDK 发送合同证据。
- `B0`: PASS。
- `B1`: FAIL，四项正式记忆均未被回答采用。
- `B2-B8`: NOT_RUN，等待 B1 根因方案和修复。
- 本轮按用户要求只整理证据，未修改 iOS 或后端代码，未部署，未提交或推送 Git。
