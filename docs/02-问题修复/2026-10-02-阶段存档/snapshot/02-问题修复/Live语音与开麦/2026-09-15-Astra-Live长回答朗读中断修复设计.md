# Live 长回答朗读中断修复设计

日期：2026-09-15。交付对象：Sol。状态：设计完成；业务代码未修改；修复红绿、真实 SDK、手机真机验收均 NOT_RUN。

**用户最新范围调整：本阶段优先修复手机端原生 Live 交互及关麦后的记忆交接。普通蓝牙按系统音频外设处理，不涉及 CarPlay、车型适配或原车机专项开发。原车机复测不属于本阶段必需验收，未进行该项不阻断手机修复的开发与发布；真实手机和 SDK 的功能验收仍须完成。本文这项范围调整覆盖旧审计稿及旧提示词中的原车机必需门槛。**

关联文档：[Live 整场记忆持续采集与结束交接修复设计](../记忆系统/采集与会后保存/2026-09-15-Astra-Live整场记忆持续采集与结束交接修复设计.md)。可以在同一个任务中开发，按独立修改块和验收交付；共享 canonical transcript 接口先统一。本文是正式交接，outputs 内源码探针和蓝牙审计保留为调查证据；旧蓝牙审计中的专项测试表不作为本阶段实施清单。

## 1. 本次范围与现场约束

用户确认从开始到 08:22 前未手动关闭 Live，是同一场持续对话。手机当时连接普通车载蓝牙播放（用户确认未使用 CarPlay），车内仅用户一人，无其他 App、导航或音乐；长回答先读几个字后停止，文字仍切换；停音前后用户没有说话或操作；随后用户说结束时，短告别又正常发声。不能把本地持久化会话分段或 08:10 后端停止写入误写成用户结束 Live；不能把短 ASR 文本直接定性为用户主动打断或回声。

修复范围为 iOS 原生 provider Live 的事件身份、ASR 触发的 ClientInterrupt、回答/句段完成、文本呈现与静音计时。继续使用火山原生低延迟连续语音与内置播放器。后端记忆采集/策略缺陷由另一设计独立处理，不合并根因或验收。本设计不授权部署、设备安装、生产操作、历史重放、候选确认或 Git 提交。

## 2. 当前证据

### 2.1 历史现场已确认与未知

手机尾部文本：08:22:09 用户 199 字；08:22:10 AI 275 字；08:22:12 用户角色文本 6 字；08:22:14 AI 98 字。图1逐字对应 08:22:14 的 98 字，界面显示聆听与结束按钮；图2逐字对应 08:22:31 的 25 字告别，并显示麦克风图标。

275 字与 98 字没有包含或后缀关系，最长连续相同文本为 6 字。它们讨论同一主题，但缓存没有 provider question/reply/segment ID，不能认定一定是同一模型回答的两个页，也不能据此否定用户观察到同一长回答的停音。08:22:12 的短文本与前条 AI 开头重合是相关线索，不能拿它作为近似文本过滤的依据。

ConversationMemoryManager.swift:609–628 使用 Date() 记录本地接收文本时刻，不是音频首音/末音。234 只保留最近 20 条记录。AI 文本写入可以来自 SentenceStart、SentenceEnd、ChatEnded，275 字已经记录不代表 275 字已经朗读。历史音频因果依旧 UNKNOWN。

EchoTraceStore 为 UserDefaults 的上下文记录，不是音频事件流水。本次手机偏好中未见其 records key；EchoRuntimeDiagnosticsStore 最近 20 条均为 viewWillDisappear 的 runtime release 快照，没有 08:22:10–14 的 TTS/ClientInterrupt 流水。PrivacySafeDiagnostics.log 最终只 print，不能将当前未能读取历史日志表述为当时没有该事件。

### 2.2 本轮确定性真实源 probe

目录：/Users/gaominge/Documents/liftora/outputs/2026-09-15-live-fix-design-analysis/audio/

- audio-source-probe.swift：逐字抽取当前真实 parser、ASR interruption 分支、interruptAI、provider 播放事件尾部、observeProviderReplyEvent，以及真实纯 reducer；基础 SDK/engine/delegate 合作者使用 inert spy。
- source-manifest.json：原文件 SHA256、每个抽取片段原始行号/SHA256，片段没有逻辑改写。
- source-probe-results.json：11 项 MECHANISM_OBSERVED。

这是当前生产源片段/纯函数的受控机制验证，不是完整 UIKit/SDK 装配测试，不是历史现场复现，不是修复后的绿。初次抽取缺少函数末尾括号及直调 swiftc 的 SDK 环境错误已修正；这些编译环境失败不计业务红。

| 当前已观察机制 | 生产源位置 | probe |
|---|---|---|
| 同 question 的迟到 ASRInfo、ASRResponse、QueryConfirmed，只要非空且 speaking，各发送一次 ClientInterrupt | DialogEngineManager.swift:4097–4105、4191–4199、4259–4267；2196–2228 | 三入口分别观察 |
| 未 final 的非空 ASR 也可打断；ID-only 不发 ClientInterrupt | 同上及真实 parseASRResult | 两项观察 |
| 不在 speaking 时不发 ClientInterrupt | 同上 | 正向边界观察 |
| speaking 时同答下一文本段被 UI reducer 拒绝；经过 finished/listening 后又获准 | EchoViewModel.swift:299–318、1600–1608 | 两项观察 |
| native PlayerFinish 和 TTSEnded 分别通知 onTTSFinished | DialogEngineManager.swift:4407–4410、4477–4484 | 重复终态观察 |
| SDK 内置播放器负责音频；应用不重放 PCM | DialogEngineManager.swift:831–840、3175–3201 | 输出策略观察 |
| 实际 observeProviderReplyEvent 先 observeQuestion 再判断旧回复，导致当前 Q2 被晚到 Q1 回退、turnSequence=3、correlation=matched | DialogEngineManager.swift:3636–3671 | 真实 helper 观察 |

完整工程 HEAD 为 11d0d0051b9be3cce57822dd059472d1e2536866，但工作树已有未提交修改，必须以 manifest 中相关源码 SHA256 对齐而非只认 HEAD。现有 EchoViewController 改动不得整体覆盖。

## 3. 修复决策

### 3.1 原生 provider 模式不再用“ASR 非空”自动发送 ClientInterrupt

首选待验证实现分支：对 answerAuthority=.provider 且 lifetimePolicy=.userControlledLive，移除 ASRInfo/ASRResponse/ChatTextQueryConfirmed 三处根据非空文本自动 ClientInterrupt 的分支；由原生 provider 的语音端点/打断机制处理音频。保留显式用户停止、用户明确中断动作、合法退出/后台、安全边界等必要控制。delegated 与单次文字播放路径保持独立原有合同，不能借此一并改动。

互补审计尚未取得当前固定 SDK 版本自动 barge-in 的精确官方合同，因此不能把上述分支直接宣告为已验证默认。必须先采集该 SDK 的真实事件序列，并同时通过“用户静默长答完整播放”和“用户真实开口及时打断并续听”两项真实 SDK 正反门禁，才采用默认。若合同不支持自动打断，则不能恢复“任何非空 ASR 就打断”；必须实现下面的合法新用户事件条件，并阻断不具备可证明身份的自动客户端中断。互补证据见 /Users/gaominge/Documents/liftora/outputs/2026-09-15-live-fix-design-analysis/bluetooth/2026-09-15-原生Live蓝牙路由与SDK音频合同独立审计.md；不能靠当前注释、mock 或用户短文本推断。

即使移除冗余客户端中断，provider 仍可能因输入处理/路由产生不应有的新问题或音频终止，所以这不等于历史问题已修复。严禁以文本相似度、长度阈值、近似重复、AI 前缀匹配或通用静音时长来屏蔽用户发言；相同文字可以是合法新问题。provider 新 question_id 是输入关联证据，不单独证明真人确实开口；静默长答与真人打断必须作为独立正反门禁。

UI 在 provider 检测新用户输入时仍应回到听/想状态，但这必须是更新呈现的 typed 事件，不能靠发送 ClientInterrupt 才驱动状态恢复。

### 3.2 固定 session/question/reply/segment 身份

在 DialogEngineManager 内新增窄范围 NativeLiveEventContext 与 native reply state；优先同文件内部类型，避免公共重构。

- SessionKey：已有 engineGeneration、dialogOperationId、bindingId/账号 lease generation。生产日志只存批准的脱敏关联。
- Question：provider question_id 与该会话内已观察的问题状态；重复同 ID 是同一个问题，不因 ASRInfo→ASRResponse→QueryConfirmed 多次出现而另建轮次。
- Reply：provider reply_id + 所属 question_id；同问题可能有多个有合同依据的 reply，禁止只以 currentReplyID 覆盖后丢弃旧状态。
- Segment：只使用实际 provider 句段标识或可证明的句段边界；若 SDK 不提供 segment ID，可分配本地 ordinal 仅用于记录观察顺序，不能冒充 provider 身份。
- callbackOrdinal：在 manager 串行接收入口增加单调序号，结合接收时冻结的 context，穿透至 UI 异步闭包；执行时验证仍属于当前 session/reply epoch，避免旧 finish 改新答。

必须修正 observeProviderReplyEvent：回复 payload 不得先推进 currentQuestionID 再验证。先对照已经由合法输入事件建立的 question registry；旧问题回复可记录为旧/终止来源，不能重开旧问题或把 turnSequence 倒回。没有 question_id 的回复只能在存在唯一可证明绑定时关联；缺 ID 或歧义应显式 unknown/ambiguous，不能伪装 matched，也不能据此关闭当前音频。

合法新用户事件需要同会话、provider 原始新 question_id/语音开始合同、未被处理、非旧 reply 的附带元数据等条件；ID-only ASRInfo 可以建立输入事件身份，不需要等待文本非空。当前 handler 在 ASRInfo/Response/Confirmed 都调用 observeQuestion，必须统一去重/退役规则。仅不同文本或非空文本不构成新用户证据。

若最终保留客户端自动中断作为某个 SDK 版本的必需适配，所有条件都必须满足：协议明确要求客户端负责；合法新 question 事件已建立；旧 reply 仍有当前播放归属；该输入 epoch 尚未中断过；目标会话/播放 epoch 一致。每个新输入 epoch 最多一次，记录具体原因及 SDK 返回码。缺少证明时不发送破坏性中断，保持 provider 控制与明确诊断。

### 3.3 句段完成、合成完成和真实播放完毕分开

当前 SDK 头文件 SpeechEngineDefines.h:1547–1551 将 PlayerStart/Finish 描述为句段边界；TTSEnded 与 PlayerFinish 分开。不能把每个 3020 或 TTSEnded 当整答播放结束。

新增 reply state 至少包含：question/reply 身份、已知段集合、已接受文本、synthesis terminal、正在播放/已 drained 的段、provider interrupted/failed 状态、是否观察到真实 player activity、关联是否歧义。由唯一 native reply coordinator 输出 replyDidStart、replyTextUpdated、replyDidDrain、replyInterrupted、replyFailed 等 typed 事件。

整答 completed 必须同时满足：该 reply 的合成终态可靠；其最后一个已提交/接受音段已经通过 SDK 可证明的 player drain；无新段或无未解决归属；非被打断/失败。不能以字数、文字生成结束、ChatEnded、固定 sleep、UI 翻页、语音电平为零或一段 PlayerFinish 单独宣布完成。

若 SDK player callback 不带 reply/segment ID，且合同不能保证不重叠/严格队列，就没有足够信息实现上述 completed。先通过隔离 SDK 证据核实事件 payload/顺序或找到真实播放器 drained API；否则保持 completionUnobserved，不能给最后一个“当前 reply”强行绑定旧 finish。必要时只增加有上限的元数据/播放器活动回调以诊断，不录音、不将 PCM 转为 App 播放、不换 SDK/串行 ASR→模型→TTS。更换 SDK 或公共音频路径另行说明具体必要性。

### 3.4 文本更新与音频控制分离

目前 native 音频由 SDK 内置播放器负责，onTTSStarted 被 UI 拒绝不会直接停止 SDK。保留这个分离，修复文字接口使其反映完整回答。

EchoViewModel 新增窄范围 native 接口，例如 beginProviderReply(context)、updateProviderReplyText(context,text,isFinal)、finishProviderReply(context,cause)。同一 reply 的更新不重复触发 .replyStarted，不要求 speaking→listening 后才允许下一句显示。

按已验证的 ChatResponse delta/cumulative 合同聚合整答；SentenceStart/End 文本仅为有身份的备选或补充来源，不能把三种来源无条件拼接造成重复。不能把“有 chatBuffer”当已经显示的证明，因为当前 onChatStreaming 仅写 pendingAIText。主气泡更新同一回答，保留可阅读的全文/滚动；任何文字换页、重绘、滚动不发送 pause/stop/ClientInterrupt，也不释放 audio lease。

Assistant context 记录应采用同一 reply 聚合结果和必要 interrupted/incomplete 状态，避免每段再变成独立“新回答”；完整项和未定稿材料分别按 3.5 的接缝持久化，既有 owner/assistant 角色隔离不得削弱。后端候选语义与历史数据处理不在本音频修改范围内。

### 3.5 与独立记忆修复共享的唯一 transcript 接缝

由本音频/文本身份修改负责定义 NativeLiveCanonicalTranscriptEvent；记忆侧只负责持久化/交付该事件，不能再次从UI或generic TTS回调推断内容与角色。

建议字段：原账号/作用域 lease、providerSessionEpoch（engineGeneration+dialogOperationId）、questionId、replyId（owner可空）、canonicalTurnId、role、text、textFinality（complete/partial）、terminalCause（providerTextCompleted/interrupted/providerFailed/sessionClosed 等）、finalityEvidence；另行携带 playbackOutcome（unknown/started/drained/interrupted/failed），不能用 textFinality=complete 冒充已经朗读完。

canonicalTurnId 由当前作用域、provider epoch、角色及 provider question/reply 的稳定身份形成，并由 durable 队列保存；重试/恢复复用原 ID，不因 App 重启或 UI 重建重新生成一条。日志仅合法脱敏关联。

- Owner：ASRInfo、ASRResponse、QueryConfirmed 共用同一个 question accumulator，不三次提交相同用户轮。仅在当前SDK路线经验证的权威 final 阶段输出一次 owner complete。若 final ASR 与迟到 confirmed 不同，先按有合同依据的权威/累计规则归一化，不能先输出截短文本再静默丢掉更完整的内容。无法确认 finality 时保留未定稿状态和原始事件归属，不能超时猜成 final；需要持久化未定稿材料时通过记忆侧受保护接缝保存，不能入正式记忆或当作新的用户表达补写。
- Assistant：按 reply 聚合 ChatResponse 与有身份的 TTS 文本，排除重复/累计重复。生成终态可靠时输出一次完整assistant文本；真实音频 drain 可稍后到来，只更新独立播放状态而不再次提交同一文本。中断、SDK错误、用户关场且文本尚未完整时，将已收到片段留为 staging partial 并标明原因，不能拼出未收到尾句。若文本已权威 complete 而声音后来中断，文本保持 complete，仅更新 playbackOutcome，不降级、不重复产生 assistant。后续仅允许有原身份的状态/版本核实，不重复产生新的assistant turn。
- Role：assistant 始终为 context-only；owner final 是用户表达证据。不得改变B7 SourcePolicy、用AI文本替代用户事实或自动处理已有候选。

现有后端 content-v1 不承载 partial/finality。与记忆文档 9.2 对齐：未定稿/partial 先写 V2 的独立 `unsealedMaterials`；每个输入/回复首次合法登记时固定 captureOrdinal 和稳定槽位；只有连续 complete 前缀 seal 时才分配 delivery sequence/messageID/command，进入既有 append。后完成的回答不得越过前面 pending 用户轮先发送，缺口后的 complete 后缀也要保留。partial 不作为完整轮上传，也不拼接提示词假装用户原话；关闭后缺 final 要保留 coverage gap，只能经用户明确选择整理首个 gap 前的完整前缀，不能跳洞拼接后面的 complete 文本。ACK 后仍保留 canonical 去重元数据；相同 ID 异内容为冲突，不能生成新轮。后续音频 drained 只更新独立本地播放状态，不能改已发送 immutable 文本或再产生一条消息。

EchoViewController native 回调的职责顺序固定为：验证冻结的账号/session epoch → native event coordinator按question/reply聚合及归一化 → 将canonical事件一次交给durable接缝（complete入队，未定稿入材料区） → 独立更新UI。UI是否允许显示、当前处于speaking/listening、用户是否滚动/换页，都不能决定是否采集；持久化失败必须反馈该canonical项未保存并保留原ID，不能靠重复生成一轮弥补。

用户close与记忆侧约定：先持久化closeRequested（允许watermark待定）并冻结新槽位登记；freezing 中用 finalizeRegisteredTurn 专门接收 stop 前已登记槽位的权威最终化，不能再经过只接受 open 的新输入入口。native normalizer只排空停止前已经登记、具原始epoch与source ordinal的回调/权威final；再通过durable disk barrier封存N。停止音频不应丢弃已登记但排队未应用的文本，也不允许close后的未知新事件另建轮次。等待有界，缺final/归属时保留明确partial/未定稿缺口，不能超时宣称全场完整保存；这一排空过程不重开麦克风、不发送模型新请求、不补发历史未知写。

必须同时关闭 native 路径旧的双入口：onASRResult 中直接 captureLiveOwnerTurn 和 onTTSStarted 中直接 captureLiveAssistantTurn 只能由新的canonical入口替代一次；delegated/文字路径保留各自既有行为。不要一边新增typed事件采集，一边保留native generic回调capture，造成两份队列数据。记忆修改负责 durable/delivery 实现，音频修改负责接缝与归一化事件；在同一个 EchoViewController 文件按接口协调顺序编辑，禁止两个实现互相覆盖。

### 3.6 60 秒无用户活动计时只能在真正等待用户时启动

EchoViewController.swift:11892–11939 目前计时到期仅校验会话/账号/token，不判断仍在说话；13760 在原生任意 finished 后重启 60 秒计时。因此合成完成或句间结束可能启动错误倒计时。

允许计时：完整当前 reply 已可靠 drained，或已可靠 interrupted/failed 并进入等待用户状态，且没有 pending reply、正在合成/播放/恢复的音频。新用户语音、当前 reply 开始、音频活动均使计时取消/失效。定时闭包必须携带 native state version 并再次验证 waitingForUser 与当前 session/reply 集合，不能只对 lifecycle token。

长回答即使生成/朗读超过 60 秒也不能因用户安静而关场。drain 无法观察时保持明确未知并诊断，不能用“60 秒过去”代替 completed。会话显式用户停止仍立即生效。设计不能靠无限计时掩盖失联，断网/SDK失败应走明确失败/恢复状态，不伪装用户静音。

## 4. 当前阶段的手机音频范围

优先保证手机 App 的完整交互：长回答连续播放、文字持续显示、真人开口打断、后续问答正常、主动关麦及时生效，并与同场记忆保存正确交接。手机内置麦克风与扬声器是本阶段真机验收基线。

历史故障发生时连接了普通车载蓝牙，没有使用 CarPlay。该事实继续保留，但不据此新增车机平台、车型适配、车载 UI 或专项兼容工程。普通蓝牙耳机、音箱等均按系统外设路径处理，沿用当前音频路由；此处不作所有设备行为完全一致的技术承诺。

真实代码审计已经发现迟到 ASR、旧回复身份、句段完成和 UI 状态方面的缺口，先修这些 App/SDK 交互问题。不能等待原车机复现才推进，也不能将缺少原车机测试作为本阶段发布阻断项。若手机验收完成后有普通蓝牙设备可用，可做同一脚本的补充检查；本阶段不要求提供原车或覆盖外设型号矩阵。

保留必要的通用手机音频守卫：系统 interruption、页面退出、账号变化不得误标整答已播完；音频恢复仍核验原 Live 意图、lease/generation 和实际 SDK 状态。正常句段不应反复释放/重建 AudioSession，文字刷新不发送停止指令。只在手机验证发现具体问题时增加局部恢复处理，不新增泛化音频恢复框架。

当前 SDK 固定为 SpeechEngineToB 0.0.14.6.1-bugfix，保持内置 recorder/player 与既有音频配置。本次不为猜测中的车载问题切换 HFP/A2DP、调车机 AEC 参数、强制改变用户系统路由、插入第二播放器或升级 SDK。一般性音频诊断保留事件身份、打断来源、播放状态及必要 route/interruption 摘要，不做车机专项采集。

历史蓝牙/AEC 审计可用于以后出现具体外设问题时辅助定位；它不能把推测变为历史根因，也不能扩大本次工作范围。

## 5. 具体修改落点

工程根：/Users/gaominge/Documents/Codex/Video/DreamJourney_dev

| 文件 | 最小修改 |
|---|---|
| DreamJourney/Sources/Services/DialogEngineManager.swift | native 事件身份与聚合、三个 ASR 入口中断策略、observeProviderReplyEvent 校验顺序、typed delegate、player/synthesis terminal 分离；保留 delegated 路径 |
| DreamJourney/Sources/Modules/Echo/EchoViewController.swift | native typed 回调应用与过期拒绝、整答文字更新、等待用户计时；避免 generic onTTSStarted/onTTSFinished 重复处理 native |
| DreamJourney/Sources/Modules/Echo/EchoViewModel.swift | native reply 文本更新与完成接口；保留普通/文字回响 reducer 合同 |
| DreamJourneyTests/AudioOwnerLeaseModelTests.swift 或现有 native Live 测试文件 | 原函数/真实 manager→viewModel 装配测试，受控 SDK 事件源与指令spy |
| DreamJourney/Sources/App/AudioOwnerLeaseCoordinator.swift（按证据必要时） | 添加实际 route/activation 诊断或已证实的局部中断恢复守卫；正常句段保持同 lease 零额外配置 |
| 必要时新增窄范围 native 测试 fixture | 合成文本、有真实协议形状的事件；版本与出处可追溯，不用私人历史录音 |

禁止整文件替换、与记忆策略修复交叉覆盖、重构全部音频 owner/coordinator、任意新增全局开关、替换 provider/model/音色、生产历史重放。

## 6. 红绿与交付门禁

本轮只有上述11项机制观察。以下修复验收目前均 NOT_RUN；Sol 需先对当前代码执行同业务断言的业务红，再实现并跑绿，不能把编译失败当红，不能改 fixture 迎合实现。

| ID | 确定性反例 / 正向对照 | 绿断言 | 层级 |
|---|---|---|---|
| A01 | 同 Q1 完成 ASR 后 R1开播，迟到Q1 ASRInfo/Response/Confirmed分别到达 | 不发送新ClientInterrupt、不另建用户轮、不关闭R1 | 真实manager |
| A02 | 相同文本的新Q2、ID-only合法Q2、重复Q2事件 | 原生正常打断/续听；重复事件不重复操作；不做文本近似过滤 | manager+SDK |
| A03 | 无用户输入长答；字幕更新/滚动/换页 | 原生音频持续到真实drain，UI事件0中断指令 | manager+UIKit+SDK |
| A04 | Q2活动中晚到Q1 reply/start/end | currentQuestion保持Q2，旧reply不能成为matched当前或改新答状态 | 真实helper+装配 |
| A05 | 同R1多个SentenceStart/End与PlayerStart/Finish | 同答文本可更新，句级finish不完成整答 | manager+viewModel |
| A06 | TTSEnded早于最后PlayerFinish；PlayerFinish早于TTSEnded | 仅在双方可靠且归属完整后一次replyDidDrain | manager+SDK |
| A07 | 句1finish闭包待处理、句2已start；旧reply回调晚到 | 过期epoch不覆盖当前；无未归属终态强绑 | manager+UIKit |
| A08 | 同答第二文本段到达时viewModel仍speaking | 更新同答文本并持久化一次聚合结果；不驱动audio stop | 真viewModel |
| A09 | reply生成/播放超过60秒、句间静默、合成早完 | 用户安静不关场；可靠drain后才开始用户等待计时 | 可控时钟+manager |
| A10 | waiting timer已经排队，然后新reply/用户输入到达 | 旧timer因state version失效，不stop新reply | 可控时钟 |
| A11 | 原生provider失败、断网、缺reply ID、无player drain | 明确unknown/failed，无假完成、无App PCM降级重播 | 真实manager+SDK |
| A12 | 明确用户结束、合法后台/页面退出、账号切换 | 必要音频控制仍及时，旧回调不能重开/写入新作用域 | 装配 |
| A13 | 手机内置麦克风/扬声器，合成长答时无人说话或操作，随后短答；另测真人开口 | 长答完整可听、无非预期中断；真人能打断并续聊；短答正常 | 手机真机+真实SDK，必需 |
| A14 | 手机系统 interruption、页面退出/返回与旧回调；受控注入对应原因 | 状态及恢复符合合同，旧finish不误触，无假播完或自动整答重播 | 手机装配/可复现真机流程，必需 |
| A15 | 单次文字播报、delegated音频、原生短答、普通Echo | 既有路径无行为回归；不启动旧串行链路 | 对应既有测试 |
| A16 | 同Q多种ASR final事件、同R累计/重复句段、UI拒绝或重建 | canonical owner/assistant各按合同单次；durable原ID可重用，UI状态不丢采集；assistant仍context | 音频归一化+真durable接缝 |
| A17 | 文本已complete后音频中断；另测文本未完成即SDK错误/关场 | 前者只更新playbackOutcome，不降文本finality；后者staging partial；不补造正文或重复assistant | 同接缝 |
| A18 | pending owner后assistant complete；freezing时合法final及下一场事件交错 | 按captureOrdinal有序seal，finalizeRegisteredTurn可排空旧合法槽位；后缀不越gap、下一场不能进入 | 音频归一化+durable |
| A19 | ACK清理后重复canonical/同ID异内容；close marker后seal前kill | 去重元数据仍有效；异内容冲突；重启不伪造drain或完整记录 | 跨进程接缝 |

必须同时保存事件身份/状态、指令spy、UI与可听音频证据。手机视频或截图不能单独证明音频完整；SDK ACK与event总数不能代替实际可听完成。人工在手机上用新合成样本核对首音、整答可听覆盖、无讲话无操作期间的中断、真人开口打断、后续短答。实施时沿用届时已有设备测试授权；缺少手机测试条件应报告具体缺口，不要求用户回到原车配合。普通蓝牙补充检查若未执行，单列 NOT_RUN，不纳入本阶段必需项。

## 7. 最小诊断与停止条件

生产保留有上限、账号作用域、脱敏的native事件环形记录：单调时间、session/question/reply关联、实际segment metadata、callback ordinal、原始event code、isAISpeaking前后、状态版本、ClientInterrupt原因/返回码、player/synthesis状态、timer arm/cancel/fire原因、route/interruption/lease摘要。只记录状态/计数/合法脱敏关联，不落文本、音频、token、provider完整payload。现有PrivacySafeDiagnostics.print不能保证事后持久取证，持久诊断需限定容量/保留期/受保护存储，不能变成另一份聊天记录。

若协议无法提供整答drain或provider自动barge-in必要保证，先完成可独立证实的同 question 重复/旧 question 迟到/失效 generation 防护、回复身份校验、文字更新与计时状态门禁；全面撤去原生自动 ClientInterrupt 仍须先通过固定 SDK 静默长答和真人打断正反门禁，并将SDK合同问题标为阻塞该条验收，不能用定时器猜测通过。若需要SDK升级、扩大音频架构或改变用户结束语义，提交具体合同缺口和最小备选方案后再扩大范围。

交付分列：已确认代码机制、仍未知的历史触发、每项修复的真实红绿、SDK/手机真机状态、发布条件与局部回退。音频修复预计只需 iOS 发版；本设计不要求后端部署，也不替代记忆问题的后端依赖。达到本阶段全部手机与 SDK 门槛后，可报告“手机 Live 修复验收通过”；历史现场的精确触发仍按证据标明未知，不妨碍本阶段功能交付。原车机不列必需项，也不宣称所有蓝牙外设兼容性已验证。

### 7.1 可持久取证的最小实现

使用原账号作用域的受保护文件环形记录，建议每场最多 4096 条、最多 3 场且合计上限 2 MiB，最长保留 48 小时；达到上限覆盖最旧事件并累加 droppedCount，不阻塞音频。数值是本次设计默认，需以实际事件速率/性能检查调整并记载。记录必须在故障和关闭时保留摘要，不能依赖 print 或仅 viewWillDisappear 快照。

字段白名单：单调时间/事件序号、SDK/App/system 来源、会话/question/reply 的场内脱敏关联、callbackOrdinal、实际 SDK event code、接受/丢弃原因、状态版本、ClientInterrupt/stop 的触发枚举和返回码；系统 category/mode/options、actual input/output portType、sampleRate/channels/buffer/latency、route reason、interruption began/ended/shouldResume；lease请求及实际调用结果；SDK player/synthesis 计数与未知归属。

不记录正文/完整payload/PCM/token/车机名/端口UID。关联使用既有隐私散列规范并限定场内；跨设备人工对照使用测试场次 ID，不拿业务 UUID 做日志标签。AVAudioSession 没有通用公开 isActive getter，记录 lastActivationRequested/result，不虚构 actualIsActive。若要启用原生 player audio callback，仅在此 SDK 明确支持内置播放器同时运行且性能验证通过时统计帧/字节并立即丢弃，不把它当可听声证明或第二播放器。

实时回调只摘取固定标量并排队；磁盘编码/轮转在独立串行队列，异常时诊断可降采样但不得扰动播放。本地语音文本的 unsealedMaterials 属于受保护业务队列，不能混进诊断日志。敏感性、容量和读取入口沿现有 App 数据保护规范实现。

## 8. 给 Sol 的具体执行顺序和放行规则

1. 对齐源码指纹与既有 dirty diff。先把本次 11 项真实源码机制 probe 转成真实 manager/controller/viewModel 装配反例；保留 SDK 指令 spy、可控时钟、原事件身份和正反对照，执行同输入红测。
2. 补最小持久诊断，先固定 SDK 的真实 ASR/句段/player/终态事件合同。并行可实现已确定的旧 question 回退保护、迟到同 question 事件防护、同 reply 文字更新、timer state fencing；不能等待蓝牙归因才修已确认逻辑缺口。
3. 按证据选择原生 provider 打断分支：能证明 SDK 自行可靠处理真实用户打断时，撤去三处盲目 ClientInterrupt；否则使用 3.2 严格新输入事件守卫。没有合同或真实 SDK 正反验收时，两者都不能直接标生产默认通过。
4. 实现 synthesis/player 分离的唯一 reply coordinator、canonical complete/partial 接缝，和记忆侧同步撤下旧 native 双采集入口。确认 finish 幂等、旧 async callback 不污染新 reply、文字刷新不控制声音。
5. 执行 A01–A19 与回归。手机使用内置麦克风和扬声器，按新合成的“短→至少三句长答→短”连续 3 轮；全程静默无操作作为反例，另独立执行真实说“停一下”的正向打断。至少一条长答超过 60 秒，另有跨策略到期的长场组合。视频/事件/人工可听核对首句、尾句和中间覆盖，截图不等于声音证据。
6. 验证通用手机系统 interruption 与退出/返回边界。普通蓝牙耳机或音箱的相同脚本可以作为后续补充，不要求原车机、专项断连矩阵或 CarPlay 验收；未做外设检查不阻断本阶段手机交付。

完成报告必须列出：修复前业务红、修复后本地绿、SDK正反门禁、手机内置音频交互、跨TTL与记忆接缝，各自 PASS/FAIL/NOT_RUN 及构建指纹/证据路径。保留“历史具体触发 UNKNOWN”与“已确认代码缺陷已修复”的不同结论。历史日志缺失不要求 Sol 无限追查过去；以固定版本的可复现反例和手机验证完成本阶段功能交付。普通蓝牙若有补充结果单列，原车机专项标为本阶段不要求。

回退局限于本次 native 状态/事件适配块，保留诊断及同场数据；不能为回退恢复已确认的旧 question 误归属、用文本近似屏蔽用户发言或启动第二音频链。若 SDK 版本/架构确需变化，先列合同缺口、最小备选及新增验收再扩范围。音频补丁独立只需 iOS 更新；两项一起交付时依赖另一份设计的后端只读 API。
