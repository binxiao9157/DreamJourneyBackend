# DreamJourney Live 正式记忆接入与会话沉淀修复方案

日期：2026-09-03\
文档类型：缺陷修复设计\
目标执行者：GPT-5.6 Luna\
状态：待开发、部署与真机验证

## 1. 执行摘要

本次修复处理两个用户可见问题：

1. Live 已经具备自然连续对话、低延迟和随时打断，但无法回答正式记忆中已经存在的学校事实。
2. Live 和文字回响的对话都没有进入待确认记忆；文字回响还会显示“本次对话暂未完成整理”。

两个问题不能用同一种修法处理：

| 问题 | 当前结论 | 修复策略 |
| --- | --- | --- |
| Live 不识别正式记忆 | 正式记忆快照已在生产生成且包含学校事实；失败位于 iOS 向火山 StartEngine 交付上下文之后的最后一跳 | 先补可观测性和真实 SDK PoC，再按证据修正字段或压缩表达 |
| 两种对话都不进入待确认记忆 | 根因已确认：PostgreSQL JSONB 参数未适配，创建 interview session 返回 500 | 直接修复 JSONB 绑定，补真实 PostgreSQL 回归测试并部署 |

必须保留已经验证良好的 Live 体验，不允许通过恢复以下旧链路来换取事实回答：

```text
Live ASR -> /echo/answers -> DeepSeek -> 文本回灌火山 TTS
```

## 2. 产品与架构约束

### 2.1 不可改变的模型分工

```text
唯一正式记忆权威：DreamJourney 后端当前有效 Memory Projection

Live 回答：正式记忆会话投影 -> 火山 S2S -> 实时语音
文字回答：正式记忆查询上下文 -> /echo/answers -> DeepSeek -> 纯文字

Live/文字新增表达：逐回合保存 -> 整场结束 -> DeepSeek 整理
                   -> 待确认记忆 -> 用户确认/更正/丢弃
                   -> 确认后更新唯一正式记忆
```

### 2.2 事实和表达边界

- 正式记忆只保存经过用户确认的事实，不做对话式润色。
- 火山和 DeepSeek可以采用不同措辞，但人物、学校、专业、时间、职业、关系、地点和数值必须一致。
- Assistant 回答只作为会话理解上下文，不能作为 Candidate 的事实证据。
- 未确认 Candidate、原始 Source、已撤销事实和其他人物数据不能进入 Live 上下文。
- 一次产品会话是用户从打开 Live/文字会话到主动结束，或 Live 连续一分钟没有有效用户输入后自动结束。
- 一问一答只是回合，不是一次产品会话；不能每回合单独生成待确认记忆。

### 2.3 本次非目标

- 不新增向量数据库、VikingDB、火山知识库或第二套正式记忆。
- 不把正式记忆长期托管给火山。
- 不重构腾讯数字人视频链路。
- 不恢复旧 Live 逐轮 DeepSeek 级联。
- 不改变家庭授权、Publication 或 Visitor 的产品范围。
- 不对正式记忆正文进行文学化加工。

## 3. 当前证据

### 3.1 生产后端

当前生产后端提交：

```text
6a6c6562d0c035226803b1015e812186a07b708e
Bind Live sessions to formal memory snapshots
```

2026-09-03 13:37 左右的生产日志持续出现：

```text
GET  /v2/vaults/{vault}/interview-sessions/current -> 200
POST /v2/vaults/{vault}/interview-sessions         -> 500

psycopg.ProgrammingError:
cannot adapt type 'dict' using placeholder '%s' (format: AUTO)
```

同一窗口内文字问答为：

```text
POST /echo/answers -> 200
```

这证明回答路径和会话沉淀路径彼此独立：文字可以正常回答，但沉淀会话根本没有创建成功。

### 3.2 生产正式记忆快照

对当前测试账户进行了只读核验，只输出统计和引用编号：

```text
schemaVersion: formal-memory-conversation-v1
factCount: 15
schoolFactCount: 4
schoolRefs: FM-005, FM-011, FM-014, FM-015
snapshotChars: 15562
snapshotBytes: 40198
maxChars: 32768
```

因此 Live 学校事实失败不是因为：

- 正式记忆没有学校内容；
- projection 尚未生成；
- `/voice/realtime-token` 无法创建快照；
- 候选记忆尚未确认。

### 3.3 iOS 当前实现

iOS 当前会：

1. 调用 `/voice/realtime-token`；
2. 解析 `echoSession` 和 `sessionContext.formalMemorySnapshot`；
3. 把快照序列化为 JSON；
4. 追加到 `dialog.system_role`；
5. 使用 `SEDirectiveStartEngine` 启动火山会话；
6. provider-owned Live 的 final ASR 不再调用 `/echo/answers`；
7. Live 与文字回响复用 `EchoLiveMemoryCaptureCoordinator` 保存会话。

代码存在“客户端已保存快照”和“StartEngine 返回成功”的日志，但没有证据证明：

- 传入 SDK 的上下文事实数量和长度符合预期；
- 当前 `SpeechEngineToB 0.0.14.6.1-bugfix` 对应资源和模型真正采用该字段；
- 供应商没有按字节、字符或 token 截断；
- 学校事实所在位置实际进入模型有效上下文。

## 4. 缺陷一：会话沉淀失败

### 4.1 根因

`PostgresOwnerTruthConversationRepository.start_interview_session` 向以下 JSONB 字段写入 Python `dict`：

- `owner_truth.conversation_threads.metadata`
- `owner_truth.interview_sessions.metadata`

新增值为：

```python
{"productSessionId": record.product_session_id}
```

即使没有 `productSessionId`，代码也会传入空 `dict`。当前两次 `cursor.execute` 没有调用仓库已经存在的 `_adapt_params`，导致 psycopg 无法自动适配。

同文件的消息插入已经正确调用 `_adapt_params`，因此应复用现有机制，不新增另一套 JSON 编码工具。

### 4.2 最小正确修复

两处 INSERT 都应将完整参数元组传给：

```python
self._adapt_params((...))
```

或显式使用同一仓库约定的 `Jsonb`。优先选择 `_adapt_params`，保持测试环境无 psycopg 时的既有 fallback 行为。

不需要数据库迁移，因为列本身已经是 JSONB，错误只发生在运行时参数适配。

### 4.3 事务与幂等要求

- thread、session 和 command receipt 必须继续处于同一个事务。
- 失败时不能留下半创建 thread 或 session。
- 相同 `commandId` 重试必须返回同一结果，不能新建重复会话。
- `productSessionId` 有值和无值两种情况都必须通过。
- metadata 读取后必须仍是 JSON object，而不是 JSON 字符串。

### 4.4 现有失败对话如何处理

本次 500 发生在 session 创建前，后端没有保存这些失败对话的最终转写，因此无法从服务器自动补整理。修复上线后只能保证新会话正确沉淀。

客户端后续应增加短期可靠 Outbox，避免网络或后端故障时丢失已经得到的 final transcript。Outbox 不保存音频，只保存必要的最终文本、角色、顺序号和幂等标识，并按账户隔离、加密和限期清理。

### 4.5 UI 状态修正

当前 `.unavailable` 把以下阶段混为一谈：

- 会话创建失败；
- 回合保存失败；
- 会话结束失败；
- 审核批次确认失败；
- Candidate admission 失败；
- Candidate 整理失败或超时。

应至少区分以下内部原因码：

```text
sessionStartFailed
turnPersistFailed
sessionFinalizeFailed
reviewBatchAcknowledgeFailed
candidateAdmissionFailed
candidateExtractionFailed
candidateExtractionTimedOut
```

用户文案必须反映事实：

| 状态 | 用户文案原则 |
| --- | --- |
| 会话仍在进行、后台暂时重试 | 对话继续，不声称已经整理完成 |
| 用户尚未结束文字会话 | 提示可继续输入或“结束并整理” |
| 已完成 finalize，正在提取 | 显示“正在整理本次对话” |
| 审核批次已 ready | 才显示“已进入待确认记忆” |
| 无新增用户事实 | 显示“本次没有需要确认的新记忆” |
| 最终失败且没有可靠重试 | 明确说明本次内容未保存或未整理成功 |

## 5. 缺陷二：Live 未采用正式记忆

### 5.1 当前链路

```text
iOS 点击 Live
  -> POST /voice/realtime-token
  -> 服务端读取当前正式记忆 projection
  -> 构建 FormalMemoryConversationSnapshot
  -> token 返回 checkpoint/contextHash/snapshot
  -> iOS 解析 snapshot
  -> iOS 将完整 JSON 追加到 system_role
  -> SEDirectiveStartEngine
  -> 火山 S2S 直接理解和回答
```

前五步已经有静态代码和生产数据证据。当前缺口是最后三步缺少可验证证据。

### 5.2 为什么不能直接恢复 `/echo/answers`

恢复逐轮 `/echo/answers` 会重新引入：

- ASR 完成后等待后端检索；
- 等待 DeepSeek 生成整段文字；
- 再把文字回灌 TTS；
- 打断、播放完成和恢复聆听的双状态机冲突。

这会直接破坏用户已经确认正常的低延迟、连续会话和随时打断。因此它只能保留在文字回响，不允许回到 Live 关键路径。

### 5.3 先补证据，禁止猜字段

增加以下隐私安全事件，禁止记录正文：

| 事件 | 必需字段 |
| --- | --- |
| `liveSnapshotIssued` | contractVersion、factCount、snapshotChars、snapshotBytes、checkpointHash、contextHash |
| `liveSnapshotDecoded` | schemaVersion、factCount、snapshotChars、checkpointHash、contextHashMatch |
| `livePromptPrepared` | promptChars、promptBytes、promptHash、factCount、formatVersion |
| `liveStartEngineSubmitted` | SDK version、model/resource profile code、config shape version、promptHash |
| `liveStartEngineAccepted` | directive return code、provider connection hash |
| `liveFirstResponseObserved` | time-to-first-audio、provider session hash |

生产日志禁止出现：

- 正式记忆 statement；
- 完整 prompt；
- 完整 StartEngine JSON；
- 用户转写和 Assistant 正文；
- token、AppKey、资源密钥或音频字节。

现有无条件打印 `configJSON` 的代码必须删除或改成上述摘要日志。

### 5.4 火山字段有效性 PoC

必须使用当前真实的：

- iOS SDK 版本；
- resource ID；
- 模型版本；
- 音色组合；
- 后端 WebSocket 代理；
- provider-owned/default Live 模式。

使用隔离测试人物创建以下合成正式事实：

```text
F01：本科毕业于 A 大学计算机专业，2016 年毕业。
F02：硕士毕业于 B 大学软件工程专业。
F03：职业是产品经理。
F04：姐姐叫 C。
F05：未记录最喜欢的球队。
```

验证三种问法命中 F01，并验证 F05 不编造。不得用“字段名看起来正确”或 StartEngine 返回 0 代替事实命中测试。

若 `system_role` 不生效，Luna 必须：

1. 查验当前 SDK 和当前服务版本对应的官方示例或可运行 Demo；
2. 找到供应商正式支持的会话级上下文字段；
3. 在最小 PoC 中验证；
4. 再把字段映射回产品代码。

找不到可验证字段时应停止并报告供应商能力阻塞，不能自行发明参数，也不能偷偷恢复 DeepSeek 级联。

### 5.5 上下文表达优化

当前快照同时包含 `coreFacts` 和 `dimensionSummaries`，相同事实可能重复；完整 JSON 还有 schema、hash、状态和来源 ID 等机器字段。它适合合同校验，不一定适合实时模型理解。

只有在确认会话字段确实生效后，才进行紧凑化：

```text
【已确认正式记忆】
版本：<checkpoint-hash>
规则：以下事实是唯一依据；未知时明确说不知道。

FM-001｜身份｜<事实>
FM-002｜家庭关系｜<事实>
FM-003｜教育｜<事实>
...
```

紧凑化要求：

- 只读取 `coreFacts`，不重复加入 `dimensionSummaries`；
- 按确定性顺序输出，保留每条 `ref`；
- 不改变 statement，不由模型再次总结；
- 不加入 Source、Candidate 或 Assistant 回答；
- 相同 checkpoint 生成相同 prompt hash；
- 不静默截断任何仍被声明为“已加载”的事实；
- 上下文超出真实 PoC 得到的安全预算时，明确失败并引导使用文字回响。

当前测试账户快照约 15.5K 字符、40KB。这个数值低于后端自定义 32K 字符限制，但不能据此推导火山模型一定全部采用，必须以真实容量测试确定安全预算。

### 5.6 会话版本规则

- Live 启动时固定一个 `projectionCheckpoint`。
- 会话进行中正式记忆变化，不热更新当前火山会话。
- 下次新开 Live 才载入新 checkpoint。
- 重连仍使用原产品会话的 checkpoint，并重新鉴权。
- 人物切换必须结束旧 Live，再申请目标人物的新票据和新快照。

## 6. Live 与文字回响的共同沉淀规则

### 6.1 逐回合持久化

- 只保存 final 用户转写；partial 不进入事实链路。
- Assistant final 文本可作为整理上下文保存，但标记为 `assistant`，不能作为事实证据。
- 保存使用独立串行队列或 actor，不阻塞音频、SDK 回调和主线程。
- 每个回合必须有稳定 `turnId`、`sequenceNumber` 和幂等 command ID。
- 重试沿用相同标识，不得重复创建回合。

### 6.2 整场只整理一次

触发条件只有：

- 用户主动关闭 Live；
- Live 在真正 listening 状态累计一分钟没有用户输入；
- 用户在文字回响中明确选择“结束并整理”；
- 用户离开文字会话且产品规则明确将离开视为结束。

流程：

```text
停止收音/停止接受新输入
  -> 等待已排队 final 用户回合获得持久化结果
  -> 幂等 end/finalize
  -> 一个 Source
  -> 最多一个审核批次
  -> DeepSeek 从用户表达提取 Candidate
  -> reviewReady/noCandidates/failure
```

### 6.3 哪些内容应生成 Candidate

| 内容 | 是否可作为 Candidate 证据 |
| --- | --- |
| 用户主动讲述的新经历、知识、关系、偏好或情绪事实 | 是 |
| 用户只询问已有正式记忆 | 否 |
| DeepSeek 或火山给出的回答 | 否 |
| Assistant 的推测、建议或润色 | 否 |
| partial ASR | 否 |
| 用户明确纠正已有事实 | 是，进入冲突/更正审核，不直接覆盖 |

### 6.4 文字回响入口语义

当前实现为便于复用，把文字回响也建成 `.live` entry mode。这不会导致本次 500，但会混淆审计和统计。

修复应优先保持协议稳定：

- 如果现有 `naturalInput` 能同时保存 Owner 和 Assistant 回合，文字使用 `naturalInput`；
- 如果当前合同强制 `captureMode=live` 与 `entryMode=live` 对齐，先记录 `channel=typedEcho` 元数据，不为了命名新增数据库枚举和迁移；
- 只有确实需要持久化级别区分时，再完整扩展 iOS、后端合同、校验和测试。

不得为了修正统计名称破坏本次 P0 修复。

## 7. 可靠性与错误处理

### 7.1 最小修复层

- session start 失败时保留内存中的待发回合，进行有界重试。
- 用户关闭时，不得因为 coordinator 已进入 terminal 状态而静默丢弃队列。
- 后端恢复后按原 command ID 重试。
- 只有持久化和整理真实完成后才显示成功。

### 7.2 生产可靠层

在本轮 P0 通过后增加短期加密 Outbox：

```text
account lease hash
productSessionId
turnId
sequenceNumber
role
final text
occurredAt
idempotencyKey
retry state
```

约束：

- 不保存音频；
- 不跨账号复用；
- 退出登录、账户撤权或过期后清理；
- 成功回执后删除；
- 大小和保留时间有上限；
- 不允许 Outbox 内容直接进入正式记忆，仍需服务端整理和用户审核。

## 8. 测试策略

### 8.1 后端

必须覆盖：

- PostgreSQL session start，`productSessionId` 有值；
- PostgreSQL session start，`productSessionId` 为空；
- metadata 读取仍为 object；
- 相同 command 重试幂等；
- start -> owner turn -> assistant turn -> end 全链路；
- acknowledge -> admit -> status 最终为 `reviewReady` 或 `noCandidates`；
- 一个产品会话最多一个审核批次；
- Assistant-only 内容不能产生 Candidate；
- `/voice/realtime-token` 快照统计、hash 和字段合同；
- `/echo/answers` 回归保持 200。

仅使用 InMemory repository 的测试不足以证明 JSONB 问题已修复。必须增加真实临时 PostgreSQL 测试或隔离 smoke。

### 8.2 iOS

必须覆盖：

- contract v5 解析 snapshot、checkpoint、contextHash；
- Live prompt formatter 的确定性和去重；
- Live final ASR 不调用 `/echo/answers`；
- typed 仍调用 `/echo/answers` 且不朗读；
- Live/typed 都将 final Owner turn 送入持久化协调器；
- partial 不持久化；
- Assistant 回合标记正确；
- start 失败不会伪装成“整理中”或“已进入待确认”；
- finalize exactly once；
- barge-in、连续会话、音频 owner 和一分钟计时不回归；
- 日志不含正文和完整配置。

### 8.3 真机

真机测试必须同时证明：

1. 学校事实回答正确；
2. 未知事实不编造；
3. 连续对话和打断正常；
4. 新事实在关闭后进入一个待确认批次；
5. 用户确认后才更新正式记忆；
6. 下一次新 Live 使用更新后的 checkpoint 回答新事实。

## 9. 发布顺序

```text
阶段 A：后端 JSONB 修复与测试
  -> 部署后端
  -> 生产隔离 session smoke
  -> 确认 500 消失

阶段 B：iOS 隐私安全诊断
  -> 编译安装
  -> 一次 Live 事实 PoC
  -> 判断字段未生效还是上下文表达问题

阶段 C：按证据修正 SDK 字段或紧凑 prompt
  -> 自动化测试
  -> 真机事实、延迟和打断回归

阶段 D：会话沉淀端到端验收
  -> Live 新事实
  -> typed 新事实
  -> 整场关闭
  -> 待确认
  -> 确认/丢弃
  -> 正式记忆和下一会话验证
```

后端 JSONB 修复可先独立发布。Live grounding 修复必须经过真机证据后再宣称完成。

## 10. 回滚策略

- JSONB 参数适配没有 schema 变化，可通过回滚应用镜像恢复；但旧版本会重新出现 session 500，因此回滚只用于新版本引发更严重故障。
- Live prompt 修改应保留短期 feature flag，用于在同一 provider-owned S2S 内回退到旧 prompt 表达，不能回退到逐轮 DeepSeek。
- 若供应商上下文字段不可用，保持文字回响可用，并明确将 Live 正式记忆能力标记为 blocked；不得让 Live 在无事实上下文时假装具备正式记忆能力。

## 11. 风险

| 风险 | 控制 |
| --- | --- |
| 修复持久化时阻塞音频线程 | 网络写入只走独立串行队列，不 await 实时回调 |
| 紧凑化改变事实 | 只做确定性格式化，不调用模型改写 statement |
| Provider 静默截断 | 用真实容量曲线确定预算，超预算明确失败 |
| 日志泄露正式记忆 | 只记录 hash、数量、长度、状态和耗时 |
| 重试产生重复会话或 Candidate | 沿用 command/message ID，服务端幂等，finalize exactly once |
| Assistant 幻觉进入记忆 | Candidate 提取只把 Owner final turn 作为事实证据 |
| 修复破坏打断 | 将 barge-in、连续 20 轮和回答后立即说话列为阻断性回归门禁 |

## 12. 最终完成标准

同时满足以下条件才可关闭缺陷：

1. 生产 session start 500 消失。
2. Live 和 typed 的 final Owner turn 均有持久化回执。
3. 整场结束最多生成一个审核批次。
4. Live 不调用 `/echo/answers`，仍能命中正式记忆学校事实。
5. typed 继续通过 `/echo/answers` 命中相同事实。
6. 两种回答措辞可以不同，但事实元组一致。
7. 未知事实不编造。
8. Live 低延迟、连续聆听和随时打断没有回归。
9. 日志不泄露正文、密钥和完整配置。
10. 用户确认后更新唯一正式记忆，丢弃后不更新。
