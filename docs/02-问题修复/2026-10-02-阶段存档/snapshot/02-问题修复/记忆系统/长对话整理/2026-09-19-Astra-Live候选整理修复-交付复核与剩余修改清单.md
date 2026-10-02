# DreamJourney Live 候选整理修复：交付复核与剩余修改清单

日期：2026-09-19。用途：在现有修改上继续完成本地开发，不推翻已完成部分，不启动真机或生产操作。

## 1. 复核结论

**修改方向正确，但未达到原设计的本地完成标准。`A_LOCAL_INCOMPLETE` 判断正确；未完成原因不只是缺 PostgreSQL。**

本次复核发现一个已经离线复现的生产装配缺口：默认 Worker 使用外层 `SourceExtractor`，新增的恢复上下文却只传给直接注入的 Live 内层提取器，因此生产默认路径丢失固定修复反馈。当前绿色测试绕过了这一层。另外，原设计要求的响应负向校验、逐任务日志、数据库重建和 iOS 失败后冷启动验证尚未完整落实。

这不等于所有修改无效：typed failure、原 job 预算内恢复的主体，以及 iOS 失败保留坐标的实现应保留。优先补接线和真实验收，不通过再次改文案、延长轮询或扩大重试解决。

原两场历史失败的精确模型输出仍不可还原。本次新发现是当前代码与设计间的差距，**不能反推它们就是那两场现场的唯一原因**。

### 1.1 本轮复核依据

- [原设计与 Sol 执行要求](2026-09-18-Astra-Live记忆候选整理失败-局部修复设计与Sol执行要求.md)
- [Sol 本地修复报告](../../../outputs/2026-09-18-dreamjourney-live-candidate-contract-fix/run-01/reports/2026-09-19-DreamJourney-Live记忆候选整理失败-本地修复报告.md)
- [Sol BE/IR 执行矩阵](../../../outputs/2026-09-18-dreamjourney-live-candidate-contract-fix/run-01/reports/2026-09-19-BE-IR执行矩阵.md)
- 后端当前工作区：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`，HEAD `ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`。
- iOS 当前工作区：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`，HEAD `11d0d0051b9be3cce57822dd059472d1e2536866`。

两端都有未提交修改，HEAD 不能代表全部被审源码。继续开发前重新记录当前 diff 和关键文件哈希；行号仅辅助定位。

### 1.2 独立验证边界

本次读取了真实代码、报告和原设计，使用本地合成输入与 MockTransport 复现了后端缺口，并在隔离导出的旧 HEAD 复现了 7 项既有失败。没有修改产品代码，没有访问供应商、生产或手机。

没有重新运行 Sol 的全部 524 项 iOS 测试，也没有将其结果重新认证为覆盖全部 IR 断言。iOS 判断基于具体测试装配与断言核查；尝试读取 xcresult 明细受本地临时输出权限限制，不将未读取的细节写成已独立验证。

## 2. 已完成且应保留的部分

1. Live 专用错误类型和各阶段分类的主体、真实 HTTP transport 注入、原持久 attempt 预算内恢复策略已经存在。
2. iOS `finishSameSessionObservation` 已把失败/quarantine 与成功清理分开，失败时保留 Outbox、follow-up、checkpoint，且正常保留不标为 `cleanupPending`。
3. 已有 failed 用例确实经过 Capture、Controller 和三类磁盘记录；另一个组合确实使用真实 FeatureGate、BackendClient 与 Controller 发出只读 GET。它们是有效的局部证据，应补齐而不是删除。
4. 没有以真实 Provider 或手机未运行冒充通过；未部署、未处理历史数据，这些边界正确。
5. 7 项后端全量失败已在旧 HEAD 中独立复现相同 259/260 断言。它们不是本轮候选修复引入的回归，单独列为已知基线问题；不能据此宣称后端全量 PASS，也不必把路由修改混入本次候选修复。

路由基线证据：[route-baseline-review.md](../../../outputs/2026-09-19-live-candidate-delivery-review/run-01/evidence/route-baseline-review.md)。旧 HEAD enforce 启动检查实际为 260 条、未分类 0；旧测试/烟测数字仍为 259。

## 3. 必须继续完成的代码与验证

下列 C 编号仅为本次复核项，不取代原设计的 BE/IR 编号。

### C1：贯通生产默认提取器的恢复上下文（优先）

定位：[Worker 默认装配](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:988)、同文件 `ModelAssistedOwnerTruthSourceExtractor.extract` 约 859 行、`_extract_with_lease_heartbeat` 约 1278 行。

当前 Worker 默认装配 `ModelAssistedOwnerTruthSourceExtractor`，而 `_extract_with_lease_heartbeat` 只在提取器直接属于 `ModelAssistedOwnerTruthLiveConversationExtractor` 时传入 `retry_context`。外层 SourceExtractor 的 `extract` 也未接收和转发该参数。

独立复现：将已有 `contract → transient → success` 场景包回生产外层组件，实际请求缺少“上次安全合同反馈”，断言失败。**后续 attempt 仍可能执行；确定缺陷是恢复反馈未贯穿生产默认路径，不能夸大成所有重试完全不执行。**

修复要求：

- 在既有提取器接口和委托链上明确传递可选的 Live 恢复上下文，默认生产装配与测试装配走同一路径。
- 固定提示来自当前有效 lease 下的同 job 安全摘要；不传旧响应或任意异常文本，不改变 Source。
- 不以把生产默认提取器强行替换为 Live-only 实例的方式破坏普通文字、图片等分派。
- 红绿测试必须使用默认 Worker → SourceExtractor → LiveExtractor → 真实组织/支持适配器，仅替换 HTTP transport。直接注入内层 LiveExtractor 的原测试可保留，但不能继续作为唯一接线证据。
- 同时断言 contract→success、contract→transient→success、transient→contract、maxAttempts=1；反馈连续保留与预算资格是两回事，保留反馈不能增加资格或预算。

对应原设计 BE-04、05、07、08。

### C2：补齐真实响应与证据下标的严格校验

定位：[Live `_response_content`](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1221)、通用 `_extract_content` 约 299 行；[支持复核索引校验](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_memory_support.py:67)。

离线探针使用当前真实适配器，观察到：

| 输入 | 当前实际结果 | 必须达到的行为 |
|---|---|---|
| HTTP JSON 根为 `[]` | 未分类 AttributeError | 明确 envelope/schema 错误，不被误分类为内部暂态错误并进入通用重试 |
| `choices=[42]` | 未分类 AttributeError | 同上 |
| content 为含 JSON 字符串的列表 | 经 `str()` 后被接受为合法空结果 | 按 Live 正式合同拒绝错误类型；不无损证据不足地强转 |
| `finish_reason=length`，内容碰巧可解析 | 被接受为合法结果 | 明确输出截断，不提交不完整候选或空结果 |
| support 的 `turnIndex=true`、`supportingTurnIndices=[true]` | 被当整数 1 接受 | 在成员查找前拒绝 bool，严格校验证据整数与范围 |

这些多数是旧行为未按原设计补齐，不能都称为本轮新回归。Worker 对未分类异常的兜底暂态处理不能代替合同分类。

修复要求：

- 在 Live 专用边界验证 envelope、choice/message/content 类型与终止原因；避免改变其他 DeepSeek 适配器默认行为。
- 支持复核所有索引字段采用一致的非 bool 整数校验；覆盖 float、容器、缺失、越界、重复、助手证据等对应合同，不能依赖 Python `True == 1` 的集合成员语义。
- 不只用宽泛 `except Exception` 吞掉错误；为每个实际检查点给出固定、安全、可测试的分类和恢复资格。
- 每个负向场景检查候选数、实际 HTTP 次数、attempt 状态；合法兼容响应及 B7 语义规则必须继续通过。

对应原设计 BE-03、05、06、09、10。

### C3：完成逐任务日志与真实阶段诊断

定位：[Worker 循环日志去重](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1715)。当前仍按 `(status, reason)` 与上条结果比较；两个不同 job 同类失败时，后一条仍可能被省略。

当前执行矩阵将多 job 日志写为 NOT_RUN，但代码仍保留原去重行为，属于实现和验证均未完成。新增 failureStage 本身也不能证明真正经过哪些 HTTP/校验步骤。

修复要求：

- 有 job 的每个 attempt 必须独立输出安全结果；仅无任务的重复 idle/blocked 心跳可以合并。
- 按原设计 §6.3 记录实际输入构建、传输开始/返回、解码、组织校验、支持复核、候选提交等阶段和安全计数；不能根据最终错误反向补造阶段。
- 两个不同 job 同类失败、同 job 不同 attempt、日志接收器失败都要有测试。日志失败不得改变业务结果。
- 不记录正文、原始响应、自由异常字符串或凭据；脱敏关联须能区分场次。

对应原设计 BE-12，不是重新定义后的 BE-13。

### C4：限制 Live 修改范围，并使持久上下文校验一致

定位：Worker `_consume_current_lease` 约 1081、1116、1159 行；[内存上下文读取](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/lease_repository.py:329)、[PostgreSQL 上下文读取](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/lease_repository.py:750)。

当前共享 Worker 在未限定 Live 的路径上新增预算 guard，以及 sourceRead/candidateCommit 的 `candidateExtraction.live.*` 包装。普通文字、图片的失败分类也可能改变，不符合原设计 §6.2 的兼容边界。

另外，PostgreSQL reader 无匹配行时返回 None，不能区分“合法 lease 无上下文”和“lease 已失效”；内存实现先校验 lease 并抛失租。当前上下文查询扫描任意前序 retryable attempt，安全码正则只限制部分结构，尚未证明原设计要求的 attempt1 资格、连续合法历史和固定 reason 白名单。

修复要求：

- 将本轮 Live 特有分类及预算行为限定于经验证的 Live 路径；普通文字/图片对照用例保持原有行为。不要为恢复旧行为而移除原有通用安全保护。
- 内存与 PostgreSQL 对同样的失租、取消、换 owner、过期、无上下文情况给出一致控制流。失租不能被当作无反馈继续调用模型。
- stage/reason/资格须有固定映射，不把“符合正则”视为业务白名单。
- 原 job 的 attempt1 必须是合格合同失败；后续 transient 可保留这一反馈，但跳号、异 job、异身份或非法历史不能产生修复提示。所有检查在当前有效 lease 和不可变 intent 约束下完成。
- 使用真实数据库验证最后允许 attempt 在模型返回后、提交前退出，过期重领后零新增模型调用，并正确终结或读到原幂等完成结果。

对应原设计 BE-07、08、11 及非 Live 受影响回归。

### C5：补齐真正的数据库与正式记忆重建链

当前 BE-14 辅助函数位于 `tests/test_owner_truth_candidate_extraction_worker.py` 约 490–592 行。它把 Worker 结果转换后 seed 到另一个内存审核仓库；所谓 reopen 是新建 service，但复用同一个 repository 对象。正式读取主要核对数量/版本/来源数量，未充分核对第二轮细节及确切来源。

这些测试证明部分审核服务和幂等行为，不等于真实数据库提交、跨仓库读取、进程重建后持久存在。

继续完成：

1. 准备本机一次性 PostgreSQL 或已核实隔离容器；不使用生产 DSN。当前没有可用 PostgreSQL/容器运行时的记录属实，但不是手机依赖。
2. 使用同一真实数据库走 Worker 提交 → 状态/候选 API → 真实审核服务/API → 正式 Memory/Version，不手工 seed 下游仓库假接前一阶段。
3. 销毁并重新创建服务、仓库与连接，从数据库重新读取；断言短场第二轮补充、长场事实清单、纠正结果、Source/候选/正式版本的绑定及幂等。
4. 完成失败回滚、候选/receipt 原子提交、并发 lease、重启预算、失租和 Source/epoch 变化等原门禁。
5. 用例先准备好，再执行并保存命令、实际库环境和结果。环境暂缺时继续其他可完成本地项，不能把整个任务改成等待手机；也不能将此门禁豁免或以内存结果标 PASS。

本次复核未安装运行时、未启动数据库。下一轮 Sol 应在本地开发权限范围内处理该依赖；若确有无法自行解决的安装/权限错误，报告准确动作与错误，同时先完成所有不受影响项。

对应原设计 BE-01、02、08、11、13、14。

### C6：补齐失败后 iOS 真正冷启动和跨端合同验证

已确认 iOS 保留坐标实现方向正确，不要求回退。缺少的是以下关键验收：

- 当前新 recovery 组合约在 `OwnerTruthContractsTests.swift:8577`，从手工 seed 单个 follow-up 开始，不是 Capture 失败后完整销毁并恢复三类记录。
- failed Capture 测试约 8565 行用固定 `RunLoop ... 0.05` 等待后检查未删除，不能证明延后清理已全部结束。
- quarantined、失败 observation 写盘失败、`cleanupPending=false` 未有完整对应断言；已有成功 reviewReady 的 observation 写盘失败测试不能替代失败分支。
- 新 GET 响应来自 iOS 手写 helper，不是原设计 §9.3 要求的同版本后端真实路由夹具。

继续完成：

1. 真 Capture + reader 产生 failed/quarantined → 三类记录真实落盘 → 销毁 Controller、Coordinator、Registry、Store → 同目录重建 → fresh FeatureGate/BackendClient GET 原场，所有业务 POST 为零。
2. 失败观察写盘故障时检查原记录、真实失败文案和清理标记；不删除坐标、不回退为成功。
3. pendingReview/empty 成功清理、重复终态、已不存在文件和局部删除失败保持原行为；清理错误不能回退业务成功。
4. 页面离开、迟到 GET、账号/epoch 变化分别验证不串场、不污染新账号、不触发旧场清理。
5. 使用有界完成信号、受控调度和清理调用监测替换固定 sleep；必须知道待验证的生命周期已经完成。
6. 由同版本后端真实路由测试生成 queued/retryWait/failed/reviewReady 夹具，附生成命令和源码指纹，交由真实 iOS 解码/Controller 链消费。不能仅用手写成功 JSON 自证兼容。

对应原设计 IR-01～IR-07 和 §9.3，保留短场保存成功、S01-08、70/48、同账号恢复/换账号迟到回调、Coverage/partial、Echo/音频回归。

## 4. 修正验收矩阵与长短场表述

### 4.1 不得重定义原 BE/IR 编号

当前交付矩阵与原设计错位，例如原 BE-09 是 B7 纯问题，交付改成缺配置；原 BE-13 是同场状态/候选一致性，交付改成日志；原 IR-01 是 failed 保留，交付改成成功清理；原 IR-04 是离页/迟到/epoch，交付只列 524 项总数。

请恢复原设计 §9.1、§9.2 的原编号、原场景、原断言。每行列出具体测试方法、命令、隔离环境、证据、当前源码指纹；未满足的子项标 PARTIAL/NOT_RUN/BLOCKED，不能用总测试数替代。已有测试可复用，不强求为表格重复写等价测试。

### 4.2 恢复原真机清单，不降低标准

当前报告把短场两轮加补充改成一条事实、恰好一条候选；长场至少五项首中尾事实加补充/纠正改成三个标记。应直接恢复原设计 §10：

- 短场：两轮用户内容及第二轮细节 → 本场待确认候选 → 用户确认 → 正式记忆 → 冷启动仍在且不重复。
- 长场：物理至少 20 分钟、至少五项开头/中间/结尾事实、后半段补充、一次明确纠正 → 同样完整链路。
- 候选允许按语义合并或拆分，按事实准确性、完整性、证据及幂等验收；不要求每句话或标记机械对应一条记忆。
- 候选、正式记忆、重启分别记结果，不能只验保存文案或列表数量。

这些只需在本地交付中准备，**不会触发手机测试，也不作为等待手机的理由**。用户另行主动发起后，才核对部署/安装版本并执行。

### 4.3 长输入的已知限制单列

离线探针另证实：4,001 字单轮、36,000 字整场可以被 organization 分为两块，但整场 support 仍因原长度限制拒绝。这是原设计已单列的独立限制，不是两场短输入历史失败的已证实原因。

本轮不暗中改为新的分块复核架构，不抬高阈值、不截尾。报告应明确适用输入范围及该限制；31 个较短合成 turn 通过不能表述为所有 20 分钟真实内容必然通过。后续真机触发此限制时如实单列 FAIL，不能压缩测试事实制造通过。

## 5. 续修顺序、回归和交付

1. 先保留当前 diff，核对本文反例；优先 C1 和 C2，让默认生产装配及解析边界在相同断言下先红后绿。
2. 完成 C3、C4，并并行准备隔离 PostgreSQL；同 job 预算、失租和非 Live 兼容不得牺牲。
3. 完成 C5 的真实持久链与 C6 的真实失败冷启动链、后端生成跨端夹具。
4. 修正原编号矩阵和原真机清单，在最终源码上运行所有受影响本地回归与两种无签名构建。新改动后的旧测试结果不能直接当最终结果。
5. 所有新增失败继续本地修复。7 项旧路由基线另列，保留独立复现证据；不得偷偷改数字、跳过或把全量结果改 PASS。
6. 交付本地报告、红绿证据、数据库执行证据、矩阵、指纹、已知限制及后续真机清单，随后正常结束本地任务。

本地完成时可标 `LOCAL_PASS / PROVIDER_NOT_RUN / DEVICE_NOT_RUN`，但前提是原 BE/IR 实质断言及数据库门禁完成、受影响回归无新增失败；报告仍明确“后端全量存在 7 项已独立证实的既有基线失败”，不能写全量通过。若任何本轮实质门禁未完成，保持 `LOCAL_INCOMPLETE` 并指出具体缺口。

本轮不得部署、访问生产、处理历史 job、重放未知业务写、清理历史、审核真实用户候选或 commit/push。不得修改 Live 音频、既有同步封存/认证保护、B7、历史 UI 仲裁或轮询预算来规避本轮问题。触及受保护共用文件时，按原设计 §9.4 跑受影响回归。

## 6. 可直接发给 Sol 的续修提示词

请在现有修改上继续完成本地修复，完整阅读：

`/Users/gaominge/Documents/liftora/02-问题修复/记忆系统/长对话整理/2026-09-19-Astra-Live候选整理修复-交付复核与剩余修改清单.md`

并保留原设计作为验收标准：

`/Users/gaominge/Documents/liftora/02-问题修复/记忆系统/长对话整理/2026-09-18-Astra-Live记忆候选整理失败-局部修复设计与Sol执行要求.md`

当前并非只差 PostgreSQL。优先修复默认 Worker→SourceExtractor→LiveExtractor 丢失 retry_context 的实际接线缺口，再补真实响应/证据索引校验、逐 job 诊断、Live 作用范围和持久上下文校验。随后完成隔离 PostgreSQL 的候选→审核→正式记忆→重建读取，以及 iOS 失败落盘后销毁全部组件、同盘冷启动只读恢复和同版本后端响应夹具验证。使用相同断言先红后绿，不放宽 B7、预算、权限或未知写保护。

恢复原 BE/IR 编号和逐项断言，不用全量测试数字替代；保留已完成的 typed failure、原 job 恢复和 iOS 失败坐标保护。7 项路由失败已在旧 HEAD 独立复现，单列基线，不混入本轮业务修复。阶段性汇报后继续完成所有可执行本地项，不在几个定向用例转绿后提前停止。

本次只做本地开发、验证和交付。不部署、不访问生产、不处理历史、不 commit/push。**真机由我之后主动发起；不要检测、连接、安装或等待手机，不得因没有手机而暂停本地任务。**本地 PostgreSQL 是单独的本地验收依赖，不能拿内存仓库替代；如有真实环境阻塞，准确报告并先完成其他本地项。

交付恢复原标准的真机清单：短场两轮含补充、长场物理 20 分钟至少五项首中尾事实含后半补充与纠正，分别验证待确认候选、用户确认、正式记忆读取和冷启动幂等。准备清单不表示开始真机测试。本地全部门禁完成后正常结束任务，不等待外部验证。

## 7. 本次独立复现材料

- [后端离线探针脚本](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-candidate-delivery-review/run-01/evidence/backend_readonly_contract_probe.py)
- [后端离线探针结果](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-candidate-delivery-review/run-01/evidence/backend_readonly_contract_probe.txt)
- [7 项旧路由失败独立复核](../../../outputs/2026-09-19-live-candidate-delivery-review/run-01/evidence/route-baseline-review.md)
- [本次复核源码指纹](../../../outputs/2026-09-19-live-candidate-delivery-review/run-01/evidence/reviewed-source-fingerprints.md)

探针使用合成内容和受控 transport；没有重放用户历史现场，也没有请求真实供应商。探针是用于定位的最小证据，Sol 应将关键断言纳入仓库正式测试，并保留修前/修后结果。
