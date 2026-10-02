# DreamJourney Live 长会话认证续期与同步恢复：问题分析及修复设计

> 2026-09-18 最新开发入口：[当前代码复核与开发执行方案](2026-09-18-Astra-Live长会话认证同步-当前代码复核与开发执行方案.md)。下文保留最初设计和 v1.2 快照；当前实现进度、剩余任务和验收顺序以新文件为准。

日期：2026-09-17\
修订日期：2026-09-18\
独立问题编号：**LIVE-L20-AUTH-01**\
版本：**v1.2，补充当前工作树的具体续修清单。**\
状态：**LOCAL_INCOMPLETE。R1/R2 已有初步修改和定向证据，但最新 R3 本地组合测试仍失败；历史 20 分钟真机场景为 DEVICE_FAIL，新版真机验收最后执行。**\
执行对象：Sol，【开发】任务。此次为客户端局部修复设计，不授权部署、操作生产业务数据、处理失败手机场次或 commit/push。

**当前执行入口：**先读 [2026-09-18 本地续修与验收指导](2026-09-18-Astra-Live长会话认证同步-本地续修与验收指导.md)。该补充根据真实 xcresult 和当前代码列出 C1–C7：恢复分支 inFlight 阻断、原场次绑定、重试曝光与预算、原命令冻结、认证合同及时钟、原测试保持性和完整本地交付。保留已完成增量，直接继续修复，不重新开始调查。本文件第 1–6 节的代码定位与初始现场事实对应分析时基线；当前进度与现场补充以续修指导为准。

## 0. 统一执行顺序

**本次任务只执行“本地修复 → 本地测试验证与回归 → 本地交付”，完成后正常结束。真机测试仅由用户之后另行主动发起，不属于本次任务的自动步骤、完成条件或阻塞依赖。现有报告、日志和源码已经足够开展本地修复；不得检测或等待用户手机，不得因手机未连接、未解锁或未配对中断本地任务、报告 BLOCKED 或推迟交付。**

| 阶段 | 必须完成的工作 | 进入下一阶段的条件 |
|---|---|---|
| A：本地修复 | 根据已有证据建立本地修前反例，按 R1–R4 修改代码，保留此前修复及安全边界。修前反例与开发中的定向测试均在本地完成。 | 适用的局部修复已实现，进入本地组合验证与回归。 |
| B：本地测试验证 | 执行第 7–8 节的真实组件、隔离磁盘、受控网络组合测试，修后绿测、受影响模块回归、编译检查，交付源码和构建指纹。 | 所有适用本地必测项通过，修复范围内无未解决的本地缺陷；列清最终真机清单与失败现场保护方案。 |
| C：用户另行主动发起的真机验收 | 不属于本次本地任务；仅在用户之后明确提出开始真机测试时，按第 9 节验证。 | A、B 通过并交付后本地任务已完成，不等待手机；实际设备证据通过后才关闭相应真机问题。 |

本地红绿测试可以穿插在阶段 A 的开发过程中，不改变“所有真机测试安排在本地修复和验证之后”的顺序。不得仅因真机仍为 NOT_RUN 就把已满足条件的本地结果判为不完整；同样不得用 LOCAL_PASS 代替 DEVICE_PASS。等待或补充当次生产拒绝日志也不是已确认代码缺口的开工条件，证据限制应如实记录。

## 1. 结论与目标

之前的短场 Live → 待确认记忆保存链已经真机通过，应保留这个结论。本次是长会话中途认证失效后的同步恢复问题，不能重新概括成“Live 完全不能持久化”，更不能回退之前的持久化授权修复。

本地采集与正文落盘持续到了 70 段；客户端持有的服务端确认只推进到 48 段。第 49 段遭遇 401 后，另一个 trace 的请求经历 401→403，之后还有 22 段未获服务端确认。关闭因队列未排空而停止，没有完成 end/ACK/admit。这里的“段”包含 owner 与 assistant，不是 70 次用户发言；关闭时 ownerTurnCount=35、persistedOwnerTurnCount=24。

本次找到三个相连、但应分别验证的缺口：

1. **发送 Live 写请求前，没有独立检查 access token 的有效期。** 现有 fresh authority 解决的是功能策略 TTL，不等于 access token 续期；Live typed write 又正确禁止了传输层自动重发，因此长期使用的 token 一旦返回 401，会停在失败状态。
2. **Live 只读核实请求在认证恢复后丢失功能授权。** `requestJSON` 的两条 401 恢复路径都传入 `featureDecision: nil`，但路由映射没有覆盖 `live-delivery-status` 和 `interview-sessions/current`。结果第二次请求不会带 captured policy。已运行生产映射函数摘录和真实后端策略模块的离线探针，复现该缺口；与现场 401→403 高度一致，但没有取得当次生产 403 的 reason，不能写成已取得服务端逐请求证明。
3. **已有会话在中途明确拒绝后，没有受严格约束的续传入口。** 修好 GET 后，若第 49 段没有对应成功回执，当前 `receiveDeliveryStatus` 仍会停在 unknown；FIFO 对 `serverRejected` 也只暂停。这个保护不能删除，应补齐“确认未生效的认证拒绝”的恢复能力，保持真正未知写只读核实。

本地修复的完成标准是：在真实组件与受控网络的本地组合中，证明同一个逻辑 Live 场次跨 access token 续期后队列继续推进，停止后按实际尾水位完整排空，完成 end → ACK → admit → pendingReview，且全部适用回归通过。最终真机验收再验证相同行为与真实候选来源包含开场、中段和结束标记。文案变化、超时退出、GET 不再 403、授权刷新成功，都不能单独作为完成标准。

## 2. 输入材料与证据范围

- [20 分钟真机复测报告](../../outputs/2026-09-17-dreamjourney-live-20min-device-retest/run-2026-09-17-01/reports/2026-09-17-DreamJourney-Live-20分钟真机复测报告.md)
- [本轮关键失败日志](/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-20min-device-retest/run-2026-09-17-01/evidence/failure-window.log)
- [本轮完整脱敏日志](/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-20min-device-retest/run-2026-09-17-01/evidence/live-sanitized.log)
- [用户观察记录](../../outputs/2026-09-17-dreamjourney-live-20min-device-retest/run-2026-09-17-01/evidence/user-observation.md)
- [已经通过的短场真机保存链](../../outputs/2026-09-17-dreamjourney-live-persistence-authority-fix/device-retest-2026-09-17/2026-09-17-DreamJourney-Live持久化授权真机复测报告.md)
- [已有 P01–P13 本地边界清单](../../outputs/2026-09-17-dreamjourney-live-persistence-authority-fix/run-2026-09-17-01/reports/P01-P13-执行清单.md)
- iOS 工作区：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- 后端工作区：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`
- [本次分析的源码指纹、探针与证据目录](/Users/gaominge/Documents/liftora/outputs/2026-09-17-live-20min-auth-sync-analysis/run-2026-09-17-01/evidence)

本次未修改 App 或后端业务代码，未操作手机、候选或历史数据。生产 SSH 只读查询因凭据未被接受而未成功；没有取得生产部署版本和当次服务端拒绝 reason。本地后端代码能证明一条可复现的拒绝机制，不能替代生产实例证据。

两轮真机报告使用相同已安装可执行文件 SHA-256：`9d29fa691ad6133ba6373f1b64baae6c4cb5af04846b630b2f383b13ff26f0c5`。当前 iOS HEAD 为 `11d0d0051b9be3cce57822dd059472d1e2536866`，但存在未提交修改，**不能只用 HEAD 代表当前代码**。逐文件 SHA-256、工作树状态和后端 HEAD 已写入 `source-baseline.json`，Sol 开始前应核对。

## 3. 事实、假设与排除项

### 3.1 已证实的现场事实

| 事实 | 证据及解释 |
|---|---|
| 采集超过 20 分钟 | 用户从 22:34:17 开始；停止时 registeredMembers=70、deliveryCount=70。 |
| 正文覆盖完整 | membersWithoutBody=0、partialTurns=0；不能诊断成 ASR 非终态或非 ASR 事件制造缺口。 |
| 客户端确认水位为 48 | serverConfirmedCount=48；这表示客户端已确认的服务端回执范围，不是独立数据库盘点。 |
| 第一次 401 出现在 22:47:31.855 +0800 | trace `sha256:8ec62d1c0eb37177`，attempt=1，responseBytes=44；约为 Live 开始后 13 分 15 秒。 |
| 随后存在独立 trace 的 401→403 | `sha256:a8e15fad194f1ecb`，22:47:31.881 的 attempt=1 为 401，22:47:31.947 的 attempt=2 为 403。 |
| 失败后采集继续 | 已确认水位停在 48，本地继续到 70，关闭时 queueCount=22。 |
| 手动关闭没有完成业务交接 | closeIntentPersisted 已发生；随后 existingUseCaseNotSafelyResumable；saving → syncPaused。 |
| 声音与聆听本轮仍正常 | 结束标记得到有声回答并自动恢复聆听，记录为 PASS。 |
| 未知写没有被自动重放 | 应保留该安全边界；不能为了排空而取消。 |

日志编号见 `numbered-failure-window.log`，对应原日志 7187–7212、10757–10763 行。`CandidateInbox` 是此处通用 HTTP 日志的命名，不能据此把该请求认定为候选列表接口。

### 3.2 代码事实与尚待验证的对应关系

| 判断 | 证据级别 |
|---|---|
| 两个 Live GET 的生产 feature 路由映射为 nil | 已运行原函数摘录验证。 |
| 401 刷新和复用合法 successor 两条路径均丢弃显式 decision | 当前源码直接确认。 |
| 缺少 captured policy 会被真实服务端 gate 拒绝 | 离线调用真实 `ReleasePolicyCommandGate`，结果 `missingCapturedPolicy`。 |
| 把旧 decision 原样带到新认证会话也不正确 | 真实 gate 对旧 generation 返回 `accountGenerationMismatch`。 |
| 现场第二个 trace 对应只读 delivery 核实 | 根据代码路径和 trace 变化作出的高置信推断；现有日志缺少 endpoint 字段，待补直接请求证据。 |
| 现场 403 就是 missingCapturedPolicy | 最可能解释，尚无当次服务端 reason，不能标为生产已证实。响应字节数相合也不是证明。 |
| 第一次 401 是 access token 到期 | 与长时间运行及 auth 恢复路径一致；也可能是会话轮换等认证失效，不能仅凭时长确定具体到期时刻。 |
| 第 49 段在数据库中一定不存在 | 尚未独立证明。完整的、可识别的前置认证拒绝可以提供未生效证据；仅有 401 数字、读不到回执或水位 48 不足以替代它。 |

### 3.3 本次不能归因或扩展到的内容

- 没有证据表明短场持久化授权修复被回退；同一版本的短场闭环已经真实通过。
- 不是已证实的 48 段数量上限，也不是 20 分钟计时器主动截断；首次失败早于 20 分钟。
- 功能策略 TTL 与 access token 有效期是两种时钟。不能用已有“多次策略 TTL”测试替代认证过期测试。
- 本地完整落盘不等于已经成为待确认记忆，但也不能说 22 段已经丢失。
- `coordinates=localOnly outcome=notSent` 是关闭期限分支的汇总字段，不能据此否定之前 48 个服务端回执，也不能把已曝光的第 49 段当成从未发送。
- 历史任务 UI 仲裁、B7 纯问题过滤、候选审核写、ASR 识别偏差、车载蓝牙和长回答音频均不属于本次根因与修改范围。

## 4. 真实调用路径与定位

以下行号基于本次源码指纹，后续变更后用函数名重新定位。

| 层 | 代码定位 | 当前行为及影响 |
|---|---|---|
| Live fresh authority | [OwnerTruthContracts.swift:15397](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:15397) 的 `withLiveRequestAuthority`、`freshLiveRequestAuthority` | 校验 lease、重新取功能策略、必要时刷新策略；没有独立保证 access token 尚有效。 |
| 认证合同 | [BackendAuthSessionStore.swift:38](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/BackendAuthSessionStore.swift:38) | `isPrivateAccessEligible` 检查合同与 refresh credential；`accessExpiresAt` 已存储，但现有请求入口没有按它做 Live 发前续期。不要修改这个全局 UI eligibility 的语义来替代续期。 |
| Live typed write | [DreamJourneyBackendClient.swift:11907](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:11907) 的 `performOwnerTruthInterviewNaturalInputWrite` | start/append/end 禁止 transport 自动 refresh/replay。应保留该限制，在明确边界补恢复。 |
| 写结果分型 | [OwnerTruthContracts.swift:11546](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:11546)；`receiveAppendWrite` 约 15606 | 完整 backend 4xx 可归 serverRejected，未知响应归 outcomeUnknown；UseCase 失败后不能继续 FIFO。 |
| Coordinator 处理失败 | [EchoViewController.swift:2169](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2169) | 持久化 dispatchState；必要时调用 `verifyDeliveryStatusIfNeeded`。dispatch 更新异步，页面瞬间 unknown 不等于磁盘最终一定为 outcomeUnknown，须实测。 |
| 只读核实 | [EchoViewController.swift:2333](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2333) → [DreamJourneyBackendClient.swift:11604](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:11604) | 使用 `.echoTextInput` fresh decision，GET 从第一个待确认 sequence 开始读取。 |
| 401 恢复 | [DreamJourneyBackendClient.swift:16982](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:16982) | 复用当前合法 successor 和实际 refresh 两分支，递归请求都置 `featureDecision: nil`。 |
| 路由映射与 header | [DreamJourneyBackendClient.swift:688](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:688)、16709、16784 | 映射缺两个 Live GET；没有 prepared decision 就没有 captured policy headers。“requestFeatureChecked reason=allowed”仍会出现，不能当成 headers 完整的证据。 |
| 服务端认证与 gate | [app/main.py:7492](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7492)、10890；[release_policy.py:1050](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/release_policy.py:1050) | 无效 bearer 在进入 handler 前返回 401；Live delivery GET 要求 captured `.echoTextInput` policy，缺少时 403。 |
| 后续续传 | [EchoViewController.swift:2438](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2438)、2495、2220 | 没匹配到成功回执则 unknown；只有剩余项均 preparedNotExposed 才续传；serverRejected 永远暂停。 |
| 关闭时恢复入口 | [OwnerTruthContracts.swift:14735](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:14735) | `resumeCurrentSessionReadForClosingIfSafe` 仅服务于启动前、无 receipt 的只读失败；不能用来恢复已确认 48 段的中途失败。不要放宽它的原保护。 |

典型失败链为：

```text
Live 正文落盘 → append(49, 旧 access token) → 401
  → UseCase failure / Coordinator 核实
  → GET live-delivery-status(旧 token + 完整 policy) → 401
  → 同账号 auth refresh / successor
  → GET(新 token + 丢失 policy) → 403
  → 后续正文继续落盘，确认水位停在 48
  → 停止时 queue=22；已有 UseCase 不能安全恢复
  → 不执行 end/ACK/admit，syncPaused
```

即便将第二个 GET 修成 200，也只有“精确匹配已提交命令”可以清除未知项；读不到第 49 段时仍需要判断它是确知认证拒绝还是未知写。不能把“GET 修好”和“完整保存修好”画等号。

## 5. 最小修复设计

### 5.1 R1：在 Live 写入曝光前完成认证准备

在 Live 请求 authority 获取流程中，增加有界的认证准备步骤，复用现有 `refreshAuthSession` 的 single-flight、CAS、token family 与 successor 检查，不另做一个绕开账号隔离的 refresh 实现。

顺序必须是：

```text
持有原场次/原命令 → 检查当前认证有效期并按需续期
→ 确认同 subject/vault/账号作用域和合法 successor
→ 用新认证会话重新取得 fresh FeatureDecision
→ 再校验 operation/lease/command
→ 持久化将曝光的原命令/attempt
→ 发出一次业务 POST
```

- 使用已保存的 `accessExpiresAt` 和可注入时钟判断。允许有明确依据的到期安全窗口，但不能改成固定每 N 分钟盲刷或固定等待。
- 有效凭据不额外刷新。凭据到期时，在业务请求尚未曝光的阶段完成续期；等待期间 Live 采集和本地落盘继续。
- 处理到期时间解析失败、时钟偏移、等待超时、并发已刷新、refresh 终态失败；不能把不确定时间默认为永不过期。
- 同一账号凭据正常轮换后，FeatureDecision 的账号代次应重新根据 auth session 身份生成；App AccountLease 继续独立验证。**不得重新加入 FeatureDecision generation 与 lease UUID 的跨域相等比较。**
- 认证等待结束、策略刷新结束、磁盘登记前、发送前、回调提交时都验证原 operation 和 scope。账号切换后旧回调不能给新账号发送。
- 注意现有 `withLiveRequestAuthority` 的 `completion(nil)` 会使调用者进入 legacy 请求分支。新增的认证准备失败不能返回这个含义不明确的 nil 后继续发送；必须显式结束当前尝试。非 Live 或原有明确 QA 入口的行为单独保留并测试。
- 不把普通 `requestJSON` 写请求统一改成 `allowsRefresh: true`，不修改候选审核/B8 未知写的传输策略。

### 5.2 R2：保持只读请求的 feature 身份，按新凭据重取授权

优先采用局部方案：补全两条准确的 **GET** 路由映射到 `.echoTextInput`，并使两个 adapter 的认证恢复请求明确采用 fresh request decision：

- `GET /v2/vaults/{vault}/interview-sessions/current`
- `GET /v2/vaults/{vault}/interview-sessions/{session}/live-delivery-status`

查询串应先规范化；严格限制 method 与路径结构，不把所有 `/interview-sessions/*` 都映射为同一功能。核查两个 adapter 以及所有递归分支是否保留 `freshFeatureDecisionAfterRecovery`，覆盖“本请求发起 refresh”及“其他请求已经刷新”两个分支。

这样可保留共享 `requestJSON` 丢弃过时 decision 的意图，同时确保本次 GET 能根据准确 feature 重新取得新 decision。如选择显式传递稳定 feature intent 的方案，必须列明对共享 requestJSON 调用者的影响，并执行第 7 节扩展回归；不要同时叠加两套难以判断谁生效的修复。

请求 headers 必须来自同一次有效授权与当前认证会话。不能只硬编码补一个 feature header，也不能将旧 decision/token 原样复用。策略到期允许一次有界刷新；明确 deny 保持 deny。认证刷新数、策略刷新数、业务 GET 数分别计数。

### 5.3 R3：恢复明确认证拒绝的 append，保持未知写隔离

当前 `.serverRejected` 范围很大，不能直接让它重新入队。增加仅用于 Live append 的内部恢复资格判定，区分以下事实，**不增加面向用户的页面状态**：

| 原写结果 | 允许的动作 |
|---|---|
| 确证未曝光 | 认证准备完成后发送原命令的首次请求。 |
| 精确绑定的成功回执，含只读查到原 command/message/hash | 磁盘确认一次；不再发该 append；继续未曝光队尾。 |
| 完整、可识别且由服务端合同证明发生在业务处理前的认证拒绝 | 保留拒绝事实；同主体合法续期、重新授权和精确场次核实后，允许原 append 一次受控再尝试。 |
| 超时、断连、取消、响应解码/绑定失败、仅有状态码的旧记录、结果未知且读不到回执 | 保持 unknown，只读核实，零自动 append 重放。 |
| 权限/策略明确 deny、scope/epoch 变化、版本冲突或无法证明未生效的其他拒绝 | 保持暂停，不能当认证到期重试。 |

实现要求：

1. 在 BackendClient 保留原始失败的安全分类和绑定信息，不能在 `receiveAppendWrite` 转成一般 `.serverRejected` 后彻底丢掉原因。不得从 UI 文案、trace 字节数或通用 `reason=unknown` 推导“认证拒绝”。
2. 在本地用当前后端真实认证 middleware 的隔离 API 合同测试证明：规定的无效/过期 access token 响应在 handler/事务之前发生，业务方法调用数为零。这是本地代码测试，不要求手机或生产访问。当前源码的完整 `detail="invalid or expired access token"` 响应可作为待验证的既有协议入口；不能把任意 401 都视为同一合同。优先使用已有稳定 code/可验证响应合同，不能为了客户端方便伪造服务端证据。
3. 若本地合同和测试本身无法支撑“明确未生效”判断，则这一自动恢复资格暂不开放。继续完成发前续期、GET 修复等不受阻工作，并解决或明确列出该本地缺口；必要的服务端结构化拒绝合同改动须单独报告影响、本地验证和后续部署要求，不能悄悄扩成生产修改。生产部署是否符合所依据的版本化合同，列入最终真机阶段的适用性核对；它不要求在本地修复前重新制造一次手机 401。没有合同保证的响应始终保持安全暂停，不得为了通过验收放开重试。
4. 拒绝证据须绑定原 productSession、thread/session、commandID、messageID、clientSequence、payload hash、原 attempt 与账号作用域。状态先落盘成功再允许恢复动作。旧版本缺少证明字段的记录默认无自动重试资格，不做历史批量升级。
5. 对可恢复的同场 append，复用完全相同的不可变业务命令与内容；不要新建 start/session/message/command 绕过旧记录。重新核对 session/authorityEpoch/版本，发生矛盾时暂停；不能静默修改 expected version 来绕过冲突。
6. 每个原 append 的认证拒绝恢复最多一次；若再遇到 401、deny 或未知结果，回到有界暂停/只读。不由通用 HTTP retry 自动处理，也不递归循环刷新。
7. 不把 `serverRejected`/`outcomeUnknown` 写回 `preparedNotExposed` 抹掉曝光历史。采用受控 attempt 记录或等价的单调证据结构，保留原命令事实与新尝试的独立生命周期；命名不强制，证据与单调性强制。
8. 为已建立的原 Live 会话提供有证据的 resume/bind 入口，不能通过清空 UseCase、把失败状态强设为 ready、调用普通 start 生成新场次来恢复。只有必要身份和资格已验证后才恢复 FIFO。
9. 原 append 确认后，按 sequence 继续未曝光的尾部。本地队列、服务端确认、owner 计数和 UI 更新均提交一次；迟到旧 attempt 不能把新 attempt 降级，也不能启动第二条关闭链。

这项补齐的是正常会话内、明确未应用的认证失败恢复。不会将冷启动或“核实整理状态”变成自动重放业务写的入口。

### 5.4 R4：排空后沿用已经通过的关闭链

- 保留现有 canonical 接入、finality、停止前排空、partial 处理、close intent、end watermark、ACK/admission 及检查点语义。
- 在该合成 70 段场景中，必须先确认连续水位到 70、队列为 0，才允许按 watermark=70 执行一次 end；之后走已有 ACK/admit/状态轮询。
- 如果失败时用户已经按停止，恢复应继续原 close intent。不能要求再次点击停止才能完成，也不能让用户新开一个 Live 来触发旧场保存。
- deadline 只控制等待预算，不能把剩余队列判作成功；核实成功的迟到结果要按原场次和有效轮次处理，不得越过硬期限或污染新场。
- 当前关闭日志 `localOnly/notSent` 的汇总不能掩盖“48 已确认，49 已曝光，50–70 尚待处理”。补充安全的分阶段计数和原始拒绝类别；不为修复日志重新设计 UI 状态机。

最小诊断还应区分 `liveAppend`、`liveDeliveryStatus`、`liveCurrent`、`authRefresh`，记录 method、脱敏 operation/attempt 归属及 feature metadata 是否存在。当前 `backendErrorContext` 丢掉结构化 `detail.reason`，因此对 `release_policy_denied` 可增加白名单原因记录，避免再次只能看到 403。不要输出 token、授权 headers 的值、正文或整个响应；共享错误解码结构若改变，加入现有调用者的兼容测试。日志里的“allowed”必须能区分真的取得 decision 与没有 decision 的情况。

## 6. 当前失败场次的处理边界

当前手机的 70/48 场次是原始证据，本轮开发期间不启动新 Live、不重装或重启 App、不点击恢复、不清除记录。不得为了获得 PASS 自动补写这 22 段。

新代码能够处理未来的同类场景，与旧版本留下的这一场是否可安全恢复，是两个独立验收项目。本地开发使用按现有报告构造的合成 70/48 场次；如已有合法保存的脱敏磁盘副本，也可用于兼容测试。没有手机快照不阻止本地修复，不为取得快照而操作当前手机。实际旧场恢复方案需检查第 49 段的原命令与曝光状态是否齐全、是否保存完整拒绝证据、其后 21 段是否确为未曝光、服务端是否有精确回执。任何“读不到”都不自动等于未应用。

历史快照缺失拒绝证据时只能保持只读恢复；不能凭本次推断补造字段。真实手机场次的恢复须在局部修复验收后，以明确列出的原坐标、拟执行动作和不重复保证单独形成操作方案。

## 7. 影响范围与强制回归映射

用户要求：之前测试通过的功能不能受影响；无法避免影响时，必须列入验收。Sol 的交付必须逐文件写清“改动 → 受影响行为 → 本地测试及结果 → 最终真机项目及结果”，以下为最低范围。两阶段结果分别记录，真机列不属于本地修复的开工或本地通过条件。

| 预计改动/可能影响 | 必须保留的行为 | A、B 阶段本地必测 | C 阶段最终真机验收 |
|---|---|---|---|
| Live authority 发前认证准备 | 已通过短场 start/append/end，不改变两个 generation 身份域 | 短场真实 Controller 完整组合；发前到期及两种身份域测试。 | 新包短场 → 唯一候选 → 冷启动候选保持；跨认证有效期继续保存。 |
| 同主体 auth successor、共享 refresh | single-flight、CAS、账号切换隔离、refresh 失败的隐私边界 | 生产实现的 refresh 链组合；并发刷新、已刷新 successor、切账号、注销、迟到回调。 | 合法续期后同场继续；受影响账号场景使用隔离测试账号验收。 |
| FeatureGate 路由映射与 GET adapter | fresh 授权、显式 deny；B6/B8 status 与候选/正式记忆读取 | 两个 Live GET 的 401；B6、候选/正式记忆 401 回归；负向路由映射。 | 新版 Live 只读核实、候选及正式记忆读取保持正常。 |
| 若改通用 requestJSON | 其他读取、写请求无自动重放 | 共享分支 HTTP 集成全回归；候选审核/关联组/B8 admission 的 401 零重发断言保持。 | 检查受影响的设备读取及同场 ACK/admit；审核写继续独立列项，不借本轮操作生产候选。 |
| Outbox 新恢复证据/attempt | 正文不丢、dedup、曝光单调、旧快照兼容、跨账号隔离 | 隔离真实磁盘读写、崩溃边界、旧格式、原命令保持、未知写读不到仍零 POST。 | 测试场次的断网/恢复、冷启动后坐标及候选保持；原失败场次恢复单独验收。 |
| Coordinator/UseCase 恢复 | capture 持续、序号连续、排空后才 end、幂等关闭 | 70/48 组合反例、继续采集、停止并发、重复唤醒、完整 Controller 链。 | 物理 20 分钟、认证恢复后排空、三标记覆盖、停止后完成关闭。 |
| close intent/检查点间接受影响 | end/ACK/admit 一次、同场 pendingReview；旧 poll 不污染新场 | B8-S01/S01-08、P01/P02/P03/P07–P12；模拟页面重进及进程重建。 | 新版短场/长场关闭、页面重进与冷启动，无重复写和重复候选。 |
| canonical/音频可能被间接波及 | 非 ASR 过滤、final 不降级、partial 正确、长回答与主动打断 | D1/P13 真实 Controller、音频现有保持性回归。 | 长回答自然播放、主动打断、恢复聆听、完整/partial 表现。 |

保持历史 UI 仲裁独立 FAIL，不借本次修复改它。若测试中仍看到冷启动历史文案误归属，要独立记录；不能误报本场候选未保存，也不能声称该旧问题修好。B7、正式审核写、Provider/音频改造及蓝牙特殊处理均无本轮改动授权。

## 8. 本地修复与测试验证：先红后绿

### 8.1 真实组件组合要求

本节 L20-01～L20-16 全部是本地测试，包括冷启动、账号切换和 20 分钟逻辑时钟的本地模型；不是要求先在手机上执行。测试使用真实 Controller → Coordinator → UseCase → 隔离磁盘 Outbox/Checkpoint → BackendClient → FeatureGate；网络用受控 URLProtocol，后台行为用隔离合成数据。“真实组件”指使用实际实现，不代表连接生产服务或操作真机。每次请求需检查实际 URL、method、完整业务身份、payload hash、auth successor 与政策 headers，不只检查 spy 被调用。

至少一项认证恢复组合测试走生产 `refreshAuthSession`、隔离 AuthSessionStore/AccountSessionActor、真实 FeatureGate 和 `/auth/refresh` 受控网络；仅在测试闭包中替换 auth session，不能证明生产 CAS/single-flight 链正确。所有账号、token、正文均合成，磁盘目录与真实用户隔离。

已有 `testManagerEchoRealGateBackendLogicalTwentyMinutesClosesAtExactWatermark` 使用固定 `authSession` 与固定由它生成的 generation，注入变化的是策略 TTL。这就是原本“逻辑 20 分钟 PASS”没有覆盖此次认证轮换的具体原因。保留这个测试，新增同时变化认证与策略的组合，不用修改原断言隐藏缺口。

### 8.2 新增验收矩阵

| ID | 场景 | 必须断言 |
|---|---|---|
| L20-01 | delivery-status GET：401 → auth refresh → 再 GET | 修前捕获第二次请求缺 metadata 并稳定失败；修后 token 与 policy generation 同属新会话，成功 GET；业务 POST=0。 |
| L20-02 | current GET；另一个消费者已经完成 refresh | 原调用使用合法 successor，无第二次 refresh；完整 fresh headers；非法 successor/换账号不得重试。 |
| L20-03 | access token 到期，但 FeatureGate 策略仍有效 | 发前走认证准备；过期 token 的 append 曝光数=0；新 token+fresh policy 发原命令一次。覆盖到期时间解析失败、时钟边界。 |
| L20-04 | auth 有效但策略到期、两者同时到期、明确 policy deny | 分别计数 auth/策略恢复；明确 deny 零业务 POST，无循环刷新。 |
| L20-05 | 服务端真实认证 middleware 的前置拒绝 | 合同化 401 的 downstream handler/事务调用数=0；其他 401、403、5xx、非 JSON 不可获得认证未应用资格。 |
| L20-06 | 完整组合：前 48 确认，第 49 次 append 在请求途中认证失效，随后本地到 70 | 修前复现中断；修后原 49 获有效未应用证明时只追加一次受控 attempt，业务身份和正文不变，50–70 首次发送；水位 70、queue=0、end/ACK/admit 各一次、pendingReview。 |
| L20-07 | 第 49 次写已应用但响应丢失 | 精确只读确认原 command/message/hash 后推进队尾；第 49 次 POST 总数保持 1。 |
| L20-08 | 第 49 次真正未知、GET 不含它/读不到/返回错误 | 仍 unknown，零重放，不越过缺口，不 end/ACK/admit，不显示成功。证明安全暂停不是被“修没”。 |
| L20-09 | 49 为权限拒绝、业务版本冲突、回执串场或 authorityEpoch 改变 | 无认证重试资格，不篡改版本/命令，不建立新 session；正文和坐标保留。 |
| L20-10 | 保存拒绝证据或新 attempt 前磁盘失败；保存后进程中断 | 没有落盘证明就零发送；冷启动可核实原命令，不能制造新的业务写；旧无证据快照不被自动放行。 |
| L20-11 | 续期期间继续采集，并发停止、重复唤醒 | capture 持续、close intent 一次，使用停止时真实尾水位；只有一个队列 owner、一个 auth refresh 和一个关闭 owner。 |
| L20-12 | A 场/旧 attempt 回调迟到，新 B 场已开始或账号已变 | A 不清 B 的 poll/队列，不消费 B 预算，不写 B 磁盘/页面，不重复 ACK/admit。 |
| L20-13 | status 迟到、刷新超时、页面离开重进、Controller 重建 | 有界等待、原坐标保留，重进后只读验证；未核实前不伪报成功。覆盖真正迟到成功和迟到失败。 |
| L20-14 | 多次 access token 轮换 + 多次策略 TTL + 短暂断网 | 每次业务发送合法；凭据换 sessionId、generation 真实随之变化；队列连续、身份固定、没有捕获停止或重复候选。 |
| L20-15 | 新版完整短场闭环及 partial 保护 | 已通过的生产 generation/lease 分域、完整关闭与候选链仍通过；partial 分支不被伪装完整。 |
| L20-16 | 完整保存后冷启动 | 候选存在且唯一，零重放 start/append/end/ACK/admit；历史文案仲裁若失败仍独立记录。 |

L20-01、03、06 至少保留“修前同断言失败 → 修后同断言通过”的真实组件证据。不得把 mock 直接返回 ready/pendingReview 作为修前反例或修后证明。新增判定器和探针通过，不等于组合链通过。

### 8.3 现有回归不得削弱

至少保留并执行这些已存在的测试或它们经证明等价的当前入口：

- `testLiveOutboxPersistsExposureAndRefusesUnknownToExposedRegression`
- `testLiveUnknownStartReconstructionUsesExactReadOnlyStatusWithoutSecondPost`
- `testLiveUnknownPreparedStartCannotUseCurrentSessionAsCommandProof`
- `testLiveCaptureReadOnlyStatusConfirmsLostAppendWithoutSecondPost`
- `testManagerEchoRealGateBackendLogicalTwentyMinutesClosesAtExactWatermark`
- `testB6Status401UsesOneSameScopeAuthSuccessorAndNeverWrites`
- `testRealHTTPClientRecapturesPolicyOnceAfter401CredentialSuccessorChangesGeneration`
- `testRealHTTPReview401IsUnknownAndNeverRefreshesOrRepostsWriteCommand`
- `testRealHTTPRelatedGroup401NeverRefreshesOrRepostsAndUsesReadOnlyLookup`
- `testB8RealBackendAdmission401NeverReplaysBusinessPost`
- B8-S01 旧 poll 归属、OwnerTruth 全回归、Live/音频保持性及 D1/P13 覆盖摘要测试。

此前 509 项、56 项等只作为历史基线，不能抄作本轮结果。本轮须读取新的 xcresult，记录实际测试名称/数量/失败/跳过。模拟器、通用 iOS 编译和 `git diff --check` 单独记录。

## 9. C 阶段：最后进行真机验收

只有阶段 A 本地修复、阶段 B 全部适用本地验证通过并完成交付，且用户之后另行主动提出开始真机测试，才执行本节。当前只进行 A、B，交付后结束本次任务，不检测手机、不等待连接或真机授权。本节仅为后续验收清单，不是本地任务完成条件；开发和本地验证期间不覆盖安装或操作当前失败手机场次。

1. **先保住短场。** 新包使用新的合成标记，验证正常有声交互、停止后及时 pendingReview、end/ACK/admit 同场、真实候选唯一、冷启动候选保持。短场失败立即停止，不能继续用长场掩盖回归。
2. **再测认证边界。** 在允许的测试环境/账号下控制凭据时效或真实等待已知有效期，保留脱敏的续期次数与阶段证据；不更改全体生产 token TTL、不打印 token，不靠随意断网假装 401。
3. **物理 20 分钟。** 首、中、末三个唯一标记；持续采集、跨认证边界、停止时服务端连续确认水位等于本地应交付尾水位，queue=0，end/ACK/admit 成功，全部标记可关联到候选来源。候选可合法合并，不强制“一段一个候选”，但不能只用列表数量增加证明整场覆盖。
4. **受控断网/未知写。** 在阶段 B 已通过隔离网络两种分支测试的基础上，最后进行适用的真机断网/恢复验收；区分“已应用响应丢失”和“真正未知未查到”，不得通过重放未知业务写达成成功。
5. **新包受影响保持性。** 长回答自然播放、主动打断、自动恢复聆听；完整/partial、页面重进、账号/旧回调隔离按影响清单复测。

在未实际执行前，本节新版真机项目全部保持 NOT_RUN；这不妨碍满足阶段 B 条件后交付 LOCAL_PASS。原失败版本的 DEVICE_FAIL 继续保留。当前失败场次如何恢复另行验收，不拿新场 PASS 冒充旧场恢复。真实数据库合同、真实 Provider、真机测试与本地 URLProtocol 测试分别记录。

## 10. Sol 执行与交付要求

按第 0 节顺序立即开始：在本地按 R1/R2/R3/R4 实现并保留本地修前反例，完成后统一执行第 7–8 节本地验证和回归，交付后结束本次任务。第 9 节真机验收仅由用户之后另行主动发起，不自动进入，也不等待手机。不要因设备未连接或真机尚未执行而中断本地开发或交付，不夹带历史 UI 或其他 Live 功能改动。

交付到新的 outputs 目录，至少包括：

1. 根因与现场对应说明，标清“源码已证实”“受控复现”“生产尚未证实”。若取得合法的服务端只读日志，补充 endpoint、脱敏 trace、deny reason、时间与部署版本；没有则明确保留限制。
2. 修前/修后同断言证据，真实 Controller/磁盘/BackendClient/FeatureGate 与 auth refresh 组合证据。
3. 逐文件影响清单及对应已通过、失败、跳过的测试；对共享层修改解释扩展回归范围。
4. 原命令身份与 attempt 记录设计、旧格式兼容、未知写零重放证据；不能用删除坐标、重置 exposure、清历史或固定延迟修复。
5. 短场保持性与长场认证恢复的本地结论分别列出；源码与构建指纹，确认包含此前已通过修复。
6. 未执行的手机/生产/数据库项目及当前 70/48 现场处理方案。

本地完成可以标 `LOCAL_PASS / DEVICE_PENDING`，条件是所有适用本地验收已执行通过且没有本地关键阻断；不得写 `DEVICE_PASS`。如本地认证拒绝未应用合同或 L20-06 组合链仍未建立，标 `LOCAL_INCOMPLETE` 并继续解决具体本地缺口，不能仅凭 GET 200 标 READY。真机尚未执行、当次生产日志尚未取得、旧手机场次尚未恢复分别列为后续核验事项，不能单独作为停止本地修复或否定本地已通过结果的理由。

本轮目标是恢复完整同步与保存，不增加新的成功文案、不延长 saving 来掩盖未排空、不降低安全门禁。此前短场真实保存链的 PASS 是本次必须保护并重新验收的基线。
