# DreamJourney Live 开麦迟缓与发前失败：分析及 GPT‑6 Sol 开发指导

日期：2026-09-27。主问题编号：**DJ-LIVE-START-20260924**。本文件是本次局部开发与验收依据。

**当前交付是分析和设计，未实施产品修复、未运行修复测试。** 本轮没有访问生产、调用真实 Provider、连接手机、部署、处理历史任务或 commit/push。历史事故的完整首因仍未闭合，不能将本文件当成“已修复”的证明。

## 1. 结论：哪些已定位，哪些还不能下结论

开麦过程存在一个明确的客户端逻辑缺口：**新一次开麦使用页面过去捕获的权限决定；旧决定到期后，即使当前策略已经允许，ticket 请求仍可能在创建网络请求前被拒绝。** 过期授权拒绝本身是正确保护，缺口在新启动请求没有准备新的请求授权。此路径可解释现场的部分线索，但现场缺少单次请求原始错误和贯穿 trace，尚不能证明它就是 23:04:55 那次失败的唯一原因。

第一次“接近一分钟才进入聆听”是另一项待定位症状。现在只有相邻请求的完成时间，不能量出等待发生在哪一段。客户端没有覆盖策略、认证、ticket 与 SDK 启动的统一截止；后端也存在同步数据库等待影响请求响应的风险。这些是已发现的代码边界，**不是历史慢启动已经证实的首因**。

本次采用三条独立工作线：

| 子项 | 当前证据等级 | 本轮 Sol 应完成 | 本地完成后仍需保留的边界 |
|---|---|---|---|
| A：旧授权阻断新开麦 | 当前代码路径已定位；尚未运行同断言红绿；与现场相符 | 真实 Controller/Client/Gate 组合复现，局部修复请求授权准备 | 不能据此宣称历史失败已唯一归因 |
| B：启动等待无统一收尾、慢成功瓶颈未知 | 启动缺乏总截止；历史耗时未分段 | 有界启动、取消和迟到回调保护；阶段计时；受控延迟验证 | 近一分钟的实际等待点仍是历史未决，需未来同 trace 现场数据 |
| C：失败原因与请求曝光诊断缺失 | 当前代码已确认 | 本次启动专属分类、阶段记录和安全持久诊断 | 不以全局最新策略快照代替某次请求证据 |

### 1.1 已有现场事实

权威材料：[9/24 开麦事件原始报告](../../outputs/2026-09-24-dreamjourney-live-mic-start-incident/run-01/incident-report.md)，目录内含三张原始截图。

- 22:57 “正在准备麦克风”；22:58 已显示聆听。用户估计接近一分钟，截图按分钟显示，不能据此算精确耗时。
- 22:57:49 ticket 完成 HTTP 200；22:57:50 出现 SDK callback。此前 22:56:58 的认证完成不等于 ticket 请求开始，二者相减不能作为 ticket 耗时。
- 23:04:55 另一次失败：`backendVoiceRuntimeRequestFailed`；全局快照中的 `echoTextInput` 决定为 `capturedPolicyExpired`，expiry 为 23:04:36。该快照没有绑定本次 ticket。
- 失败窗口没有观察到新的 ticket/auth/policy/runtime 访问日志。**访问日志缺席不能证明没有网络请求**：也可能未到应用、处理中未完成，或没有覆盖到对应日志。
- 整体 readiness 曾通过，不能证明启动路径每个阶段正常。

### 1.2 本轮代码基线与外部边界

- iOS：[DreamJourney_dev](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev>)，读取时 HEAD `11d0d0051b9be3cce57822dd059472d1e2536866`，分支 `feature/prd-stitch-ui-adaptation`；存在用户已有未提交修改。HEAD **不足以标识实际源码**，Sol 开始前须记录完整受影响文件指纹和 dirty diff，不得 reset 或覆盖这些修改。
- 后端：[DreamJourneyBackend](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend>)，读取时 clean HEAD `8141ff271228b78c35237f9947f4bf026332af43`。与历史发布报告相符；本轮没有再次验证线上当前运行版本。
- 代码链接行号对应本轮读取快照，实施前按符号重新定位。
- 后端 ticket 阶段读取正式记忆 projection、生成并持久化票据；不运行 DeepSeek 候选整理。新的火山连接在后续 WebSocket 消费票据后才建立。**不能将本次发前失败解释为长对话 token 容量已满，也不能据没有 ticket 日志全面排除一切外部连接。**
- 本轮查阅到 Apple 官方说明：默认 request timeout 是等待新数据的间隔，可随数据到达重置，并非整个开麦链的总墙钟截止。[Apple URLSessionConfiguration](https://developer.apple.com/documentation/foundation/urlsessionconfiguration/timeoutintervalforrequest?language=objc)。不能因为观察接近一分钟，就断言命中了它。
- 火山相关官方文档链接在本轮访问中未取得可核对全文；未据第三方转载增加任何供应商容量、并发或时延结论。后端当前 `open_timeout=10` 是项目配置，**不是本轮验证过的供应商 SLA**。

## 2. 实际链路与精确修改入口

当前顺序为：

`点击麦克风 → 账号/生命周期 → 系统麦克风权限 → 客户端 runtime/策略/认证 → ticket POST → 后端鉴权/策略 → 正式记忆快照 → ticket 落库 → iOS 配置/启动 SDK → WebSocket 消费票据及火山建连 → SDK SessionStarted → 进入聆听`。

各守卫可能早退；不能把它当作每次都会执行全部网络步骤的固定流水线。

| 入口 | 本轮核查结果 | 开发约束 |
|---|---|---|
| [EchoViewController.startVoiceCapture](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:14598>) | 麦克风授权后进入 preparing，随后配置 runtime | 保留真实权限、账号与音频所有权检查 |
| [configureVoiceRuntimeThenStart](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:15582>) | 调 ticket；成功后配置 SDK、绑定 productSession、启动采集；失败统一 fallback | 启动作用域与一次终态应在这里贯穿，不能动上一场保存坐标 |
| [fetchRealtimeVoiceConfig](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:8155>) | 未接 fresh 请求决定、专属 trace、启动总截止或取消句柄 | 增加 Live 专属可选上下文；旧调用契约不受全局默认变化影响 |
| [FeatureGateService.requestDecision](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:537>) | 同账号复用 `routeDecisions` 的旧捕获 | 新启动使用当前请求授权；不改变所有业务写的授权语义 |
| [freshServerPolicyManagedRequestDecision](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:621>) | 已有从当前缓存捕获新决定的工具 | 优先复用机制；刷新缓存与重新捕获决定是两步 |
| [ReleasePolicyEvaluator.revalidateForRequest](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/ReleasePolicyStore.swift:378>) | 旧 capture 到期先拒绝，之后才检查 current policy | 不删除到期、账号、版本或真实 deny 保护 |
| [requestJSON](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:16465>) | feature 拒绝发生在创建 transport 前；另有 runtime 恢复和 401 恢复 | Live 上下文必须跨恢复传递，不能套用候选列表 GET 的预算/错误类型 |
| [Echo 失败诊断分类](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:48>) | 本地 policy reason 和网络底层分类未完整保留 | 以本次错误分类，禁止用全局 latest decision 反推 |
| [DialogEngineManager SDK 初始化](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:3656>) | 未 ready 时有 0.5 秒补查；长期未 ready 分支需要终态 | 只补启动完成/失败边界，保留播放完成与续听机制 |
| [ticket 后端入口](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:17810>) | 同步 handler：正式快照后生成 ticket | 这是会写票据的 POST，不能称为可任意重试的只读请求 |
| [ticket PG 持久化](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/postgres_store.py:1948>) | 获取锁、撤销未连接旧票据、检查 active session、写票据 | 不扩并发数，不移除授权/快照绑定，不新增无幂等证据的 POST 重试 |
| [火山代理连接](</Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/realtime_voice_proxy.py:471>) | 在票据消费之后连接 upstream | SDK/WS 的成功不能用 ticket 200 或虚构 SessionStarted 替代 |

[本轮源码审查指纹](../../outputs/2026-09-27-dreamjourney-live-mic-start-analysis/source-review-fingerprints.md)仅供比对，不是构建证明。

详细核查备忘：[iOS](../../outputs/2026-09-27-dreamjourney-live-mic-start-analysis/ios-audit.md) · [后端](../../outputs/2026-09-27-dreamjourney-live-mic-start-analysis/backend-audit.md)。

### 2.1 为什么此前测试通过仍漏掉它

当前检索发现的大量 Live policy/auth 测试覆盖的是记忆 start/append/ACK/status，并不等于开麦 ticket。已有 ticket 合同、有效期、映射及快照失败测试，也没有覆盖“旧页面决定过期→当前策略允许→真实开麦入口”组合。源码字符串检查不能替代这条运行链。

因此新测试必须实际调用 `fetchRealtimeVoiceConfig`，经过真实 `Controller → BackendClient → FeatureGate → requestJSON → 受控 transport`，而不是直接给 Controller 注入成功结果或调用成品配置。

Sol 须枚举 `configureVoiceRuntimeThenStart` 和 `fetchRealtimeVoiceConfig` 的全部调用者，说明哪些入口纳入启动上下文。不能因可选上下文为 nil 而让真实 Live 入口绕过截止，也不能误改其他业务入口的语义。

## 3. 必须保留的行为与范围

1. 保留正文逐轮采集/落盘、不可变投递、账号/authority 绑定、停止尾段水位、end/ACK/admit、同场状态读取、候选可见、审核正式落库、重建读取、B7 和跨批去重/补充/纠正/撤回。
2. 保留未知业务写不自动重放、原预算、旧回调隔离、诊断磁盘故障不破坏保存、先短后长门禁。启动预算与保存/整理/GET 预算分离。
3. 保留真实策略 deny、注销、账号切换、旧权限代次、正式记忆快照及 proxy-only 凭据保护。ticket 当前映射为 `echoTextInput` 是已有双方契约；不得借本修复改成恒允许、另造权限映射或绕过服务端校验。
4. 不改 DeepSeek prompt、分批大小、提取重试预算、Live 时长/票据 TTL/并发额度以碰运气。没有证据支持这些是本次根因。
5. 本轮可改 Live 启动准备、作用域/取消/截止、分类与诊断以及对应测试。后端仅增加本次阶段取证与隔离验证；全局 UoW/连接池/metrics 的架构性修复归已有独立登记项，不混入此局部变更。

## 4. 实施设计

### D1：每次显式开麦建立独立启动上下文

可命名 `LiveLaunchAttempt`，但复用等价的现有结构亦可，不要求为改名重构。它至少持有：随机 attempt UUID、单调时钟起点、账号/应用 lease、现有生命周期 token、阶段、请求序号、恢复名额、取消句柄、是否终止。

- 点击时创建上下文并记阶段；重复点击沿用/拒绝当前在途意图，不并行发多个 ticket。
- 系统首次权限弹窗等待单独记录。权限允许后，且当前上下文仍有效，才开始“实际启动”的统一计时；迟到授权不能恢复已经取消/离页的意图。
- runtime、policy、认证 single-flight 等待、ticket、SDK 初始化/StartEngine/SessionStarted、音频激活都消耗同一剩余时间。刷新、401、SDK 补查不能重置时钟。
- **新启动等待上限默认 15 秒**，使用一个具名、可注入测试的参数。它是本设计选择的用户等待边界，既不是供应商限制，也不是旧事故首因证明。健康快路径目标仍是尽快进入聆听；不得为凑足 15 秒增加等待。若现有明确产品配置与该值冲突，在交付记录依据并维持单一总预算，不悄悄改多个独立 timeout。
- 到期一次性结束本次启动，UI 恢复可操作状态，提示“语音启动超时，请稍后重试”。不得显示“发布策略拦截”，也不得伪装已经开始采集。
- UIKit 主线程卡住时普通主队列 timer 不能保证墙钟截止；设计不要声称消除了系统调度停顿。回调提交前仍用单调时钟复核截止，防止队列恢复后启动已过期的会话。
- 每项启动副作用前同样复核截止，而非只检查异步回调。同步 SDK 初始化或音频激活返回时若已到期，不得继续 StartEngine、创建后续资源或进入 listening；用确定性测试覆盖这条路径。

### D2：从当前权限准备本次请求，不放松权限

按以下顺序实现，避免每次开麦增加几次无意义网络请求：

1. 验证当前账号、lease、生命周期、配置和启动上下文。
2. 从当前策略缓存**新捕获**本次请求决定；当前缓存有效允许时直接前进，不刷新，不复用旧页面 capture。
3. 缓存缺失/过期、且属于现有规则允许恢复的情况时，至多一次策略刷新；刷新后重验账号和上下文，再新捕获。旧页面决定过期但当前缓存已允许时，不应浪费这次刷新。
4. 当前有效策略明确 deny、本地开关关闭、账号/代次不符、权限范围不符，立即终止，保留实际原因，ticket 与 SDK 启动均为零。
5. 既有 runtime 配置恢复和认证恢复仍独立受限，但必须挂到同一个启动上下文。策略恢复最多一次，runtime 恢复不超过既有一次，认证恢复不超过既有一次；不能递归重建预算。
6. Live ticket 的任何 401 后自动重发，**包括继承的既有 `requestJSON` 分支**，都必须先满足可识别、经服务端对照证明的写前拒绝合同。符合时仅允许既有一次认证恢复，再验证当前请求授权与账号；不符合时禁止自动重发，不能因为旧实现会重发就沿用。仅有 HTTP401、来源不明/中间层401、可能已发行票据的401均不足以证明安全，保留发行未知。用 endpoint/Live上下文限定改动，不全局更改其他业务的401策略。

正常成功 ticket POST 恰好一次；确证的写前 401 恢复场景最多两次。timeout、连接中断、响应丢失、未知 5xx 不得自动换 ticket 重试；缺少响应时保留“发行结果未知”。取消本地请求不代表服务器回滚。后续用户显式重新开麦仍由既有 broker 的票据失效/替代与并发规则约束，不能因本地取消就假定远端资源释放。

### D3：取消与迟到回调必须绑定同一次启动

- ticket transport 暴露作用域内可取消句柄；停止、离页、账号变化、supersede、截止使该上下文终止。
- 共用认证/策略刷新只移除本次 waiter 或使它失效，不能取消其他业务共享请求。SDK 停止必须核对 owner/generation，旧 attempt 不能停止新会话。
- ticket 晚到不得 configure SDK；SDK 晚到 SessionStarted 不得进入聆听；旧失败不得覆盖新成功 UI；旧音频 lease 清理不得释放新 owner。
- 终态仲裁必须串行且一次性完成。启动成功时，原子地把 SDK generation、音频所有权及会话绑定移交既有 Live 会话生命周期，并撤销启动 watchdog。attempt 成功结束不能屏蔽正常 ASR、播放、停止、保存回调，也不能让已排队的旧取消回调停掉已成功移交的会话。后续对话不受这15秒预算影响。
- SDK 初始化长期未 ready、StartEngine 拒绝、永不返回 SessionStarted 都有明确阶段失败；不得仅用现有 0.5 秒补查后直接 return，留下永久 preparing。
- 只处理本次启动拥有的资源。不得调用全局清理删除前一场 outbox、follow-up、checkpoint 或停止保存链。若本次已经建立采集对象后失败，沿用其正规停止/保存合同并验证，不能裸清空。
- 特别检查 `handleBlockedRealtimeVoice` 中的 `finishLiveMemoryCaptureIfNeeded()`：不能在尚未建立本次 capture 时无归属地结束旧场；本次已有 capture 时只结束本场。四场成功回归不能代替“新启动失败、旧场仍在保存”的反例保护。

### D4：记录能还原首个断点的诊断

诊断必须随 attempt 创建，未发请求也可记录；请求 trace 不得等 HTTP 回来才生成。每个 HTTP 子请求有独立序号，以便区分 policy/runtime/auth/ticket 与重试。

**客户端阶段：** tap、permission start/end、lease 验证、策略捕获/刷新、runtime 检查/刷新、认证等待/刷新、ticket request created/task resumed/response received/completed、decode、SDK configure/ready/StartEngine/SessionStarted、音频激活、listening 或 terminal。

**后端阶段：** 最外层入站、鉴权、release policy、runtime capability、正式记忆 snapshot、票据取锁/写入/commit、响应前 metrics、响应发送。仅覆盖这次启动相关路由；不把一般 access log 的完成时刻当入站时刻。

- 使用现有 trace 设施时先核查是否确实贯通；目前 Live 没有传 `diagnosticTraceID`，现有 helper 名称存在不等于有证据。
- 使用随机、限长、格式校验的诊断 ID，跨 ticket HTTP 传递并回显；服务端不接受任意用户字符串直接拼日志。关联不替代鉴权，不改变签名/租约。
- ticket 阶段可同 trace 对齐；SDK/WS 侧只使用现有协议支持的安全关联字段。若无法贯通 WS，不得把 trace 塞入未支持的 SDK 字段或记录票据做关联；记录此边界，用两端已验证的安全映射后再关联。允许诊断链缺环被明确标注，不允许时间邻近被宣称为同一请求。
- 时长用各自进程单调时钟。墙钟仅用于定位日志窗口；客户端与服务器时钟不相减来精确计算某个阶段。
- 曝光分清 `requestCreated`、`taskResumed`、`responseReceived`；客户端 task resumed 不能证明后端已收到，只有对应后端入站能证明。明确本地发前拒绝为未创建 ticket transport，其余未知保持未知。
- 白名单保存 error family、有限的 system domain/code、HTTP 可选状态、policy reason、服务端业务 code、首次失败阶段和最终阶段。`backendVoiceRuntimeRequestFailed` 可保留兼容字段，但不能是唯一诊断。禁止从全局 latestDecision 填本次 policy reason。
- 不保存正文、音频、token、cookie、完整 header/响应、带 ticket 的 URL、用户/家人/会话原始标识、context hash 或其他业务内容 hash；未知错误只保存允许的通用分类，不原样写 `localizedDescription`。
- 复用现有有界持久诊断容器，按 attempt 保留首错和终态；后续错误不能覆盖首错。若无可用容器，采用本地限额记录，不引入远程诊断业务服务。磁盘失败只影响诊断自身，不能递归导致采集/保存终止。
- 后端新增诊断不得同步写新的诊断数据库或阻塞响应；使用安全有界日志路径，失败不改变业务结果。必须记录日志丢弃/不完整状态，不能据缺日志判某阶段没发生。

UI 只展示用户可理解的原因：真实权限限制、登录失效、网络不可用、启动超时、临时语音不可用。业务和技术细节留在诊断中；不借此修改所有模块的超时分类。

### D5：慢启动与既有 API 停摆的隔离分析

关联但独立的问题：[DJ-API-READ-STALL-01 等记录](../服务端与认证/2026-09-24-DJ-API-READ-STALL-01-登录后读取停摆与超时误分类-待修复记录.md)。

后端审查确认 `/live` 虽绕过请求 UoW，外层 metrics 仍可能在响应前同步落诊断库；async middleware 也包含同步鉴权/取池/事务路径。因此“连 /live 都挂住，所以数据库等待不可能”是不成立的。代码风险已存在于旧版本，本轮没有现场栈证明它造成这两起事故。

Sol 需在离线隔离进程做有限故障注入，分别延迟 metrics sink、auth lookup、pool checkout，观测完整 ASGI lifespan 下 ticket、并发 `/live` 和事件循环心跳，并留进程栈、起止阶段与退出结果。**不得在测试主事件循环用无限阻塞，也不得让测试无限等；父进程 watchdog 负责收尾。** 诊断目的是判定机制能否导致所见症状及新增日志能否定位，而非强行要求本轮修掉所有历史架构债。

隔离必须在导入 app 之前完成：固定专用测试环境与合成 PG，禁止加载真实 `.env`，默认拒绝外连。核对 metrics/release-policy recorder、incident service、readiness 等依赖全部绑定测试实例；它们可能在模块初始化时捕获原 store 方法，仅在导入后替换 `main.store` 并不足以隔离。MIC-15/16 均适用此要求。

若注入能稳定重现共享阻塞，登记到相应已有 API 问题，给出局部修复建议与证据；除非它是本次新增回归，否则不将全局中间件重构混入本轮。若不能重现，如实记录不能重现及条件。两种结果都不证明历史首因已经还原，也不阻塞其他本地工作交付。

## 5. 本地验收矩阵：不得只检查 helper 或计数

本地模型与 SDK 边界使用受控 adapter/HTTP，禁止真实 Provider。能经过真实 Controller/BackendClient/FeatureGate/磁盘的地方必须经过；不能从 QA 分支直接注入最终业务状态。需要隔离 PG 的测试使用全新合成数据，正确创建与关闭 fixture/lifespan，不触及生产或用户历史。

MIC-01 必须由真实 Gate 捕获旧 route、推进可控时钟、更新真实策略源，再经真实 Controller 调用 Client。仅权限、时钟、SDK/音频及网络 transport 可控；不得用固定 `allowed=true` 的 QA feature provider 或直接返回 fresh decision 跳过授权逻辑。

| ID | 场景 | 必须观测的断言 |
|---|---|---|
| MIC-01 | 旧页面 capture 到期，当前缓存有效允许 | **同断言修前业务红/修后绿**。修前 ticket 无曝光、预期成功失败；修后 ticket 一次、无多余刷新、SDK 一次、正确 owner 进入 listening |
| MIC-02 | 当前缓存也过期，合法刷新后允许 | policy 请求最多一次；刷新后 fresh 决定；同 attempt、同总截止；ticket 一次 |
| MIC-03 | 当前有效 deny、本地关闭、刷新后 deny | ticket 与 SDK 均零；原因准确；无自动 refresh 循环；保留 evaluator 旧 capture 必拒绝原测试 |
| MIC-04 | 刷新中同账号正常返回 vs 切账号/注销 | 正常对照能启动；错账号/旧代次不能曝光 ticket 或提交 SDK；共用刷新其他 waiter 不被取消 |
| MIC-05 | 先取消/离页，后 ticket 或 SDK 回调；再启动新 attempt；SessionStarted与截止竞速 | 旧回调不启动、不改新 UI、不停新 SDK、不释放新 lease；终态一次；成功资源移交与截止只能一个获胜 |
| MIC-06 | 连点麦克风、重复 completion、多个失败竞速 | 单一在途意图，无并行 ticket；一次终态，UI 可操作；首错不被最后错误覆盖 |
| MIC-07 | 规范写前401正例；非规范/来源不明/可能写后401负例；恢复后deny/换账号 | 正例最多一次认证恢复、ticket最多两次，拒绝时快照和票据写入均未执行；负例不刷新后重发；恢复后deny/换账号零重发；fresh authority/lease与总预算保持 |
| MIC-08 | ticket 曝光后 timeout/响应丢失/连接中断/未知 5xx | 首次曝光后未知则POST总数保持1；确证写前401恢复后第二次曝光未知则保持2；两者均不得再自动换票或用迟到结果启动SDK；取消不等于远端回滚 |
| MIC-09 | policy/runtime/auth 等待、ticket、SDK 任一悬挂 | 总截止触发一次准确阶段终态，取消本次 waiter/transport；无无限 preparing；迟到结果无副作用 |
| MIC-10 | 各阶段单独未超时但累计超预算；同步SDK初始化跨过截止后返回ready | 总预算仍拒绝；返回后不继续StartEngine/listening；刷新/重试没有重置；墙钟调整不破坏单调判断 |
| MIC-11 | SDK 未 ready、StartEngine 失败、SessionStarted 缺失 | 各有终态与诊断，不依赖 0.5 秒补查直接 return；正常进入 listening 后启动 timer 失效 |
| MIC-12 | 健康热路径及原正式记忆绑定 | 无新增固定等待，无多余 policy/auth/runtime 请求；正确 productSession/context 绑定；本地确定性时序证明，真实启动秒数另验 |
| MIC-13 | 本地 deny/DNS错误/HTTP503 snapshot失败/429/解码失败 | 错误可区分，无 HTTP 的状态为 nil；不把网络错误显示为发布策略；保留已有 snapshot 业务分类 |
| MIC-14 | 全局 latestDecision 来自别的请求；诊断重启读回/磁盘不可写 | 本次原因不串场；首错与终态可复核；脱敏规则无泄漏；诊断失败不破坏保存链 |
| MIC-15 | 默认 API lifespan + 隔离 PG 的合成 ticket 链 | 鉴权/策略/快照/票据/commit 真实执行；绑定正确；写前401无票据；issued替代/active并发及消费原保护通过；外部 WS 受控 |
| MIC-16 | 完整 ASGI 链中有限延迟 metrics/auth/pool | 各阶段记录能定位；并发 liveness/心跳是否受阻被准确记录；旧机制风险与新诊断回归分列；所有进程有界退出 |
| MIC-17 | 已有音频与所有权保持，启动成功后立即正常停止 | 成功移交后ASR/播放/停止/保存回调仍正常；长回复、打断、续听、账号与租约保护无回退；不得用“构建成功”替代 |
| MIC-18 | 同版短长保存保持 | 执行下述四场；真实客户端读取候选，核对 Source/证据身份、审核正式入库及重建回查 |
| MIC-19 | 场A仍在保存/整理，场B发前deny/超时；B建立capture后SDK失败 | A的outbox/follow-up/checkpoint身份、水位与预算不变；B发前失败不对A发送end/ACK/admit；B已有capture只正规结束B，不删除或误完成A |

MIC-01 必须有隔离修前副本、**相同业务成功断言**的失败产物与修后通过产物，不把编译失败、改断言或只断言“旧版失败”当业务红。MIC-09/11 对现有永久等待路径补稳定受控反例；已有正确的安全保护只要求保持通过，不强造修前红。时序测试用可控时钟、阶段屏障与完成信号，不能靠 `sleep(0.1)` 或增大等待来掩盖竞态。

### 5.1 保存链保护：每次长场前独立短场

本次修改 Echo 与 BackendClient，属于保存链实际依赖。最终版必须重新执行：

**short-A → logical20 → short-B → logical65**。

- 两个独立短场均至少两轮，第二轮补充第一轮；从真实采集事件、落盘、同步、停止，到待确认候选可见，再审核为正式记忆、重建读取。短场失败立即停止其后长场，修复后重跑受影响的门禁及长场。
- logical20 保持现有 110 用户轮及对应助手轮负载；logical65 保持现有 150 用户轮及对应助手轮负载。逻辑时间压缩要明确，不替代物理20/65分钟。
- 逐轮核对 turn/Source/span/evidence 身份、停止尾段、全场事实集合；跨批重复只一条候选，补充/纠正/撤回符合现有语义；不能只看候选数量或 pendingReview。
- 真实默认 API/Worker 与隔离 PG 完成候选→审核→正式→重建；Provider HTTP 受控。保留目前端到端工具，不能换一套更短的假链以提高通过率。
- 回归覆盖 OwnerTruth、Echo/音频/账号以及改动触及的后端 ticket/策略/快照测试；最后构建 Simulator 与通用 iOS 无签名目标，检查两端 diff。仅文档变化可复用；实际依赖、配置、测试装配变化则按指纹重跑受影响结果。
- 原后端七项路由基线和其他已记录例外独立说明；任何新的失败不能凭名称归基线，需同环境旧/新对照。完成信号必须覆盖真实在途回调与持久化回调。

## 6. 后续外部与真机验收：只准备清单，不自动开始

**用户后续主动发起；本地工作不连接、不检测、不等待手机，不能以无手机停工。** 历史授权的某次费用/诊断或设备连接不扩展为本轮持续调用许可。

1. 冻结受验版本和配置。真实 Provider 验证确认具体接口/模型、账号额度、实际连接用时与错误，不用 HTTP101 或 ticket200 代替对话成功；DeepSeek 仍属于会后整理边界。
2. 优先一次短场：准备阶段同 trace 耗时、Live文字与语音实时性、两轮补充、结束后候选可见、审核正式与重建回查。记录是否命中缓存过期/策略恢复路径。短场失败不进入长场。
3. 用户安排物理20分钟测试，前面必须有该最终版本独立短场通过。后续物理65分钟前再做另一独立短场；两场均验证正文逐轮到达、停止尾段、待确认和正式记忆。
4. 静音 PCM 自动化可以覆盖已证明等价的数字传输链；麦克风/扬声器/回声消除仍另验，不能冒充已通过。核对真实 SDK 顺序，不能以本地受控 adapter 证明它。
5. 若再出现慢启动或失败，保留单次 attempt 首错/阶段时间、请求创建与曝光、同 trace 服务器阶段；若服务器整体失活，先取栈/池状态，再按独立运维授权恢复。禁止反复点麦、重放历史命令或循环重启碰运气。

15秒截止只能使错误有界收尾，**不能被计为“启动速度问题已解决”**。真实健康热启动与用户原约3秒体验的差异须据阶段实测说明；若实际启动稳定超过该范围，继续以证据定位，不更改计时口径来宣布通过。

## 7. Sol 执行顺序、交付与状态

先读本文件、原始事件、[登记册](../DreamJourney问题登记册.md#dj-live-start-20260924)与[维护规则](../问题登记与验收维护规则.md)。记录实际 checkout/dirty状态/依赖指纹，确认当前代码还存在对应路径；如已有修改，按现状核对，不照旧行号覆盖。

按 D1–D4 最小实现与 MIC 专项推进；D5 做本地隔离机制验证并按独立问题登记结论。先建立修前业务红，再修改，再跑相同断言。不要先修改生产、等手机复现或反复新增功能。

一次性交付以下材料，建议目录为 `outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-01/`：

- 实现报告：具体改动、未改边界、源码定位、剩余不确定性。
- 逐项 MIC 执行矩阵：场景、真实执行链、预期/实际、命令、产物、状态；SKIP 有替代证据，否则保留缺口。
- 修前/修后同断言产物与版本/dirty补丁指纹；异步完成屏障证据，禁止只给摘要计数。
- attempt 脱敏样例：发前 deny、ticket未知、超时、SDK未ready及正常路径，证明首错/阶段/曝光可以区分。
- 后端有限阻塞对照与归属；不将机制复现等同历史首因。
- 最终同版四场、受影响回归和两种无签名构建证据；指纹以及旧结果复用/失效依据。
- 更新主登记册及材料索引：A/B/C分别记录本地修复、历史首因与现场状态；保留历史原报告。
- 后续用户主动发起的真实 Provider/短场/20分钟/65分钟/声学验收清单。

本轮适用本地断言全部满足，可交付：

`LOCAL_PASS / REAL_PROVIDER_NOT_RUN / DEVICE_NOT_RUN / DEPLOY_NOT_RUN / HISTORICAL_REPROCESS_NOT_RUN`

同时明确：`HISTORICAL_SLOW_START_CAUSE_UNRESOLVED`，历史23:04失败的单次因果尚未由请求级证据证实；已有 `seq5` 首发原因继续未决。**本地已修不能把父事件直接改为现场关闭。** 既有 API 风险有据列为独立待修，不要求无关架构债全部清零才能交付本次适用本地结果。

如果 MIC-01 或其他适用断言仍不满足，写 `LOCAL_INCOMPLETE` 并列出精确失败和剩余工作；不要只说“无手机/未调用真实 Provider”。持续完成一切不依赖外部的本地工作后交付，不以规划或阶段性通过代替完成。
