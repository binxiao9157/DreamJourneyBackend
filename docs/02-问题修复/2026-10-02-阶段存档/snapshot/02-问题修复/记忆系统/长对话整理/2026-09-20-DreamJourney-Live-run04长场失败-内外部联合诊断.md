# DreamJourney Live run04 长场失败：内外部联合诊断

问题编号：**DJ-LIVE-RUN04-CAPTURE-01**\
现场日期：2026-09-20，时间统一为北京时间；证据文件内保留 UTC。\
范围：只读核对本场服务器与手机保留文件、核查当前代码和供应商合同、隔离本地诊断。**本文不是已完成修复的报告，也不是业务重试指令。**

## 1. 结论与置信边界

本轮失败位置已经明确：**Live 音频对话继续，但客户端记忆采集/持久化链在第 13 个用户回合后没有继续保存后续内容。** 手机本场 Outbox 和服务器都只保留前 13 个用户回合及对应的 13 个助手消息；没有尾部待上传队列，也没有持久化关闭意图或服务器 end。因而没有形成会后 Source、ACK/admission 和最终候选发布。不能再把这一轮描述成“完整正文已保存，只是 DeepSeek 整理失败”。

本场已执行的两次 DeepSeek 请求均成功；首批会中预整理完成。火山语音 SDK 在手机停止保存之后仍持续回调到约 23:24。**当前证据不支持把本次失败归因于模型达到 20 分钟上限、输出截断或长请求超时。**

真实本地组合已证明一个与现场相容的程序缺陷：同一 canonical 身份收到不同的完成正文，哪怕只差一个句号，Store 抛冲突后，Coordinator 将局部异常升级为整场 `.unavailable`，Controller 随即关闭入口并移除活动采集对象；之后既不收下一轮，也无法正常执行停止交接。其它已冻结回调可能仍持有对象，不能把这一点说成对象必然立即销毁。

必须保留的边界：**这个程序缺陷已经复现，但尚不能证明它就是本场首次触发异常。** 手机环形日志覆盖了 23:09 附近，且未持久化 canonical 错误类别。用户终稿冲突、助手终稿冲突和局部磁盘异常等具体触发仍不能靠截图或合成测试强行定案。

准确状态为：`DEVICE_FAIL / FAILURE_BOUNDARY_CONFIRMED / LOCAL_DEFECT_REPRODUCED / FIELD_FIRST_TRIGGER_UNRESOLVED`。

## 2. 本场事实链

| 时间/阶段 | 已证实事实 | 含义 |
|---|---|---|
| 23:03:34–35 | 手机建立本场 Outbox；服务器 start 成功 | 手机和服务器场次可关联 |
| 23:03:50–23:09:21 | 本场 26 个消息 POST 均返回 HTTP 201 | 前 13 个用户回合及助手消息已送达 |
| 23:07:30–23:07:35 | 第一批 atomExtraction、atomSupport 成功 | 会中预整理确实调用过 DeepSeek |
| 23:09:21 | 手机 Outbox 最后更新时间；服务器最后一条正文时间一致 | 最后一次成功保存发生在约第 6 分钟；首次异常的准确时间仍未取得 |
| 23:10:15–23:24:49 | 手机保留的 SDK 回调继续，含 1,374 次 ASRResponse、19 次 ASREnded 及多轮回答事件 | 后半段语音链仍有活动，不能解释成整个 Live 已断开 |
| 用户手动停止后 | 页面立即且持续显示“当前无法继续整理，原对话已保留” | 该文案对应客户端 `.unavailable`；不能证明整场正文已保存 |
| 服务器只读核对 | session 仍 active；无 close watermark、review batch、Source；Run 为 collecting | 最终候选整理尚未满足启动条件 |
| 候选列表 | 总数仍 46，本场候选 0 | 是上游未完成交接后的结果，不是候选列表自行吞掉 33 轮 |

关联只使用脱敏标识：productSession `5b861a4716ea9f67`、backend session `99707a87175322bd`。手机 Outbox 内 session ID 归一化后与服务器匹配。

### 2.1 手机落盘结果

只复制并分析本场恢复文件，输出白名单元数据；临时原始副本已删除，手机文件未修改。

- `ownerTurnCount=13`
- `lastClientSequenceNumber=26`
- `canonicalMembers=26`，`canonicalTurns=26`
- 26 段全部 `complete`、均有 `deliveryMessageID`
- `pendingTurns=0`，`membersWithoutBody=0`
- start 为 `committed`
- 无 close intent 字段；最后更新 `2026-09-20T15:09:21Z`

这排除了“手机本场 Outbox 已保存全部 33 轮，只剩 20 轮上传不出去”。只能证明本条保存链保留了前 13 轮，不能用界面文案保证后 20 轮完整可恢复。

另有旧 `ConversationMemoryManager` 路径，在接受 UI turnIntent 后记录用户及助手内容，并在 endSession 时保存已接受的最后 20 条。它不保证覆盖全场，也不会自动补进 OwnerTruth Outbox。本次未读取该旧 memory.json，故**不能进一步宣称后半场在手机所有存储里都不存在**；它仅是可能的只读恢复调查线索。

### 2.2 服务器落盘、传输与预整理

API 和 nginx 对应窗口均显示本场 26 个消息 POST 为 201；之后没有本场 messages、live-delivery-status、end。数据库连续序号 26，用户轮数 13。不存在本场 review batch。

本场长记忆 Run：`collecting`，`planned_unit_count=1`，该 unit `completed`，`provider_request_count=2`、`recovery_request_count=0`、`failureCode=null`、`source_id=null`。这是已完成一个私有预整理单元、尚未结束和统一发布的状态。

| DeepSeek 阶段 | 耗时 | 输入 tokens | 输出 tokens | 本地输出预算 | 结果 |
|---|---:|---:|---:|---:|---|
| atomExtraction | 约 3.915 秒 | 2,574 | 1,171 | 4,096 | responseAccepted，finish_reason=stop |
| atomSupport | 约 1.446 秒 | 2,187 | 237 | 4,096 | responseAccepted，finish_reason=stop |

这两次请求没有超时、长度截断或预算耗尽的证据，也未进入合同修复重试。后半场没有送达，不能声称 DeepSeek 已验证全部 33 轮。

### 2.3 原报告中日志推断的修正

会中预整理没有使用与会后候选提取完全相同的阶段 reporter。仅查询 `ownerTruthCandidateExtractionStage` 不能排除预整理已经调用模型。

原报告“没有观察到本场会后候选整理阶段”成立；若进一步解释为“本场从未调用 DeepSeek”则不成立。本次数据库 attempt 和 unit 记录已经补足此盲区。

## 3. 内部代码：为什么单条异常能切断整场

### 3.1 已复现的因果路径

1. `DialogEngineManager` 将 SDK 数据归一化为 canonical member / transcript。用户按 engine generation、owner、questionID 归属，助手按 replyID 归属。
2. `OwnerTruthInterviewLiveTurnOutboxStore.upsertCanonicalTurn` 对已 complete 或已 delivery 的正文要求完全一致。新正文不同则抛 `canonicalTurnConflict`。这一步在保护已确认事实，不应简单改成任意覆盖。
3. `EchoLiveMemoryCaptureCoordinator.appendCanonicalTurn` 对失败未区分“该条终稿冲突”和“整个持久化系统不可用”，统一记录 `persistenceRejected`、`isCaptureOpen=false`、`.unavailable`。
4. `.unavailable` 被标记为终态。真实 `EchoViewController` 关闭 canonical ingress，清空 active 和 retained coordinator。
5. 后续新回合无法登记；用户停止时已经没有正常协调器完成 close intent / end / ACK / admit。音频使用另一套流程，仍可继续。
6. Live 期间页面优先显示语音状态，记忆状态可被缓存；用户按停止后才看到 `.unavailable` 文案，容易误以为错误始于最后一次停止。

代码定位（当前工作区行号）：

- [终稿冲突检查](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:16733)
- [未分类的失败处理](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1668)
- [Controller 终态关闭采集](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:13034)
- [SDK 用户事件归一化](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1422)

### 3.2 真实组合正反例

使用真实 Manager → Controller → Coordinator → 磁盘 → FeatureGate → BackendClient，以 URLProtocol 提供受控响应。只通过 VFS overlay 加入隔离诊断测试，产品源码和工作区测试源码未修改。

装配边界：该 Simulator 构建启用 `UI_QA_SIMULATOR`，使用模拟器 Manager 与生产共享的 canonical parser/router/assistant stream，以及真实 Controller、Coordinator、磁盘、FeatureGate、BackendClient。**没有运行真实 SpeechEngine SDK 的 `handleProviderMessage` 分派分支。** 因此它能证明收到这些 canonical 事件后产品会关闭整场，不能替代供应商真实回调顺序验收。

| 输入 | 诊断结果 |
|---|---|
| 同 questionID，ASR final 和 confirmed 文本完全相同 | 继续 10 个用户回合，end / ACK / admit / pendingReview 完成 |
| 同 questionID，confirmed 仅少一个末尾句号 | persistenceRejected → unavailable；下一回合拒绝；磁盘停在 3 用户 / 6 段；无 end / ACK / admit |

`2/2` 表示正对照及缺陷复现断言均通过，**不能写成修复通过**。

原测试 `testLiveCanonicalConflictDoesNotBlockFollowingOwnerTurn` 只直接调用 Store，捕获前一条冲突后由测试继续写下一条。它绕过 Coordinator 的终态升级和 Controller 的释放逻辑，所以没有覆盖真实缺陷。这是之前绿测不能证明场上正常的具体原因之一。

### 3.3 不应只盯住 QueryConfirmed

手机现存后半场日志没有 3021；不能断言本场收到了 QueryConfirmed 并因标点变化出错。

同一 ASR 身份收到再次完成文本、助手 ChatEnded 与 TTSEnded 为同一 replyID 提交不同完成文本，也会落入相同 Store 规则。需要把“单条冲突升级为整场停止”的结构性缺陷，与“本场究竟哪一种回调首次触发”分开。

两个增量真实组合也已执行：一项仅用同身份 ASR explicit final 的文本变化，不经过 3021；另一项用同 replyID 助手再次提交不同完成正文。每项都包含同正文正对照。不同正文均复现整场关闭、后续 member 拒绝、无 close intent / end / ACK / admit；相同正文均继续完成闭环。结果包为 `canonical-controller-increment-pair.xcresult`。这证明缺陷不依赖 QueryConfirmed，但这些合成回调仍不是本场真实 SDK 顺序的取证。

### 3.4 助手文本流与朗读文本的具体交互风险

当前原生分支中，`ChatEnded`（3016）先提交完整回答并替换 assistant 正文缓冲；`TTSSentenceStart/End`（3008/3009）又更新该缓冲；`TTSEnded`（3011）再按同一 replyID 提交 complete。

缓冲更新只判断“新文本是不是累计全文”或“旧全文是否已经以该片段结尾”。例如缓冲已有“第一句。第二句。”，后来到达 TTS 的第一句“第一句。”，它不是旧全文的尾部，当前算法会追加成“第一句。第二句。第一句。”。下一次 complete 因而与已提交全文不同。

这是[共享正文缓冲算法](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1685)、[TTS 结束提交](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:5636)及[Chat 结束提交](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:5825)形成的具体风险。手机后半场确实同时有这些事件，但断点事件顺序和正文已缺失，不能写成现场已发生的拼接实例。

原生 TTS 分派不在当前 Simulator 编译分支，现有测试钩子也没有该入口。本次没有通过改写产品 Manager 来冒充“真实 TTS 顺序已实测”。重复助手终稿造成的生命周期后果已复现；上述真实原生顺序仍需后续适配层测试与用户发起的设备验收。

## 4. 外部模型与容量、时限核对

### 4.1 DeepSeek

实际生产 adapter 使用 `deepseek-v4-flash`，域名 `api.deepseek.com`；本场两次调用已成功。当前组织/支持调用设置 4,096 输出 tokens、httpx timeout 60 秒。该 timeout 是 HTTP 操作约束，不是整场 Live 的 60 秒上限。

DeepSeek 官方说明超并发会返回 429；等待超过 10 分钟尚未开始推理的请求会被关闭。这是请求级规则，不等于用户能聊天的最长时间。本场两次请求分别约 4 秒、1.5 秒结束，没有 429 或超时证据。[官方限流与保活说明](https://api-docs.deepseek.com/quick_start/rate_limit/)

代码内还有组织请求的条数、正文长度和结果条数校验。这些是我们自己的单次适配器限制；长场管线按单元拆分，不应解释成“用户只能说 8 次”或“整场只能有 8 条记忆”。本场仅 1 个 unit、2 次请求，Run 总预算远未耗尽。

**本场不能以增大 max_tokens 或延长 timeout 作为首要修复：后半段正文根本没有进入该管线。** 这也不代表未来高密度 1 小时场景永远不会触及其它边界；65 分钟端到端验收仍需独立进行。

### 4.2 火山 Live

保留的手机 SDK 日志证明后半段仍接收到 ASR 与回答相关回调。取得的官方文档未提供“活跃语音会话到 20 分钟必断”的规则。此前保存的 2026-09-19 官方文档描述 10 分钟无交互断开；这与持续对话场景不同。模型上下文窗口、会话历史查询上限也不能替代我们逐轮保存正文的职责。

更值得核对的是**事件合同**：官方 API 的 `ChatTextQueryConfirmed` 是文本 `ChatTextQuery` 请求的确认，示例只有 question_id；并未承诺它是语音 ASR 的第二份权威终稿，更未保证两路正文逐字相同。SDK header 定义了 3013 / 3021 数字，但没有补充这种不可变保证。当前代码把 confirmed 和 ASR final 同归 `.complete`，这是我们的适配假设，需要验证，不能归责为供应商违约。[官方 API](https://www.volcengine.com/docs/6561/1594356)；[官方 iOS SDK](https://www.volcengine.com/docs/6561/1597646)

官方 API 本次网页读取受重定向限制；事件合同依据是本机保存的 2026-09-19 官方完整正文与已安装 SDK header。未以别的 ASR 产品文档替代本产品语义。

### 4.3 我们自己的网关、策略和部署

只读核查当前实际部署配置：普通会话 3,600 秒 / 512 MiB；长 Live 配置启用，长档位 7,200 秒 / 1 GiB；单帧 2 MiB。它们均不是 13 轮或约 6 分钟上限。是否命中某个流量限制仍须具体错误证明，不能只凭配置推断；本场后续音频持续也不支持网关整体中断。

认证 access TTL=900 秒，release policy TTL=300 秒。23:08 刷新策略后仍有消息 201；23:20:58 的认证刷新明显晚于 23:09:21 的最后正文。没有本场第 27 条请求返回 401/403 的证据，不能套用之前的 70/48 认证恢复事故。

API 与 Worker 七个关键文件的部署指纹一致，并与当前后端工作区一致。已启用长场管线与对应 Worker；不存在仅因忘记开启开关、忘记部署 0122 就能解释本场的证据。

## 5. 待验证假设与排除项

| 假设 | 当前判断 |
|---|---|
| DeepSeek 对整段长对话超时/截断 | 不符合本场已有证据：仅早期两次调用且均成功，后半段未送达 |
| 火山达到 20 分钟或 13 轮硬上限 | 未取得这种合同或错误证据；保存停于约第 6 分钟，SDK 后续仍回调 |
| 手机已存满，后端上传排队堵住 | 本场 Outbox pending=0，只存 13 用户回合，不是 33 轮积压 |
| end / ACK / admit 之后候选整理失败 | 本场根本没有这些最终业务阶段；应先查采集与停止交接 |
| 同身份完成文本冲突使全场采集退役 | 真实组合已复现，和现场形态吻合；首次现场触发未证明 |
| 单次 member/body/ack 文件读写失败 | 仍可能；当前统一错误码没有保留底层类型，不能排除 |
| 单纯策略 TTL 或 presentation 读取失败 | 传输暂停应映射 syncPaused/statusUnknown，且不阻止 canonical 继续落盘；本场无后续 member/pending，不能由单纯 TTL 解释 |
| 账号归属变化 | 独立校验仍重要，但没有本场切换证据，不能把策略 TTL 等同于账号 lease 变化 |
| 只读确认队列排空时先置 unavailable 再重建 | 另有代码风险，但本场无 delivery-status GET，不得冒充本次根因 |
| 历史任务 UI 覆盖 | 可独立存在，但不能解释手机和服务器同时缺少本场后 20 轮正文 |

## 6. 为什么还不能宣称查到唯一首次触发

本场 NativeLiveDiagnostics 共生成 7,191 条，环形文件仅保留最后 4,096 条，`droppedCount=3095`，最早保留到 23:10:15，晚于最后保存时刻 23:09:21。大量 ASR/TTS callback 和 silenceTimerNotArmed 事件占用了容量。

更关键的是，该文件仅保存音频事件元数据，不保存完整 canonical 状态迁移和错误枚举；canonical 失败被打印成同一种 `persistenceRejected`。因此不能从现存文件还原当时究竟是 owner 冲突、assistant 冲突或具体磁盘失败。

无需为获得结论现在再做一场 20 分钟测试。应先把已复现的采集生命周期缺陷及真实装配测试纳入后续讨论；如要对首次现场触发作确定归因，需要持久保留首个关键错误及前后有界事件，而非单纯加大普通音频日志。

## 7. 后续讨论应遵守的修复边界

本节是由诊断导出的约束，不是授权立即修改产品。

1. 分开处理单条转写/版本冲突、磁盘不可写、权限失效和整场最终状态；不能把所有单条异常都释放整场采集对象。
2. 保护已确认正文，不能直接删除冲突检查或用后来文本静默覆盖；根据供应商实际事件语义确定消息身份、版本和封存时机。
3. 即使个别回合不完整，也要持续保留后续可接收内容和本场恢复坐标，明确标出缺口；不能提前假报全场成功。
4. 停止交接对象的生命周期必须覆盖排空、close intent、end、ACK、admit；既不能丢对象，也不能重放未知结果业务写。
5. 覆盖真实 Controller 释放、同 ID 终稿变化、助手双结束事件、旧回调、磁盘故障、策略刷新和停止时序，而不只测 Store。
6. 已通过的短场持久化、分批整理、跨批合并纠正撤回、B7、审核到正式记忆、未知写和音频链路必须列为受影响回归；不要为改采集而改 Live 音频行为。
7. 本地开发与验证先独立交付。真实 Provider、20 分钟密集场与未来 65 分钟、候选审核到正式记忆，分别列为后续验收；真机始终由用户主动发起，不能因手机未连接而中断本地任务。

## 8. 证据目录和执行边界

本次证据目录：

`/Users/gaominge/Documents/liftora/outputs/2026-09-20-live-run04-device-failure-analysis/evidence/`

- `device-outbox-metadata.json`：本场手机持久化数量、状态、场次关联，无正文。
- `device-native-ring.json` / `device-ring-summary.json`：SDK 安全事件及保留窗口。
- `session-chain.json`：服务器会话、序号、消息数量、无 review batch 的只读证据。
- `current-metadata.json`：本场 Run、unit、两次模型 attempt、tokens 与完成结果。
- `http-chain.jsonl` / `nginx-http.json`：API 和代理端本场请求时序。
- `api-runtime.json` / `worker-runtime.json`：白名单运行配置和源码摘要。
- `deployment-local-fingerprint-comparison.json`：部署与工作区关键文件指纹比较。
- `canonical-controller-pair-analysis.md` / `canonical-controller-pair.xcresult`：真实组合正反例及说明。
- `canonical-controller-increment-pair.xcresult` / `canonical-controller-increment-safe-sequence.log`：不依赖 3021 的两项增量诊断。
- `ios-capture-path-exclusion-and-increment-probes.md`：其它失败路径的排他分析、原生 SDK 测试边界、旧本地存储的保留范围。

本轮没有修改产品代码，没有新的 Live，没有新的模型付费调用，没有重试或重放业务写，没有清理手机/生产/历史任务，没有部署、commit 或 push。手机只读核对已结束，无需继续连接。
