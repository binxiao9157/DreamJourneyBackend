# DreamJourney Live 记忆候选整理失败：局部修复设计与 Sol 执行要求

事件日期：2026-09-18；设计定稿：2026-09-19。\
用途：交给 Sol 顺序完成本地开发、验证和交付；另附由用户主动发起的真机验收清单。\
主问题编号：**DJ-LIVE-CANDIDATE-01：正文已保存，但后台候选整理首次失败，待确认记忆无新增。**\
伴随问题编号：**DJ-LIVE-CANDIDATE-02：整理失败后，本地恢复坐标被按成功场次清除。**

## 1. 执行约定：Sol 必须先读

### 1.1 本次授权的开发目标

本次开发要完成以下实际行为，不能以新增日志、修改文案或调整轮询次数代替：

1. 从真实 Live Source 进入真实模型适配器、独立支持复核和候选事务的整条链，在本地受控网络下可验证。
2. 整理失败能够准确定位实际阶段及安全原因，不再把所有 ValueError 都误标为模型响应错误。
3. 明确可恢复的模型输出问题，在**原后台任务的持久尝试预算内**获得一次有界恢复机会；只有完整校验通过才能生成候选。
4. 持续失败时，不伪造候选或成功状态，保留 Source 和客户端失败恢复坐标。
5. 本地同时验证候选确认后成为正式记忆的路径，保护之前已通过的功能。

先完成主问题 01 的本地修复与测试，再完成伴随问题 02，最后执行受影响回归。阶段之间不等待手机、不等待真机验收，也不把不同问题合成一个根因。

### 1.2 本地开发和真机验收严格分开

**本次任务顺序只有：本地复现与修复 → 本地测试与回归 → 交付本地报告、源码指纹及后续验收清单 → 正常结束本次开发任务。**

- 真机测试由用户在之后另行主动发起，不属于本次任务的自动步骤或完成条件。
- 不检测、连接、安装、启动或等待用户手机；手机已经连接也不表示用户授权开始真机测试。
- 不因没有手机、手机未解锁、未配对、未授权真机测试而暂停任务、标记 BLOCKED、提前交付半成品或要求用户操作手机。
- 使用本地模拟器、受控 HTTP、隔离磁盘、隔离 PostgreSQL。通用 iOS 构建使用无签名 generic iOS 目标，不绑定手机 UDID。
- 真实 DeepSeek 调用与生产部署均不在本次本地执行范围内。准备后续验证入口即可，不等待凭据或供应商验证；缺少这两项不能成为不完成本地工作的理由。
- 真正无法自行解决的本地编译、依赖或数据库问题应如实报告具体错误；不得跳过关键测试标 PASS，也不得转向手机掩盖本地问题。

本地完成后使用：`LOCAL_PASS / PROVIDER_NOT_RUN / DEVICE_NOT_RUN`，并写明：**“本地开发已完成；真实供应商与真机验证尚未执行，真机由用户另行主动发起。”**随后结束任务，不继续等待手机。

### 1.3 当前事实边界

两场历史失败的准确抛错检查点没有被旧代码保存，不能从相同宽泛错误码还原唯一根因。本文要求修复已经确认的分类、恢复策略、验证缺口和失败坐标保护，不能编造旧模型响应或声称已经重放历史现场。

“历史精确触发点未恢复”不是停止全部本地开发的理由；但它必须作为证据限制保留。受控 HTTP 通过也不能写成真实 DeepSeek 或真机已经修复。

## 2. 必读材料与工作区

按以下顺序阅读，先核对当前文件和 diff，再开发。附件和旧报告是证据，不是要求无条件重做历史任务的指令。

1. 本轮原因核查：
   `/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-short-repro/run-01/2026-09-18-Live长短对话候选缺失-原因核查.md`
2. 精确场次关联证据：
   `/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-short-repro/run-01/evidence/server-session-whitelist.json`
3. 部署接口、源码指纹和 Worker 诊断：
   `/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-short-repro/run-01/evidence/server-worker-whitelist.json`
4. 短场观察：
   `/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-short-repro/run-01/observations.md`
5. Sol 长场原报告：
   `/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-l20-auth-sync-device-retest/run-2026-09-18-01/reports/2026-09-18-DreamJourney-Live长会话认证同步真机复测报告.md`

后端工作区：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`。\
iOS 工作区：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`。

本次设计检查时，后端 `tests/test_owner_truth_interview_input_api.py` 已有未提交修改；其他工作区也有此前交付内容。必须保留，禁止 reset、整体回退或用旧版本覆盖。开始时重新记录 HEAD、diff 和关键文件 SHA-256；本文行号仅为定位辅助，函数名与实际代码为准。

## 3. 已证实事实、排除项与目标链

### 3.1 两场实际失败

| 项目 | 长场 | 本轮短场 |
|---|---|---|
| workflow 指纹 | `sha256:e716db2a62d198a3` | `sha256:0f46a9c3dfd5f672` |
| 服务端连续接收序号 | 32 | 4 |
| Source 用户/助手条数 | 16 / 15 | 2 / 1 |
| Source 总字数 / 最长一条 | 2,018 / 212 | 162 / 82 |
| 封存提交 | end、ACK、admit 成功 | end、ACK、admit 成功 |
| 后台结果 | attempt 1，failed，候选 0 | attempt 1，failed，候选 0 |
| 失败码 | `candidateExtraction.responseContract.invalid` | 同左 |

两场 Source 均 active，batch 均 acknowledged，session/batch/source/job/extraction 已精确关联。候选缺失不是根据页面文案推断，而是已核对后台结果。

实际候选整理接口：`api.deepseek.com`。Live 语音接口：`openspeech.bytedance.com`。两者分开，禁止把后台整理失败直接归因于火山 Live。

### 3.2 必须纠正的前提

- 原长场报告中的“本场 pendingReview，候选只是不可见”不能继续作为事实。服务器确认该 Source 失败、没有生成候选。
- 长场的六次 GET 预算耗尽是另一个客户端观察问题；短场读到了明确失败终态，不是同一现象。
- 两场输入均未超过当前长度限制。已发现的“分块组织、整场复核超限”是另一缺口，不是这两场的已证实原因。
- 两场均包含所有已登记用户发言；这不等于逐字验证了 ASR 与原始声音完全一致。
- 整理 Source 取第一条至最后一条用户消息之间的窗口，末次助手回复不在窗口内。本轮保持此窗口合同，不能无证据扩展为“修改为包含末次助手回复就解决失败”。

### 3.3 本轮要保证的链路

Live 正文采集与上传 → 原场 end → ACK → admit → 不可变 Source → 组织请求及解析 → 独立支持复核 → 候选原子提交 → 同场状态与候选列表一致 → 用户确认 → 正式记忆读取。

本轮主修改集中在 Source 后的后台整理，以及失败终态的本地恢复记录。前半段已经通过的同步与封存代码必须保护。

## 4. 修改边界与禁止事项

### 4.1 可修改的最小范围

| 模块 | 本轮职责 |
|---|---|
| `app/services/deepseek.py` 中 Live 专用适配器 | 输入/请求/响应各阶段的准确错误，受控传输注入，固定修复提示；不改变其他模型适配器默认行为 |
| `app/services/owner_truth_live_memory_support.py` | 保持语义准则，给拒绝原因明确类型与安全枚举 |
| `app/async_effects/owner_truth_candidate_extraction_worker.py` | Live 失败分类、持久 attempt 内恢复、阶段诊断、原子提交前后错误归属、逐任务日志 |
| `app/async_effects/lease_repository.py` 及其内存实现 | 在有效 lease 下读取同 job 前次失败的最小安全摘要，如现有接口不足 |
| `EchoViewController.swift` 的 Capture 终态收尾 | 失败/quarantine 保留原恢复记录，成功清理保持现状 |
| 对应测试和本地验证脚本 | 真实适配器、Worker、Postgres、Controller、磁盘、BackendClient、FeatureGate 组合证据 |

允许增加小型 Live 专用错误/尝试上下文类型，不得借机重构整个提取框架或新增通用任务系统。优先复用现有 `failure_code`、job_attempts、状态 API、follow-up observation；本轮默认不新增数据库表、对外 API 或用户可见状态。

### 4.2 本轮不修改

- Live SDK、音频、蓝牙/车机、长回答播放、主动打断。
- 已通过的 canonical 采集、非 ASR 过滤、停止排空、覆盖摘要和 partial 文案。
- start/append/end/ACK/admit 的幂等、曝光状态、认证恢复和未知写保护。
- B7 的用户证据约束、纯问题过滤、纠正/撤回及事实完整性要求。
- 历史任务 UI 仲裁、核实按钮任务归属、六次轮询预算、第二轮气泡不更新。这些保留独立问题状态，不拿来替代本轮后台修复。
- 长输入超限的全局分块复核架构。本次正常长场未触发该限制，不通过抬高阈值或截尾将其混入本轮。

如果真实红测证明需要触及上述受保护代码，应先完成影响分析并写入报告，采用能满足反例的最小修改和对应回归；不得顺手重写无关模块。涉及产品规则、模型更换、历史重处理或额外外部操作的范围变化需另行交由用户决定；不妨碍先完成其他本地项。

### 4.3 明确禁止

禁止把错误吞掉改成功、跳过复核、自动把 uncertain 改 supported、把遗漏事实改成 query、将对话全文直接伪装成记忆、只保留通过的部分就宣称完整、只改 UI 文案、增加固定等待掩盖失败。

禁止重发未知 Live 业务写，禁止通过新建 job 绕过旧 job 预算，禁止自动重放本轮历史失败场次，禁止修改生产或历史数据、部署、手机操作、候选正式审核写、commit/push。这里“候选正式审核写”指真实用户环境；第 9 节隔离本地合成夹具的确认事务必须测试。

## 5. D1：先建立会失败的真实组合测试

必须先锁定反例，然后改业务实现，最后以相同断言转绿。不能只增加绿色 mock 测试。

### 5.1 真实部件要求

后端组合使用真实 `DeepSeekLiveMemoryOrganizationProxy`、Live Extractor、支持复核器、Worker、lease/attempts 和候选服务。仅在 HTTP 传输边界返回受控响应；响应必须是实际 HTTP envelope，例如 choices/message/content，而非直接注入最终 memories。

使用隔离 PostgreSQL 验证真实 UoW、回滚、重启后的 attempt 和幂等；内存单元测试可补充，不能替代事务验收。

参考现有 `tests/test_owner_truth_candidate_extraction_worker.py`，但其 `_RecordingLiveMemoryOrganizer` 会按草案覆盖情况把未覆盖 user turn 自动标为 query。**新关键组合测试禁止使用这种自证式复核。**语义标签和期望事实必须由独立固定夹具提供，不能从被测输出倒推正确答案。

### 5.2 至少先暴露以下缺口

1. 请求前 ValueError、组织响应坏 JSON、复核遗漏事实、proposal 构造错误和事务领域冲突被归为同一响应错误。
2. 首次明确可修复的生成输出错误直接终结；同一 Source 若下一次给合法输出，本可以形成正确候选，却没有恢复机会。
3. 连续两个不同 job 同类失败，循环日志去重丢失后一个 job 的结果。
4. iOS 收到失败终态并成功落盘观察后，三类恢复记录被清除。

夹具至少有短形状“2 user + 1 assistant”和长形状“16 user + 15 assistant”，使用原创合成正文与独立事实清单。不能凭长度相同就宣称重放了旧现场。

## 6. D2：让失败位置、类型和结果可被准确区分

### 6.1 类型与阶段

给 Live 链引入明确异常/结果类型，至少包含固定 stage、固定 reason、失败来源类别、可恢复资格，以及可选 HTTP status。不要通过匹配自然语言异常文本做业务决策。

最低阶段：`sourceRead`、`organizationInput`、`organizationRequest`、`organizationDecode`、`organizationValidate`、`supportInput`、`supportRequest`、`supportDecode`、`supportValidate`、`proposalBuild`、`candidateCommit`。

安全原因至少区分：输入缺失/绑定无效、配置缺失、HTTP envelope 无效、空内容、JSON 无效、输出截断、schema 不符、非法或越界证据、复核覆盖不全、语义 uncertain、事实缺少最终草案、proposal 合同错误、提交合同错误。

具体命名可遵循仓库规范，但需要在代码中固定枚举并在报告列出映射。未知异常可保留兜底 `unclassified` 和实际阶段，不能硬标为 responseValidation，也不能凭猜测获得模型修复资格。

### 6.2 分类和兼容

- Worker 优先识别 Live typed error；非 Live 既有分类不因本轮发生隐式变化。
- 保留 HTTP 401/403、402、404、429、5xx、timeout、transport 的既有处理语义，补充其实际组织/复核阶段。
- `AsyncEffectLeaseLost` / `AsyncEffectLeaseCancelled` 保持原控制流，禁止包装为普通模型错误后重试。
- 已解析后的领域/提交错误必须标在 proposalBuild/candidateCommit，不能误导为供应商返回错误。
- 将具体安全码写入现有 job_attempts 和最终 extraction 的 failure_code；状态接口继续可读，旧客户端不因新安全码解析失败。
- 不记录原始异常文本、对话、完整请求/响应、headers、token 或 API key。内部抛错对象也避免意外携带可被 logger 展开的原始响应。

### 6.3 阶段诊断

记录实际到达的阶段，不用失败分类标签冒充执行证据。至少能分辨：已构造输入、HTTP 调用开始、HTTP 返回、envelope/content 解码、组织结构校验、独立支持复核、候选提交。

只返回安全字段：job/source/batch 的脱敏关联、attempt、stage/reason、chunk 序号、输入数量/长度、HTTP status、固定 finishReason、模型/prompt 版本、实际组织/复核调用计数及候选数量。

“HTTP 调用开始”仅证明本进程调用传输，不声称供应商已经收到；只有观察到响应才记录相应状态。缺失阶段不能用时间推算补造。

循环日志只允许合并无任务的重复 idle/blocked 心跳。有 job 的完成/失败必须按 job + attempt 留下独立结果；不能继续仅按 status + reason 去重。日志失败不能改变业务结果。

## 7. D3：修复生成合同处理，复用持久预算执行有界恢复

### 7.1 先修合同，再增加恢复能力

核对真实请求构造、HTTP envelope、内容提取、JSON、组织 schema、独立支持 schema、领域构造的前后合同。对“满足正式合同却被错误拒绝”的情况，必须用独立 fixture 先红后绿修实现，不能只靠再请求一次掩盖解析缺陷。

对既有合法兼容行为保持回归。任何新增规范化只能是确定、无信息损失且不改变证据语义的处理；不得补造 sourceTurnIndices、用户事实、支持结论或缺失内容。

必须特别覆盖 HTTP 200 但缺 choices、content 为空/类型不符、坏 JSON、截断、非法证据下标、布尔值冒充整数、缺用户/草案评估、引用助手作为事实等情形。它们不能被“HTTP 成功”直接升级为候选成功。

### 7.2 唯一预算规则

**不在 DeepSeek adapter 内加循环，也不为恢复新建 job。复用现有持久 job.attempt、job_attempts 和 maxAttempts。**

仅当当前 `attempt == 1`，错误是第 7.3 节允许的生成输出错误，且原 job 仍有预算时，允许释放原 lease 为 retryWait，申请下一次执行。该次失败不写最终 failed ExtractionResult，不生成完成 receipt 或部分候选。

`attempt >= 2` 再出现同类契约错误，必须明确终结，不再授予契约修复机会。maxAttempts 原本不足 2 的 job 不提高预算。

HTTP transient 沿现有策略在原 maxAttempts 内处理。因此 `contract → HTTP transient → success` 可能有第三次执行；**不得写成“任何情况下最多两次模型调用”。**准确上限是：契约错误只在 attempt 1 有一次申请资格，所有执行仍受持久 maxAttempts 限制；每个 attempt 不再嵌套恢复循环。

进程重启、重复调度和并发领取不能重置预算。不得通过把 retryable 统一设 true、提高 maxAttempts、无限 retryWait 或新建任务绕过限制。

**必须补齐请求前预算检查，不能只修改失败后的 release_retryable 条件。**现有 lease repository 对过期 leased 记录重领时会递增 attempt，不能假设它已替 Live Worker 拦截超预算执行。对本轮 Live 路径，每次实际模型执行前验证持久 attempt 与原 maxAttempts；超限重领只能在有效 lease 下核对已有幂等完成结果或持久化明确预算耗尽终态，不得再发模型请求，不得留下部分候选或无限 leased/retryWait。

不把此处变为所有 Worker 的全局领取策略重构。如清理性重领占用额外 attempt 行，报告应区分“重领/终结次数”和“允许执行模型的 attempt 数”，不能虚假断言数据库 attempt 数永不超过 maxAttempts。最后一次允许执行在模型返回后、提交前进程退出，必须由真实数据库反例验证该边界。

### 7.3 固定资格表

| 失败类别 | 模型输出恢复资格 | 处理 |
|---|---|---|
| 有响应但 envelope/content/JSON/schema 无效、输出截断 | 仅 attempt 1 且有预算 | 严格区分原因，原 job 后续执行重新组织和完整复核 |
| 生成结果证据下标/绑定不合法、独立复核结构缺项 | 同上 | 拒绝原草案，不矫正为“合法事实”，可重新生成 |
| 独立复核 uncertain、遗漏明确用户事实、事实无最终草案 | 同上 | 重新组织全部 Source 并独立复核；不放宽 B7 |
| 合法纯问题/无新事实，且完整复核支持空结果 | 不需要恢复 | 成功空结果；不能声称生成了待确认候选 |
| 输入、配置、Source/account/epoch 无效，模型开关拒绝 | 无 | 模型请求数必须为 0 或在发现处停止，准确报告 |
| 权限、lease 丢失/取消、领域构造或提交合同错误 | 无 | 走原保护/事务路径；不以模型重试处理内部错误 |
| HTTP 401/403/402/404 | 不属于契约修复 | 保留既有供应商错误策略，不混同 iOS Live 认证恢复 |
| HTTP 429/5xx/timeout/transport | 不属于契约修复 | 保留原持久网络重试预算，不叠加内部循环 |
| 未分类 ValueError/其他未知异常 | 无自动契约修复资格 | 保留真实阶段和安全兜底码，不猜测 |

表中“模型生成结果绑定错误”指尚未信任的草案证据错误；“Source/account/epoch/lease 绑定错误”指系统权限和任务归属错误，二者必须用不同类型区分。

### 7.4 后续 attempt 如何恢复

1. 在当前有效 lease 下，读取**同 job 合法前序 attempts**的固定安全失败摘要。优先扩展现有 repository；如需新增，内存/Postgres 两实现一致，至多返回最近前次摘要与 attempt1 的白名单契约失败摘要（attempt + stage/reason），不返回旧正文或草案。
2. 禁止跨 job 查询“最近错误”。过期 lease、不同 owner/vault/epoch、跳号/不匹配记录不能提供修复上下文。
3. 只有白名单安全码可以映射为固定修复提示，例如“完整返回指定 JSON”“覆盖全部有事实的用户轮次”。`contract → HTTP transient → 后续 attempt` 时，可沿同 job 的持久记录继续使用 attempt1 的安全契约反馈，不能仅因最近一次变为网络错误就丢失该反馈；保留反馈不授予新的契约修复资格、不增加预算。禁止把任意数据库错误码或原始异常直接拼入 prompt。
4. 从原不可变 Source 重建组织输入；重新生成全部草案，再独立复核全部最终草案。不得只追加一条遗漏候选后宣称整场完整。
5. 不持久化未验证的中间草案，不新增隐式缓存事实来源，不更换模型或供应商。
6. 发模型请求前执行第 7.2 节的持久预算检查；heartbeat 保持；网络请求在数据库事务之外；提交前重新验证原 lease、authority、source version/hash 和幂等身份。
7. 只有全链通过才在原事务中提交 extraction、候选、consumer receipt 和任务结果。消息通知仍是辅助事务，失败不能回滚已存在候选。

固定反馈比保留旧模型原文更小、更安全，但不能保证所有错误必然恢复。持续失败必须真实终结；验收关注有界、正确性和恢复能力，不承诺任意模型输出均能成功。

### 7.5 明确不是历史重处理

新逻辑仅作用于按正常队列规则可执行的任务。不得主动选取此前 terminalFailed/dead-letter 的两场重新执行，不恢复其预算、不重发 admit、不把旧失败状态批量改成 pending。历史 Source 的受控重处理需用户另行明确发起，本轮只保留后续操作所需证据。

## 8. D4：失败后保留 iOS 恢复依据

定位 `EchoViewController.swift` 的 `receiveSameSessionStatus`、`finishSameSessionObservation`、`clearCompletedOutboxThenFinishOrganization`。本轮只增加内部清理策略分流，不新增用户状态或磁盘 schema。

| 状态 | 处理 |
|---|---|
| pendingReview / empty | 保持已有成功清理；仍须先持久化终态观察；重复回调/已删除文件幂等 |
| terminalFailure / quarantined | 保留原 Outbox、follow-up、completion checkpoint 和已有 lastRecoveryObservation；停止 reader，解除观察关系，正常结束当前捕获生命周期 |
| 失败观察写盘失败 | 保留三类原坐标，保留真实失败状态，记录安全错误；不能删除剩余坐标或显示成功 |
| 未取得终态 | 保持现有未知/等待路径，不执行成功清理 |

正常保留失败记录不能被记为“等待重试删除的 cleanupPending”，否则未来恢复清理仍可能误删。

冷启动通过现有 RecoveryService/Coordinator 与 fresh BackendClient GET 核实原场，绑定原 account/vault/epoch/productSessionID/reviewBatchID。恢复失败记录不代表重放业务：start/append/end/ACK/admit 均不得新增，后台提取也不得重新提交。

现有冷启动成功恢复并不统一执行 Capture 的三项清理，本轮不扩大为历史清理系统。旧 V1/V2、缺 observation、损坏或 scope mismatch 按原规则处理。历史版本已删除的坐标不能凭空重建，也不能拿另一历史场次替代。

本项解决失败恢复依据丢失，不承诺解决多历史任务 UI 仲裁。只有一个明确绑定场次的 Controller 用例不能标记“全部历史 UI 问题已修复”。

## 9. 本地验收矩阵：执行完才交付

### 9.1 后端主链与真实 HTTP 合同

| ID | 场景 | 必须断言 |
|---|---|---|
| BE-01 | 两轮短场，明确个人经历 | 真实请求构造/解析/独立复核；Source 保留；候选准确、同场 status reviewReady、列表可见；无自动正式记忆 |
| BE-02 | 16 user + 15 assistant 长形状，多主题且有首中尾标记 | 所有 user indices 原样进入组织输入与完整复核；事实清单均有正确结果或合法纠正/撤回；不靠总条数代替内容完整性 |
| BE-03 | 输入/config 错、组织 envelope/JSON/schema 错、support 错、proposal/commit 错 | 精确 stage/reason；前置错误零模型请求；提交错误不冒充供应商错误；具体安全码可读 |
| BE-04 | attempt1 组织格式错 → attempt2 正常 | 原 job 持久 retryWait，重新读取原 Source；候选/receipt 最终只提交一次 |
| BE-05 | attempt1 支持格式错或遗漏事实 → attempt2 正常 | 第一次无部分候选；第二次全部重生成并独立复核；遗漏与既有事实都覆盖 |
| BE-06 | contract→contract、持续 uncertain/非法证据/遗漏 | attempt2 有界失败；候选 0；Source 不变；具体原因保留 |
| BE-07 | contract→HTTP transient→success；HTTP transient→contract；maxAttempts=1 | 模型执行不越原预算；attempt≥2 的契约错误不再获得机会；同 job 契约反馈跨 transient 保持；HTTP 策略不被嵌套放大 |
| BE-08 | retryWait 后重建；最后允许 attempt 在模型返回后/提交前退出，lease 过期后重领 | 从真实数据库恢复预算及摘要；超限重领零新增模型调用，无半候选，原任务明确终结或读到已有幂等结果；提示不跨 job/账号 |
| BE-09 | 合法纯问题、确认问法、反问、助手诱导 | 无伪事实；合法无事实走成功空结果；不得把明确事实标 query 让测试通过 |
| BE-10 | 同轮/跨轮纠正、否定、撤回、事实与问题混合 | 只保留最终受支持事实，B7 既有语义断言不降低 |
| BE-11 | heartbeat/lease 失效、Source/version/epoch/权限变化、并发 Worker/重投 | 旧结果不能提交；原子性与幂等；模型等待期间不长期占 DB 事务 |
| BE-12 | 同类错误的不同 job；诊断系统失败 | 每 job/attempt 有独立安全结果；诊断故障不改变业务；敏感内容不泄漏 |
| BE-13 | 状态 GET 与候选 GET 的同场一致性 | reviewReady 必有同源、同账号、同 epoch 的有效待确认候选；failed 不伪称 reviewReady；禁止借历史场通过 |
| BE-14 | 本地合成短场/长场候选确认 | 经真实确认服务/API形成正式 Memory/Version；销毁并重建服务/仓库后，正式列表/详情与来源仍可读；重复确认幂等 |

在 BE-01/02 中为合成事实预先定义期望集合。真实模型可将一轮拆成多条草案，故不把“候选条数必须等于发言条数”作为规则；检查事实覆盖、证据和去重。

BE-14 必须走正式候选确认合同，不直接 INSERT 正式记忆模拟成功。正文保存不等于候选成功，候选成功也不等于正式记忆已确认。

### 9.2 iOS 真组件与磁盘

| ID | 场景 | 必须断言 |
|---|---|---|
| IR-01 | 真实 Capture + 同场 reader 返回 failed | 修前暴露错误清理；修后原三类记录和安全失败观察都保留 |
| IR-02 | quarantined 与失败观察写盘失败 | 状态不伪造；坐标保留；正常保留不进入删除重试 |
| IR-03 | 销毁 Controller/Coordinator/Registry/Store，以同磁盘重建 | 原失败场可发现；真实 BackendClient + FeatureGate 发同场 fresh GET；业务 POST 为 0 |
| IR-04 | 页面离开重进、失败结果迟到、账号/epoch 变化 | 不污染新场/新账号，不触发旧场清理；原失败记录保留 |
| IR-05 | pendingReview/empty 成功，重复回调，局部磁盘清理失败 | 成功清理保持幂等；清理失败不能回退业务成功，不新增业务写 |
| IR-06 | V1/V2、缺 observation、损坏、scope mismatch、仅 admitted checkpoint | 兼容和原有保护边界保持 |
| IR-07 | retryWaiting/organizing/failed/reviewReady 与新增安全码 | 真实响应解码及 UI 映射兼容，无新状态，无未知码导致错误成功或解码崩溃 |

复用现有 Controller 测试入口 `installLiveMemoryCaptureForTesting`、`installLiveMemoryRecoveryForTesting`，以及 `OwnerTruthContractsTests.swift` 中 B8-S01、B6、Live Capture 终态用例。不能只测试一个枚举辅助方法。

### 9.3 部件间接口证据

至少一组本地后端真实路由返回的 queued/retryWait/failed/reviewReady 响应，要交由 iOS 真实 BackendClient/FeatureGate/Coordinator 消费。可以使用本地 HTTP 或由同版本后端测试生成的响应夹具；必须可追溯生成命令和后端指纹，不能手写一个永远成功的 JSON 替代。

后端 Worker→Postgres→状态/候选/正式记忆链与 iOS Controller→磁盘→BackendClient 链分别使用真实组件，并以这组响应合同连接。若未运行跨语言单进程端到端，不得宣称运行过。

### 9.4 受影响回归：不得破坏已通过部分

| 实际改动 | 本地必须复验 | 用户之后主动发起真机时复验 |
|---|---|---|
| Live adapter/支持复核/Worker | BE 全部；既有 B7、文本/其他提取分类兼容、失败事务、消息投影不回滚候选 | 短/长场候选和正式记忆；混合事实与问题、纠正内容 |
| lease/attempt repository | 原 HTTP 重试、失租/取消、并发领取、重启预算、同源幂等及其他使用者回归 | 弱网/恢复作为独立扩展项目，不以本轮正常网络冒充通过 |
| Echo 终态清理 | IR 全部；已有短场成功、S01-08 旧 poll、账号迟到回调、70/48 原场排空组合 | 正常保存成功、失败后原场可核实、冷启动不重复业务写 |
| 共用 Echo 文件/构建 | 非 ASR 过滤、final 保持、停止排空、Coverage/partial、现有 Echo/音频回归与模拟器/通用 iOS 无签名构建 | 长回答自然播放、主动打断、恢复聆听、停止时 partial |
| 候选确认/正式记忆服务若被修改 | 权限/authority、幂等、来源/版本和正式读取全套相关回归 | 用户确认后正式记忆准确可查、重启不重复 |

测试使用受控回调、注入时钟和有界完成信号。禁止固定 sleep 等“碰巧成功”；逻辑 20 分钟与物理 20 分钟分别标记。

本地 PostgreSQL 必须是本机或已核实隔离容器内的一次性数据库，禁止把生产 DSN 交给 smoke 脚本。只能清理本次脚本创建且身份匹配的一次性库。可复用仓库 `backend-owner-truth-candidate-failure-postgres-smoke.py`、`backend-owner-truth-review-ready-confirmation-handoff-postgres-smoke.py` 的隔离设施；不能把脚本文本断言当作实际数据库执行证据。

## 10. 真机验收清单：仅用户另行主动发起后执行

本节目前全部为 `DEVICE_NOT_RUN`，不阻断本地交付。真实供应商、部署和设备版本必须在用户发起该阶段后核对；本地新代码未部署/未安装时不能拿旧环境证明新修复。

### 10.1 前置与保护

- 保留现有数据和登录；读取当前待确认/正式记忆基线，不能硬编码永远 46 条。
- 为本次短场和长场分别使用唯一、无隐私的测试标记；记录各自账号、场次、batch/source 的脱敏关联及 App/后端版本。
- 不清理历史任务，不重新提交旧失败场，不把其他场候选算入本场。
- 只确认用户本次明确选择的测试候选。开始真机测试并不自动授权批量审核历史候选、清理数据或重放失败 job。
- 出现本场保存/整理失败，保留证据并停止依赖该场成功的后续步骤；不继续制造更多场次。不能因此倒称本地开发未交付。

### 10.2 DEV-S：短对话进入待确认记忆，再成为正式记忆

1. 进行两轮短 Live：第一轮表达一条明确个人经历并带唯一标记；第二轮补充一个可核对细节。等待回答后手动停止。
2. 验证两轮用户文字均属于本场保存数据；end/ACK/admit 正确绑定，不重复提交。
3. 刷新待确认记忆，找到**本场新生成**候选；核对唯一标记、第二轮细节、事实准确及来源。仅气泡有文字、状态文案或总数增加都不够。
4. 在用户确认后，通过正式界面接受本场测试候选。核对审核结果和候选状态，不把“点过按钮”当成成功。
5. 打开正式记忆列表/详情，找到由该候选生成的正式记忆，核对内容、补充细节与来源。
6. 关闭并重启 App，分别刷新待确认和正式记忆：已接受候选不再次呈现为未处理；正式记忆仍可查；无重复候选、重复正式记忆或业务写重放。

**DEV-S-CANDIDATE、DEV-S-FORMAL、DEV-S-RESTART 三项分别记录。只有三项满足才标短场完整流程 PASS。**

### 10.3 DEV-L：物理 20 分钟长对话进入待确认记忆，再成为正式记忆

1. 实际持续至少 20 分钟，保持同一用户场次；分布在开头、中间、结尾的至少五项明确测试事实带可辨认标记，并包含后半段补充和一次明确纠正。测试开始前列出预期事实清单。
2. 可以混入纯问题和 AI 讨论作为 B7 保持性观察；预期记忆仅来自用户明确事实，不把 AI 回答或问句变为用户经历。
3. 手动停止后核对原场正文同步、封存及 end/ACK/admit。时长、登记水位、Source 输入与提取结果分别取证，不以“聊了 20 分钟”推定 TTL/401 已覆盖。
4. 在待确认记忆中核对本场首/中/尾事实、后半段补充和纠正结果。候选可以按语义合并/拆分，但不能漏掉预期事实、保留已撤回旧事实或混入 AI 推测。
5. 用户逐项选择并确认本场测试候选。到正式记忆列表/详情核对已确认事实、最后补充、纠正结果及来源，不能只验待确认列表。
6. 冷启动后重新核对正式记忆与候选状态：内容仍在、不重复、不回退，原场未重复 start/end/ACK/admit。

**DEV-L-CANDIDATE、DEV-L-FORMAL、DEV-L-RESTART 三项分别记录。只有三项满足才标长场完整流程 PASS。**

不能为了通过测试删减后半段、压缩事实或只检查第一个标记。若碰到本轮未修的真实输入上限，单独标 FAIL 并保留证据，不篡改为本轮通过。

### 10.4 展示和扩展故障场景单列

- 当前保存/整理提示与本场实际结果应核对；历史任务覆盖、核实按钮错场、轮询窗口若仍有问题，分别记录独立 FAIL，不能用候选/正式记忆 PASS 掩盖，也不能误说它们证明后台没有保存。
- 正常短场和长场完成后，按影响清单复验音频保持性、主动打断、partial。
- 自然 401/多次 TTL、断网恢复、未知写恢复分别记录 OBSERVED/PASS/FAIL/NOT_OBSERVED/NOT_RUN。未发生不能标通过。
- 失败分支真机验证只在用户明确发起相应测试时进行，不故意破坏生产配置或制造供应商错误。应验证失败文案真实、原场恢复依据保留、只读核实不重放业务。
- 受控 HTTP 的故障恢复验证、真实 Provider 调用、真机完整流程、历史数据重处理是不同验收层，不能相互替代。

## 11. Sol 开发顺序与交付门槛

1. 完成材料与当前 diff 核对，列出本轮实际文件边界和受影响测试。
2. 建立 D1 红测及真实 HTTP/数据库组合装配，保存修前证据。
3. 按 D2、D3 完成后台分类、合同处理、持久预算恢复与逐任务诊断；运行 BE 门禁。阶段性更新后继续开发，不在“加了日志/几个用例通过”处停止。
4. 按 D4 完成失败恢复记录保护，运行 IR 门禁。
5. 在最终源码上运行受影响回归、后端本地检查、模拟器及通用 iOS 无签名构建、diff 检查。失败继续本地修；新改动后的旧结果不能沿用为最终证据。
6. 交付报告与真机清单，记录全部状态；本地门禁完成后结束任务，不等待手机。

本地 PASS 必须同时满足：

- BE-01～BE-14、IR-01～IR-07 及适用受影响回归有实际可追溯证据。
- 红测反例与绿测断言对应，没有删断言、把失败改成跳过或扩大 mock 边界。
- 短/长合成场次完成候选与正式记忆链；持久预算、重启和幂等由真实数据库证明。
- 没有新增无限重试、未经确认的正式记忆、权限旁路、日志正文或未知写重放。
- 已知未修问题、历史触发点未知和真实 Provider/真机未执行均保留，不包装为全产品修复完成。

## 12. 交付物

建议输出到新目录：
`/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-candidate-contract-fix/run-01/`

若目录已有内容，保留并创建下一个 run，不覆盖证据。必须交付：

1. 本地修复报告：确定缺陷、实现、未能恢复的历史触发点、改动边界、剩余独立问题。
2. BE/IR 逐项清单：测试名、命令、环境、证据路径、真实结果和对应源码指纹。
3. 修前红测、修后绿测及最终回归记录；不引用之前 501/521 等数字替代本轮运行。
4. 固定失败 stage/reason/恢复资格表，预算交错的实际 HTTP 与 job_attempts 证据，敏感字段负向检查。
5. 短/长本地“待确认候选→确认→正式记忆→重建读取”的数据库及 API 证据。
6. 完整影响清单和第 10 节真机验收表，所有未执行项保持未执行。
7. 如准备真实供应商验证脚本，应默认关闭、只使用合成文本、不读用户 Vault、不保留原始响应，且在未获授权时不调用供应商；这只是交付准备，不作为已验证结果。

最终状态分别写：`LOCAL_PASS` 或 `LOCAL_INCOMPLETE`；`PROVIDER_NOT_RUN`；`DEVICE_NOT_RUN`；`DEPLOYMENT_NOT_RUN`；`HISTORICAL_REPROCESS_NOT_RUN`。旧现场具体触发点如果仍无法还原，明确写 `HISTORICAL_EXACT_TRIGGER_UNRESOLVED`，不影响满足明确本地门禁后的本地交付。

## 13. 可直接发送给 Sol 的执行提示词

请完整阅读并严格按以下文档，在现有工作区继续完成本地开发：

`/Users/gaominge/Documents/liftora/02-问题修复/记忆系统/长对话整理/2026-09-18-Astra-Live记忆候选整理失败-局部修复设计与Sol执行要求.md`

本轮主问题是“Live 正文/end/ACK/admit 已成功，但后台候选整理首次失败”，不要继续只改保存文案、增加轮询或推测火山故障。先按文档 D1～D3 修复后台真实整理链与持久预算内恢复，再按 D4 保留失败场恢复坐标；保留 B7、同步封存、认证恢复、音频及已有工作区修改。

必须完成真实适配器受控 HTTP、Worker/隔离 PostgreSQL、Controller/磁盘/BackendClient/FeatureGate 的相应组合测试，以及短场、长场候选确认后进入正式记忆的本地验证。不得用假 organizer 自证、删断言、放宽语义校验或重发未知业务写来转绿。历史具体失败检查点缺失应如实保留，不能编造旧模型输出；也不能因此只加日志就停止开发。

本次只做本地修复、测试、回归和交付。不要访问生产、部署、处理旧失败任务、清历史、审核真实用户候选或 commit/push。**真机由我之后主动发起，不是本地任务完成条件；不要检测、等待、安装或操作手机，更不能因手机未连接或真机未验证暂停任务。**

阶段性汇报后继续完成全部本地门禁。完成后交付实测报告、影响矩阵、源码/构建指纹和后续真机清单，标明真实 Provider/真机未执行，然后正常结束本次开发任务。真机清单必须分别覆盖短对话、物理 20 分钟长对话的“待确认记忆可见→用户确认→正式记忆可查→重启仍在且不重复”，不能仅验保存提示或候选数量。
