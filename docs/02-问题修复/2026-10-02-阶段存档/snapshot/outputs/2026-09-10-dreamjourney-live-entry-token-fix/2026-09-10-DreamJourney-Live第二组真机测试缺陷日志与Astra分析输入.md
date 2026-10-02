# DreamJourney Live 第二组真机测试缺陷日志与 Astra 分析输入

日期：2026-09-10\
测试范围：B 阶段第二组，Live 正式记忆问答、持续会话、播放打断、会话结束与待确认记忆\
用途：供 Astra 基于当前代码、生产日志和真机现象继续定位并给出修复方案

## 1. 执行摘要

本轮确认 Live 基础实时语音链路已恢复：有声音、会话中未再降级到文字回响、朗读可打断、打断后可恢复聆听、用户可主动结束会话。

但两条核心产品链路仍未通过，均属于发布阻断问题：

1. **Live 没有可靠使用已绑定的正式记忆回答。** 服务端在测试前可以从当前正式记忆构建 20 条事实的 Live 快照，令牌接口和 WebSocket 也成功，但学校、职业等事实问题仍回答“不知道”，另有回答与正式记忆不一致。
2. **Live 会话结束后的整理已终态失败，并非仍在慢慢处理。** 会话、消息和结束动作均已持久化，Candidate Proposal 已提交，但 Candidate Extraction 首次执行即耗尽最大尝试次数，生成 0 条 Candidate 并进入开放的 Dead Letter，因此界面持续提示“本次对话暂未完成整理，请稍后重试”，待确认记忆中也没有记录。

此外观察到 iOS 仍调用已经退役的 `/archive/items` 写入口并收到 409。该问题目前作为相邻缺陷记录，不能直接认定为 Candidate Extraction 失败根因。

## 2. 版本与环境

| 组件 | 本轮版本/状态 |
| --- | --- |
| iOS 工程 | `feature/prd-stitch-ui-adaptation`，HEAD `5fd061fd869edbe1fc13e8535a47880826581934`，包含未提交的 Live 修复 |
| 后端工程 | `main`，HEAD `be9670b6ec05e73ab9562943f402e5a9e1346988`，包含未提交的安全诊断修改 |
| 生产 API | 镜像 `sha256:4964872...`，revision `be9670b6ec05-livefix-20260910` |
| Projection Worker | 本轮已对齐至 API 镜像 `sha256:4964872...` |
| Candidate Extraction Worker | 观察到仍为较早镜像 `sha256:c963...`，存在版本偏移，需核对是否包含当前模型、合同和配置 |
| 正式记忆快照预检 | `ready`，`factCount=20`，`snapshotChars=12354`，`memoryRevision=0` |
| 用户主体 | 已脱敏；本文件不记录手机号、令牌、正式记忆正文或供应商密钥 |

未经另行授权，本轮没有 Git 提交或推送，没有重放 Dead Letter，没有清理或修改历史数据。

## 3. 用户真机反馈

| 序号 | 操作与观察 | 结果 |
| --- | --- | --- |
| 1 | 启动 Live 后有声音 | 通过 |
| 2 | 对话中未出现“语音暂不可用，请使用文字回响” | 通过 |
| 3 | 询问毕业学校，Live 无法回答 | 失败 |
| 4 | 询问职业，Live 无法回答；另有语音回答与正式记忆不一致 | 失败 |
| 5 | AI 朗读途中打断 | 通过，打断生效 |
| 6 | 打断后等待恢复，再次询问职业 | 聆听恢复，但回答“不知道”，失败 |
| 7 | 连续再进行一轮正式记忆问答 | 仍无法命中正式记忆，失败 |
| 8 | 点击停止 | Live 可以结束，但持续提示“本次对话暂未完成整理，请稍后重试” |
| 9 | 进入待确认记忆 | 没有生成本轮记录，失败 |

用户侧结论：

- 声音链路正常。
- 正式记忆相关问题均未正确回答。
- 打断和恢复聆听正常。
- 会后整理未完成，也没有形成待确认记忆。

## 4. 本轮后端请求时间线

以下为同一测试窗口内的脱敏请求顺序。账号主体已删除，保留排查所需业务标识。

```text
POST /voice/realtime-token                                      200
WebSocket /voice/realtime-stream                               accepted / open
GET  .../interview-sessions/current?productSessionId=...       200
POST .../interview-sessions                                    201
GET  .../presentation                                          200
POST .../interview-sessions/{id}/messages                      201  (多次)
GET  .../presentation                                          200  (多次交错)
WebSocket /voice/realtime-stream                               closed
POST .../interview-sessions/{id}/end                           201
GET  .../presentation                                          200
GET  .../interview-review-batches/pending                      200
POST .../interview-review-batches/{id}/acknowledgement         201
POST .../interview-review-batches/{id}/candidate-proposal/admit 201
GET  .../candidate-proposal/status                             200  (多次)
POST /archive/items                                            409  (多次)
GET  .../candidates                                            200
```

关联标识：

| 类型 | 标识 |
| --- | --- |
| Live product session | `echo_live_24795b6c5174f23225a0c1cfaafd3ad6` |
| Interview session | `C351F04D-AAC7-4440-8B5B-609CF32E8684` |
| Review batch | `D694DB28-0E48-5F26-9CBF-18879444146B` |

这组日志可以确认：Live 建连、会话创建、多轮消息写入、会话结束、审核批次确认和 Candidate Proposal admission 均已发生。问题发生在正式记忆提示词真正进入供应商会话的闭环，以及会后的 Candidate Extraction 阶段。

## 5. 缺陷一：Live 正式记忆 Grounding 未生效

### 5.1 已确认事实

1. 测试前 Projection 已修复为 `ready`，可构建 20 条正式事实的快照。
2. `/voice/realtime-token` 返回 200，Realtime WebSocket 成功连接。
3. 后端设计上只允许 Live 使用 `formalMemorySnapshot` 回答事实问题，并把快照放入 `sessionContext`。代码入口：
   - `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:17022`
   - `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:17048`
   - `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:17177`
4. iOS 会读取 `runtimeConfig.formalMemorySnapshot`，并具有将其序列化进 Live `system_role` 的实现。代码入口：
   - `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1478`
   - `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:2534`
   - `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:2684`
5. 真机实际回答仍不能复述学校、职业事实，并出现与正式记忆不一致的语音答案。

因此目前只能确认“快照能够在服务端生成且令牌成功签发”，不能确认“同一快照完整进入火山 StartEngine 请求并在打断、恢复和连续轮次中持续生效”。

### 5.2 Astra 应优先核查的代码疑点

**疑点 A：正式记忆拼接被错误地依赖本地 `config.systemPrompt`。**

iOS 当前只有在 `!config.systemPrompt.isEmpty` 时，才会构造 `fullPrompt`、追加 `buildFormalMemorySnapshotPromptSection()` 并回写 `dialog.system_role`。服务端 `runtimeSystemRole` 和 `formalMemorySnapshot` 即使存在，也仍受这个本地条件控制：

```swift
if !config.systemPrompt.isEmpty {
    var fullPrompt = providerOwnedLive
        ? (runtimeSystemRole ?? config.systemPrompt)
        : config.systemPrompt
    if providerOwnedLive {
        fullPrompt += buildFormalMemorySnapshotPromptSection()
    }
    dialog["system_role"] = fullPrompt
}
```

位置：`DialogEngineManager.swift:2570-2603`。

当前默认 `config.systemPrompt` 看起来非空，但 Astra 需要核对生产运行时是否有配置覆盖、重建引擎或后续状态复位使其为空。更稳妥的逻辑应以 `providerOwnedLive && runtimeSystemRole/formalMemorySnapshot 有效` 作为服务端快照注入条件，而不是依赖客户端默认 Prompt 是否为空。

**疑点 B：缺少从令牌到供应商 StartEngine 的同值闭环证据。**

现有代码具备 `liveSnapshotDecoded`、`livePromptPrepared`、`liveStartEngineSubmitted` 和 `liveStartEngineAccepted` 的脱敏诊断，但本轮设备日志采集会话在输出中断后无法回放，因此尚未取得四个事件的完整关联链。需要重新采集并比较：

- 后端 `liveSnapshotIssued`：`factCount/snapshotBytes/checkpointHash/contextHash`；
- iOS `liveSnapshotDecoded`：相同计数与 hash；
- iOS `livePromptPrepared`：`promptBytes/factCount/promptHash`；
- iOS `liveStartEngineSubmitted/Accepted`：相同 provider session 关联值；
- 打断恢复及下一轮对话后，确认未启动一个丢失正式记忆 Prompt 的新供应商会话。

**疑点 C：供应商合同字段、长度或会话更新覆盖。**

需对照当前火山 SDK `StartEngine` 合同确认最终 JSON 中 `dialog.system_role` 的嵌套位置、大小限制和返回码；同时检查 StartSession/SessionUpdate、重连、打断恢复是否用默认 Prompt 覆盖首次注入的正式记忆 Prompt。不能仅以 SDK 返回“连接成功”证明模型已接收完整 Prompt。

### 5.3 暂不能下的结论

- 不能认定正式记忆数据库没有数据：快照预检已有 20 条事实。
- 不能认定令牌或 WebSocket 失败：本轮均成功。
- 不能仅凭后端存在快照构建代码，认定火山模型实际收到了快照。
- 不能通过恢复旧的逐轮 `ASR -> DeepSeek -> TTS` 链路规避问题；必须保留火山原生 Live 的持续聆听和打断体验。

## 6. 缺陷二：会后整理终态失败，未形成待确认记忆

### 6.1 Candidate Extraction Worker 原始结果

```json
{
  "attempt": 1,
  "businessOutcome": "failed",
  "candidateCount": 0,
  "consumerInboxState": "failed",
  "consumerOutcome": "accepted",
  "deadLetterCause": "maxAttemptsExceeded",
  "deadLetterId": "2a6cf6e4-4b47-56af-8f31-2039caaae625",
  "deadLetterNextAction": "authorizedReplayRequired",
  "deadLetterOutcome": "admitted",
  "deadLetterState": "open",
  "extractionId": "d7de6b5e-d7ae-51ee-a518-ecdff767d7d1",
  "extractionStatus": "failed",
  "jobId": "34209c9f-95b3-59f4-8adb-4e1ded889f8e",
  "jobState": "failed",
  "jobType": "ownerTruth.source.created",
  "mode": "run",
  "operationId": "a6764eff-3137-57ed-bd7e-30731de35547",
  "operationState": "failed",
  "outboxState": "dispatched",
  "reason": "candidateExtractionRetriesExhausted",
  "status": "failed"
}
```

后续 Worker 输出：

```json
{"mode":"run","reason":"noEligibleCandidateExtractionJob","status":"idle"}
```

### 6.2 已确认根因层级

直接原因已经确认：Candidate Extraction 没有生成 Candidate，任务在第 1 次失败后进入开放 Dead Letter，所以不存在可展示的待确认记忆。

当前还不能确认最初异常的业务根因，因为 Worker 在顶层捕获 `Exception` 后直接进入 `_release_retryable_or_terminalize()`，没有记录安全的异常类型、失败阶段或业务错误码：

- `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:822`
- `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:897`
- `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1113`

同时 `AsyncEffectIntent.max_attempts` 默认值为 1，而 Source 创建时没有显式覆盖该值：

- `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/contracts.py:190`
- `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_source.py:72`

这会导致任何一次瞬时模型错误、网络错误、合同解析错误或版本不兼容都立即终态失败。`maxAttemptsExceeded` 是失败收口结果，不是最初异常的真正原因。

### 6.3 Astra 必须完成的定位

1. 使用 `jobId/operationId/extractionId/deadLetterId` 关联 Candidate Worker、DeepSeek Provider 和数据库状态。
2. 查明第一次异常发生在 `input read`、`model request`、`model response parse`、`authority revalidate` 还是 `candidate persist`。
3. 增加不含用户正文的安全日志：`failureStage/errorType/businessCode/retryable/providerStatus/contractVersion/modelId/promptVersion`。
4. 核对 Candidate Worker 的旧镜像是否缺少当前合同、模型配置或 DeepSeek 凭据；先证实再归因。
5. 为可重试的供应商或网络异常设置明确且有限的退避重试；合同不兼容、权限失败等非重试错误仍应 fail-closed。
6. 会话结束成功后，状态机应清楚区分“整理中”“可重试失败”“终态失败/需要处理”，不能对终态 Dead Letter 一直显示“请稍后重试”。

历史 Dead Letter 当前要求 `authorizedReplayRequired`。未经产品/运维明确授权，不得重放该任务，也不得删除或伪造 Candidate 来让界面通过。

## 7. 相邻缺陷：旧 Archive 写入口仍被调用

本轮多次出现：

```text
POST /archive/items 409 Conflict
```

后端对已经切换到 Owner Truth V2 的记忆写入会明确返回：

```json
{
  "code": "legacyArchiveAuthorityRetired",
  "authority": "ownerTruthV2",
  "feature": "ownerTextCaptureV1",
  "requiredRoute": "/v2/vaults/{vaultId}/sources",
  "retryable": false
}
```

代码入口：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:6800`、`app/main.py:18789`。

Astra 需定位是哪条 iOS 会后持久化路径仍发起旧写入，并判断它与 V2 Source 写入是重复兼容写、错误回退还是状态展示依赖。当前证据不足以把 409 认定为 Candidate Extraction 失败原因，但旧写失败不应被无限重试或污染用户整理状态。

## 8. Astra 建议执行顺序

1. **先补证据，不先改答案策略。** 重新采集一轮脱敏 Live 日志，闭环比对快照 hash、Prompt hash 和 provider session。
2. **修复正式记忆注入条件和会话保持。** 确保同一不可变正式记忆快照进入火山会话，打断和连续轮次不丢失或被覆盖。
3. **修复 Candidate Worker 可观测性。** 先取得本轮第 1 次异常的真实阶段和类型，再决定配置、合同或重试修复。
4. **修复一次失败即终态的问题。** 根据错误分类使用有限退避重试，不能简单无限增加重试次数。
5. **清理旧写调用。** V2 Source 成功时不再把 `/archive/items` 409 当作普通可恢复失败。
6. **补自动化反例。** 将“快照已签发但 Prompt 未带事实”“打断恢复后事实丢失”“首次瞬时失败即 Dead Letter”“终态失败仍显示整理中”转为回归测试。

## 9. 验收标准

### 9.1 正式记忆问答

1. 用合成且已审核的学校、职业、饮食偏好事实进行测试，不读取或覆盖真实正文。
2. Live 对三类问题均只能基于当前正式记忆快照回答，允许口语化但不得改变事实。
3. 无依据时明确不知道，不得编造；有依据时不得错误回答不知道。
4. 打断、恢复聆听和至少 5 轮连续对话后仍使用同一已授权快照。
5. 后端签发、iOS 解码、Prompt 提交和 provider accepted 的计数/hash 可关联，日志不含正文和密钥。

### 9.2 会后整理

1. Live 停止后，完整用户表达被持久化并进入一次整场整理任务。
2. 整理成功后生成 Candidate 并出现在“待确认记忆”，正式记忆仍必须由用户审核后写入。
3. Provider 瞬时失败按策略重试；终态失败显示明确可操作状态，不得永久显示“请稍后重试”。
4. 重复结束、重复回调或重试不能生成重复 Candidate。
5. 不允许助手回复、推测或润色内容未经审核进入正式记忆。

### 9.3 Live 体验不可回退

1. 保留火山原生 Live 的低延迟、持续聆听、有声回答和中途打断。
2. 不恢复逐轮 `ASR -> DeepSeek -> TTS`。
3. 文字回响继续走既定 DeepSeek 链路且不朗读；本缺陷修复不得改变该产品边界。

## 10. 当前验收矩阵

| 能力 | 本轮结果 | 是否可发布 |
| --- | --- | --- |
| Live 令牌与 WebSocket | PASS | 是 |
| Live 有声回答 | PASS | 是 |
| Live 中途打断 | PASS | 是 |
| 打断后恢复聆听 | PASS | 是 |
| Live 正式记忆事实一致性 | FAIL | 否，核心阻断 |
| Live 会话与消息持久化 | PASS | 是 |
| 会话结束持久化 | PASS | 是 |
| 整场整理生成 Candidate | FAIL | 否，核心阻断 |
| 待确认记忆可见 | FAIL | 否，核心阻断 |
| 失败状态对用户准确表达 | FAIL | 否 |

## 11. 证据边界

- 本文件中的 HTTP、Worker 结果和业务标识来自本轮生产日志观察；已去除主体、正文、令牌和密钥。
- 真机声音、打断、回答内容和界面状态来自用户本轮现场反馈。
- 本轮设备控制台日志流在输出中断后无法回放，因此没有把缺失的 `liveSnapshotDecoded/livePromptPrepared` 事件写成“未发生”；它们属于下一轮必须补采的证据。
- `Candidate Worker 旧镜像`、Prompt 注入条件和供应商会话覆盖目前是高优先级核查项，不是已经证实的最终根因。
- 没有执行历史任务重放、数据清理、生产数据修改、Git commit 或 GitHub push。
