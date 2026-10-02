# DreamJourney Live run-02 独立复核与短场强制门禁

日期：2026-09-21\
复核对象：Sol `2026-09-21-dreamjourney-live-capture-lifecycle-fix/run-02`，以及当前 iOS/Backend 工作区。\
结论：**本轮有有效修复，但仍存在可定位的本地缺口；整体不能维持无条件 LOCAL_PASS，应为 LOCAL_REVIEW_CHANGES_REQUIRED。**

本文件补充[主开发指导](2026-09-21-DreamJourney-Live采集中断修复-开发与验收指导.md)及[run-01复核](2026-09-21-Astra-Live采集中断修复-run01复核与剩余修改要求.md)。只处理下列残余实现/验证缺口，保留已经通过的功能，不重新设计后端长场语义整理，不改 Live 音频或历史 UI 仲裁。

## 1. 本轮已核实的进展

1. 当前已列出的关键 iOS、测试与 Backend 源码指纹匹配 run-02 交付记录。独立读取 `ownertruth-full-final.xcresult` 确认为 554 total、553 PASS、1 SKIP、0 FAIL；SKIP 的 CAP-15 有独立双向执行证据。测试数量真实，但不能代替以下未覆盖的行为断言。
2. 可信 final 与 interim 分离、未绑定 QueryConfirmed 不再封存语音、两个 reply 交错缓存，已补代码和对应红绿测试。原 R01/R02 的语音反例及 R03 的两 reply 反例应保持，不重新打开这些已经修正的具体场景。
3. 冲突已进入完整关闭门禁；CAP-06 使用允许网络的策略并检查 end/ACK/admit 为零。overflow gap 已持久化并参与不完整判定。这些原 R04/R05-B 修复应保留。
4. CAP-15 已有真实 iOS BackendClient→本地 HTTP→Backend/PG 的双向链；实际后端响应回到原客户端，Python 重绑命令数为零。这比旧的单向导出回放有实质进展。
5. Echo/音频42项汇总已包含要求的相关类，后端受影响集合报告为192项；其中音频本轮目录只有汇总，未独立读取对应原始xcresult，不能把报告数量当成全部重新验真。后端非零epoch新分支已有有效断言，不继续要求重复修它。隔离PostgreSQL的候选、审核和正式记忆证据有价值，保留其真实作用范围。

本次不更改产品代码，不调用真实模型、不连接手机、不访问生产、不处理历史任务、不部署、不 commit/push。独立探针和本地短场复测的证据保存在[本轮复核目录](/Users/gaominge/Documents/liftora/outputs/2026-09-21-astra-live-capture-review/run-02)。

## 2. R2-01 / P1：旧 generation 在被拒绝前已污染当前助手缓存

位置：[DialogEngineManager.swift:2082](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:2082)。共享 raw dispatcher 先调用 `assistantState.consume` 改变缓存及 completed 集合，再调用 `router.freezeAssistant` 校验 generation。缓存键只有 replyID。生产 `enqueueProviderMessage` 在主线程 generation guard 前已执行冻结，因此后面的 guard 不能撤销此前变更。

独立生产组件探针输入：当前 generation 的 r1 ChatResponse → 旧 generation 的同 r1 ChatEnded → 当前 generation 的 r1 ChatEnded。旧事件在 router 被拒绝，但已将 r1 标记完成；当前合法结束也被拒绝，最后只留下 interim。该探针没有真实 Controller/磁盘，不能当成原现场发生了此时序的证明。

修复要求：在任何可变助手状态消费之前完成账号/场次/generation 的准入；缓存与终态身份应包含正确作用域。拒绝的旧事件必须零状态影响，不能只做到零最终投递。

红绿测试：用共享生产入口接真实 Controller/Coordinator/磁盘，制造上述顺序及正常对照；断言旧回调不改变当前缓冲、终态、正文、manifest 或网络命令，当前终稿完整封存，正常停止能完成。保留跨账号、跨场、同 replyID 对照。

## 3. R2-02 / P1：完成身份只保留32个，长场旧片段会重新开启已完成回复

位置：[DialogEngineManager.swift:1892](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1892)、[1964](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:1964)。`completedReplyIDs` 超过32后用 `Set.prefix(32)` 丢弃旧身份；集合截断不是稳定时序淘汰。

独立探针先正常完成40个 reply，每个由前段 delta、后段 delta、空 ChatEnded 构成；再重发旧 reply 原本的后段 delta 和空结束。被遗忘身份重新产生“仅后半段”的 complete。这会给下游相同 canonical identity 带来不同正文，具备触发 immutable conflict 的条件。**组件重开已复现；整条 Controller 链受到何种终态影响仍需指定红绿测试，不据此认定它就是原真机首因。**

修复要求：活动正文缓冲可有界；完成身份去重必须覆盖本场有效生命周期，或由可靠持久身份判定阻止旧片段重开。不能仅把32改成另一个未经完整性设计的任意数量。不能为放行长场删除冲突、重写已曝光正文或放松完整性校验。

红绿测试：40/100/150用户回合规模中混入已完成回复的迟到重复片段/终止；同场身份不重开、不生成第二份正文、不误制造冲突，真实短场和长场均完成关闭。另测合法新回复继续和必要容量边界。针对大规模验收先执行第8节短场门禁。

组件证据：[说明](../../../outputs/2026-09-21-astra-live-capture-review/run-02/evidence/README.md)、[探针源码](/Users/gaominge/Documents/liftora/outputs/2026-09-21-astra-live-capture-review/run-02/evidence/main.swift)、[结果](/Users/gaominge/Documents/liftora/outputs/2026-09-21-astra-live-capture-review/run-02/evidence/result.log)。被重新开启的具体身份与数量受 Set 顺序影响，判定依据是“存在错误重开”，不能把一次运行数量写成固定产品阈值。

## 4. R2-03 / P1：停止 manifest 的完成标记与正文持久化不原子

### A. 登记完成先于正文或争议落盘

位置：[OwnerTruthContracts.swift:16564](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:16564)。`registerCanonicalMember` 发现 manifest entry 就将 handoffID 写入 resolved；实际 `upsertCanonicalTurn` 在 Coordinator 中是随后的另一次调用。

最小窗口：同一 member 已有完整正文A → stop manifest 包含该 member 新观察B的 handoff → register 将 handoff 标完成 → B/争议写盘前崩溃。磁盘已有旧正文A且 manifest 显示完成，但新观察B尚无持久处置记录。该问题是“持久义务被提前消除”；不能未经验证断言冷启动一定自动发 end，恢复链还有其他保护。

### B. 已写完的 handoff 被迟到 manifest 重新列为待处理

位置：[OwnerTruthContracts.swift:17055](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:17055)。`requestClose` 首次写 manifest 时将 resolved 初始化为空。

最小窗口：正文写操作已经排入串行持久队列但暂未完成 → finish 快照仍包含该 handoff → 已排队正文先完成，当时磁盘尚无 manifest，因此没有记完成身份 → requestClose 随后把旧快照写入 manifest并置空 resolved。正常正文已在磁盘，handoff不会再次执行，清单却会永久等待它。这能导致普通短场停止也卡住，属于本轮必须优先保护的路径。

### 必须落实的修复与断言

采用一个明确、可恢复的原子交接合同：在相同提交中持久正文/partial/争议处置与该精确观察的完成事实；停止清单与已完成事实求差，不能靠“先登记了 member”或“同 canonicalID 有旧正文”认定新观察已排空。身份必须能区分同 member 多观察、来源与入口序号。

先红后绿分别控制真实磁盘写入的两个窗口，而不是只暂停 Controller 主线程 scheduler：

- A：已登记但新正文/issue未提交，销毁并重建后仍有未完成义务；不得完整关闭。
- B：事件写队列先完成、manifest后落盘，完成身份被准确识别；正常 end/ACK/admit 与本场候选成功，无重发业务写。
- 对照：确实未处理的同 member 观察、伪造身份、重复stop、磁盘不可写、已曝光命令保持不可变。

**本轮追加独立复现：两个窗口均在真实Store提取源码与临时磁盘重建中复现。** [证据及复现命令](../../../outputs/2026-09-21-astra-live-capture-review/run-02/evidence/manifest-probe/README.md)。A在登记后重建得到`pendingManifest=0/conflicts=0`，继续执行新正文的对照才产生`conflicts=1`；B在正文先写完、陈旧manifest后写入并重建后得到`pendingManifest=1/unsealed=1`，而正文存在且`deliveryCount=1`。正常manifest先于handoff的对照为`pendingManifest=0`。Store段保持原文，外围未调用依赖采用薄壳；这是Store/磁盘证据，真实Controller调度与网络关闭的组合红绿仍需按上文补齐。

## 5. R2-04 / P2：安全移交后的释放条件未贯通真实 Controller

[Coordinator:1522](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1522) 已允许部分非终态在 durable handoff 后释放，但[Controller:13413](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:13413) 仍要求 `state.isTerminal && canReleaseCaptureOwnership`。coverageGap/statusUnknown 不属于 isTerminal，因此仅测试属性为true不能证明实际 retained 字典会释放。

将安全移交完成信号接到实际所有权释放；未移交仍必须保留。用真实 Controller 断言 retained 移除、弱引用释放/观察器收尾、后续场可建立、旧回调无写，范围仅限采集所有权，不修改历史任务 UI 仲裁。

## 6. R2-05 / P2：诊断配额仍缺“活动首错对抗其他首错”保护

[ConversationMemoryManager.swift:1015](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/ConversationMemoryManager.swift:1015) 已将 critical 排在普通日志前，能保护首错免受普通音频 session 挤出；但尚无活动场 pin。配额为2，较早的活动场critical与两个较新的critical竞争，活动场仍可能淘汰。原测试只证明critical优先于普通session，不能概括为活动场始终受保护。

在有界规则中明确活动场首错的保留/移交生命周期，补critical对critical、结束后配额回收、重建与账号隔离对照；不要无界保留，也不要清理真实历史日志。本项是原R06范围内的闭环，不是新增日志产品。

## 7. R2-06 / P1验收缺口：双向链尚未覆盖150用户轮

run-02 CAP-15 的 Swift 实际运行两轮短场及九轮长场，服务器证据 `sourceTurnCounts=[4,18]`。它能证明双向连接和这一规模的语义场景；不能证明主指导要求的同一客户端150用户轮链。

另一组 PostgreSQL 的301条消息/150用户轮与149候选有后端压力价值，但它与这份2+9轮双向链不是同一条完整执行。不得把301条消息写成301个用户回合，也不能合并两份证据宣称完整CAP-15已通过。

补完实际150用户轮的客户端→真实后端响应→隔离PG→候选→审核→正式记忆→重建链，沿用事实真值表和 F-65 语义保护。重复、补充、纠正、撤回必须实际分布在不同整理批次和后半段，不能全部放在前8轮就声称跨批验证。只替换外部模型响应，不重绑命令、不播种Source。按第8节先运行独立短场内容门禁，再执行长场；若短场失败，长场入口不得运行。

另需校正R02报告：“只处理可绑定文字请求”当前没有对应正向测试证明。已有用例证明未绑定ack不影响语音。若当前产品不存在该Provider文字请求入口，明确写不适用并验证现有文字回响独立保存；若存在，则定位真实发送/登记入口补正向绑定测试。不要为了报告补造一个新产品入口。

## 8. 用户新增硬要求：每次长场之前先验证短场

用户明确要求：每次修复长场、每次测试长场前，先保证短场内容进入待确认记忆；非真机部分也要实际验证。该要求已写入主指导§12，后续执行时必须生效。

本地短场门禁采用两个用户回合：第一轮校园合唱经历，第二轮补充同一事件的代号松塔七号。通过条件为实际客户端输出经后端形成同场Source、一条同时含经历和补充的候选、正确来源绑定、审核后正式记忆与重建回查完整且无重复。UI文案或pendingReview本身不算通过。

原CAP-15将短场与长场放在同一个测试中，短场只等到pendingReview就接着跑长场；后台最终核对Source也不能替代长场开始前的候选内容门禁。因此必须拆分可单独运行的short入口，并让long runner在候选内容及既定正式链验证通过后才继续。

**Astra本轮独立短场复测：LOCAL_SHORT_GATE_PASS。** 使用临时测试VFS与服务器副本只选择短场并增强内容断言，业务源码不变；没有启动原脚本附带的九轮或150轮长场，没有调用真实模型。不把受控模型成功写成Provider通过。该通过只属于当前源码；Sol继续修改后必须重跑。

## 9. 本轮独立执行证据与状态

- 已执行生产组件探针，确认R2-01/R2-02输入序列下的错误行为；此证据是组件级，不冒充Controller或真机。
- 已执行真实Store提取源码及临时磁盘重建探针，复现R2-03两个持久化窗口，正常顺序对照通过。
- 已核对当前源码、实际xcresult、CAP-15双向服务器证据及相关测试断言。
- 已独立实跑短场完整链，主审再次读取实际xcresult：1/1 PASS、0 skip、0 fail。服务器正常退出，新建隔离PostgreSQL已停止，1613个既有产品源码指纹没有变化。

短场证据入口：[独立复跑报告](../../../outputs/2026-09-21-astra-live-capture-review/run-02/short-gate/README.md)。其中包括实际xcresult、测试/服务器副本差异、源码指纹、候选响应、Worker与正式记忆重建结果及执行命令。

| 本次实测环节 | 实际结果 |
|---|---|
| 输入与发送 | 2个用户回合、2个受控助手回合；真实BackendClient和localhost后端双向交互 |
| 本场Source | 1个Source，4条conversationTurns，第一轮与补充均保留 |
| 待确认候选 | 1条；主审直接检查候选`content.event`同时含“校园合唱演出”和“松塔七号”，不是只在证据原文中查到标记 |
| 候选归属/去重 | 绑定本场Source/sourceRefs；`content.sourceTurnIndices=[1,3]`；未拆成重复候选 |
| 审核与正式记忆 | 真实本地审核/激活执行；重建Store后仍1条，两项内容保留，Source正文回读相等 |
| 环境边界 | 外部模型HTTP受控2次；真实Provider、iPhone、长场、部署均未执行 |

因此当前版本的这条正常短场链可以标PASS，但整体仍有第2–7节残余问题，不能把短场通过扩大为全部LOCAL_PASS或真机通过。

复核不认定上述缺口已经唯一解释历史真机第13轮中断；历史首个触发点仍需保留未唯一确认的边界。它们是当前可定位且必须补完的实现与验收问题。

## 10. Sol执行顺序与交付

1. 保留run-01/run-02及有效实现，新建下一轮结果目录；记录当前基线与文件指纹。
2. 优先补R2-03两个原子交接窗口，再补R2-01/R2-02；按正确行为先红后绿，不改变预期去迎合现有实现。
3. 完成R2-04/R2-05原范围闭环和R02报告校正。每个修改列受影响模块及对应CAP/KEEP回归，保留B7、音频、分批语义、未知写、预算与账号隔离。
4. 落实独立短场门禁，实际运行并读取候选内容与补充、来源、正式记忆及重建结果。失败则继续本地修复；通过后再启动各长场组合与150用户轮双向链。
5. 完成适当范围回归、构建与指纹，逐项交付真实证据。没有待修本地缺陷且指定场景齐全后才恢复整体LOCAL_PASS。
6. 本地任务连续完成；手机未连接、Provider未调用、未部署不构成本地停工理由。后续手机短场、普通20分钟、密集20分钟、未来65分钟分别由用户主动发起，且每场长场前均先短场。

## 11. 可直接发给Sol的提示词

请继续当前DreamJourney Live任务，完整阅读本文件、2026-09-21主开发指导（特别是新增§12）以及run-02交付。保留已有有效修复和证据，不重写已经通过的后端长场语义整理。

Astra复核发现当前整体LOCAL_PASS仍不成立。请按本文件R2-01至R2-06连续完成剩余本地修复和验证，重点是旧generation先污染助手缓存、完成reply身份32项淘汰后被重复片段重开、停止manifest与正文提交的两个竞争窗口，以及真实Controller释放和活动首错配额闭环。用正确行为断言先红后绿，补150用户轮的真实双向客户端/后端/隔离PG链，不用2+9轮双向测试与独立大Source测试拼成完整通过。

新增硬要求：每次长场验收前，在同一最终源码及配置上先实际跑短场完整链。至少两轮，第二轮补充第一轮；读取本场待确认候选正文证明两轮内容与补充均存在、Source绑定正确且不重复，并完成隔离审核、正式记忆和重建回查。短场失败则长场不启动，继续本地修复后从短场重跑。把这个顺序和失败阻断落实到测试入口，不只写在报告。受影响的已通过模块必须列入回归并实际通过。

本轮连续完成本地实现、测试、构建和交付，不因未连手机停止。真实Provider、部署、手机安装和真机验收由我另行主动发起；不要检测或等待手机，不访问生产、不处理历史失败场、不重发未知写、不commit/push。最后按每个问题和短场门禁给出证据、源码指纹及所有NOT_RUN边界。
