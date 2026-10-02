# DreamJourney B8：文字会话结束 notObserved 局部修复设计

日期：2026-09-14。问题：`B8-TEXT-END-NOT-OBSERVED-01`。现场状态：`FAIL`。本次交付为代码/证据核查与设计；未修改 iOS 或后端代码，未部署、访问生产、安装设备或 commit/push。实现、组合红绿与修复版真机验收均为 `NOT_RUN`。

## 1. 结论与范围

**当前最应定位的是正常文字关闭流程在 acknowledgement 之后、admission 准备之前为何停止。已证实有阶段与错误信息丢失的代码机制，但尚不能确认现场首次原因。** 不把本次认定为数据库丢任务，也不认定原 admit 一定未发送。

重要校正：现场摘要的 `roundFinished reason=notObserved` 是 iOS 恢复协调器输出的汇总原因。当前后端 status-v3 没有同名业务枚举；合法 `readyForAdmission`、`pendingAcknowledgement` 等情况都能被客户端记为该原因。原报告“业务结果为 notObserved”的表述应理解为客户端观察结果，不能当作原始 HTTP 响应字段。应补录已解码的状态元组，而不是为查找一个不存在的后端枚举扩大排障。

本方案优先限定在 iOS 正常关闭流程的阶段记录、未暴露请求的前置就绪处理、错误分类、原批次只读核实与页面展示。**目前没有证据要求后端部署；若隔离 PostgreSQL 证明提交/读取不一致，再按本文条件单独补后端局部设计。** B7 的候选语义问题不属于本文件；B6 只作为必须保留的只读恢复基线。

## 2. 输入、基线与证据分级

必读资料已核查：

- [B8 现场问题记录](../../../outputs/2026-09-14-dreamjourney-fm-b6-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-B8文字会话结束后状态notObserved-真机问题记录.md)、[现场截图](/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-fm-b6-device-retest/run-2026-09-14-01/evidence/screenshots/B8-text-session-not-observed.jpg)、[脱敏摘要](/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-fm-b6-device-retest/run-2026-09-14-01/evidence/sanitized-logs/2026-09-14-B7-B8-device-evidence-summary.log)。截图已实际查看。
- [原始第二组验收设计](../采集与会后保存/2026-09-10-Sol-Live第二组真机问题设计与修复指导.md)，尤其文字会话、状态合同和 B8。
- [B6 只读恢复设计](2026-09-14-Astra-B6会后任务冷启动只读恢复修复设计.md)、[run-02 最新交付报告](../../../outputs/2026-09-14-dreamjourney-b6-cold-start-read-recovery-fix/run-2026-09-14-02/2026-09-14-DreamJourney-B6五项遗漏补充修复-本地交付报告.md)。

| 基线 | 本次核实结果 |
|---|---|
| iOS 工程 | `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`；`feature/prd-stitch-ui-adaptation`；HEAD `11d0d0051b9be3cce57822dd059472d1e2536866` |
| 后端工程 | `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`；`main`；HEAD `a25b993922fc90dde1e689d19e51becb68fccdbf` |
| 现场安装包 | 记录为 iPhone 14 Pro Max / iOS 26.4.1；可执行文件 SHA-256 `f9b809bbc6ed10b5ed8e4481e58acb1ac29373eea0bda1136afd22db633a3d8b`；本次未重新提取安装包 |
| 工作树 | 两端均有前序未提交修改；HEAD 相同不证明全部脏文件已进入现场安装包或生产镜像 |
| B6 当前文件 | Echo、OwnerTruthContractsTests、B6 UIQA 脚本的 SHA-256 与 run-02 报告逐一一致：`332d2479…22ef`、`8175673e…49e6`、`3ab704d2…acc6` |

B6 run-02 报告有真实 BackendClient/FeatureGate/URLProtocol 的跨进程模拟器组合验证和 440/593 项测试记录；本次只核查报告及上述文件指纹，没有重跑这些测试。不能再把 run-01 的模拟客户端缺口当成当前未修事实，也不能把这些本地结果当作 B8 已通过。

### 2.1 已证实事实

| 编号 | 事实 | 证据边界 |
|---|---|---|
| E01 | 用户输入合成陈述“本次B7隔离测试的未审核代号是云帆九号。”；文字回答返回，随后用户结束 | 来自现场记录；不评价回答内容质量 |
| E02 | `live → saving → queued → unavailable`，ownerTurnCount 与 persistedOwnerTurnCount 均为 1 | 表示客户端收到并记下逐轮保存进度，不证明 admission 已接受 |
| E03 | 恢复计划 `hasBatch=true / phase=acknowledged / sourceCount=2` | `sourceCount` 是本地 checkpoint/outbox/follow-up 记录来源数量，**不是后端 Source 条数** |
| E04 | 原工作流 status GET HTTP 200、typed decode、resultValidated，随后 `reason=notObserved / actionRequired` | 查询和本地映射完成；缺少实际状态元组与首次 admission 失败 notice |
| E05 | 截图显示“当前无法继续整理，原对话已保留”，麦克风入口仍在 | 展示事实；不能据 UI 单独证明所有服务端内容均耐久保存 |
| E06 | 恢复阶段摘要只观察到只读查询，无额外 end/ack/admit | 现场观察范围内的保持性；不代替精确网络计数 |

### 2.2 本次独立本地验证

[独立状态探针](/Users/gaominge/Documents/liftora/outputs/2026-09-14-b7-b8-independent-design-audit/b8-status-builder-probe.py)调用真实 `OwnerTruthInterviewCandidateProposalStatusService` 与 status builder，使用内存库预置 acknowledged 批次，禁止网络。结果见[证据](/Users/gaominge/Documents/liftora/outputs/2026-09-14-b7-b8-independent-design-audit/b8-status-builder-result.json)：

```text
reviewBatch.state = acknowledged
candidateProposal.status = readyForAdmission
source.status = notAdmitted
candidateExtraction.status = notRequested
candidateExtraction.jobState = notCreated
candidateReview.status = notReady
查询前后 Source/effect 数均为 0/0
```

状态合同核查 `PASS`，不是修复绿测；没有实际 HTTP、iOS 或 PostgreSQL 事务。此结果证明该组合是合法返回，不能证明现场返回恰好就是它。

## 3. 当前真实调用路径和事务

### 3.1 文字关闭实际复用 capture 链路

[EchoViewController.swift:10537](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:10537)：`source == typed && ownerPrivate` 时调用 `beginLiveMemoryCaptureIfNeeded → captureLiveOwnerTurn`。该文字入口复用了会话 capture；不能按“它叫 Live”排除文字路径。

```text
文字输入被采纳
  → EchoLiveMemoryCaptureCoordinator.appendOwnerTurn
  → liveTurnOutbox.enqueue（原 productSessionID、messageID、commandID、序号）
  → NaturalInputUseCase.submitPersistedLiveTurn
  → BackendClient append（delivery.appendCommand 的 captureMode=live）
  → 合法 append 回执 → 本地 outbox acknowledge
用户结束文字会话
  → finishTypedEchoConversation → finishLiveMemoryCaptureIfNeeded → finish
  → 等待本地持久化及在途 turn，markClosing(lastClientSequenceNumber)
  → prepareEnd 落盘 → POST end → 验证回执 → acceptEnd 落盘
  → pending-review-batches GET 精确发现 → prepareAcknowledgement
  → POST acknowledgement → 验证回执 → acceptAcknowledgement 落盘
  → AdmissionUseCase.beginRequestOrFail（独立 CandidateReview 策略、lease）
  → 构造原 admission command → willSendCommand/prepareAdmission 落盘
  → BackendClient 再捕获 CandidateReview decision → POST admit
  → 验证回执 → acceptAdmission → follow-up 落盘 → 清理已可替代的旧坐标
  → 原 batch status GET → 页面观察
```

关键位置：[capture 的 end/ack/admit 链:1710](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:1710)、[admission 前置与落盘顺序:10702](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:10702)、[BackendClient admit:12189](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:12189)、[delivery 的 captureMode:9505](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:9505)。本轮不重新设计 captureMode 或文字回答路径。

### 3.2 恢复路径

`EchoLiveMemoryRecoveryService.resumePendingWorkflows` 合并原作用域的 journal，以 acknowledgementReceipt 取得 batch。`EchoLiveMemoryRecoveryCoordinator` 只经[真实 bounded BackendClient:9388](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:9388)查询原 batch；校验 lease、gate、generation、schema 与绑定后提交 UI。

[receive(status):2722](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2722)当前把 pendingAcknowledgement 记为 `actionRequired(.ended)/notObserved`，把 readyForAdmission 记为 `actionRequired(.acknowledgementPrepared)/notObserved`。后者的 phase 标签也会模糊“已 ack、未观察到 admit”的真实含义。[safeReason:2997](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:2997)还把 HTTP 404/409/410 合并为 notObserved；这是需要局部细分的错误分类缺口，不是这次 HTTP 200 的直接原因。

### 3.3 服务端事务与查询绑定

| 阶段 | 当前实现 | 持久边界 |
|---|---|---|
| end | [route:11160](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:11160)、[PG end:2425](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_conversation.py:2425) | 原 vault/thread/session、版本和连续序号校验；关闭会话、冻结 review boundary、原命令回执；不能等同创建 Source |
| ack | [route:11635](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:11635)、[PG ack:2775](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_conversation.py:2775) | 独立 UoW；校验 session/batch/CAS/authority，批次变 acknowledged，回执落库；不创建 Source |
| admit | [service:289](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_interview_candidate_proposal.py:289)、[PG prepare:931](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_interview_candidate_proposal.py:931) | 命令锁与 batch 锁；同一 UoW 中 Source、effect accept、admission 绑定记录一起提交；有 command hash、payload hash、授权证据、batch 版本和 Source hash |
| status | [PG read:1073](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_interview_candidate_proposal.py:1073) | 以 vault+batch 查询，联结 admission+Source，复核 owner、authority、Source version/hash/state；随后读取 extraction/job。没有查不到就推进写入的行为 |

[DatabaseUnitOfWork:117](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/db/uow.py:117)在退出时 commit，异常时 rollback；嵌套仓储共享根事务。[PostgresStore:283](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/postgres_store.py:283)提供此边界。静态上已有原子事务设计，不能凭客户端状态直接宣称事务分裂。提交时连接断开仍存在结果未知窗口，必须通过新的只读事务核实。

## 4. 已定位机制、假设与根因定位计划

已证实机制：AdmissionUseCase 的策略/lease 检查在 `willSendCommand` 之前。拒绝、构造/落盘异常与传输失败最终被 capture 合并为 unavailable；真实 notice 未被保存到该工作流的诊断链。页面把该状态当终态并移除 capture。[prepareAdmission:983](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:983)成功才会把 checkpoint 推进到 admissionPrepared；当前阶段单调。因此 `acknowledged` 显著缩小了定位窗口，但仍需证实现场文件、安装包和执行轨迹一致。

| 假设 | 优先级及证伪办法 |
|---|---|
| H01 ack 后 CandidateReview 策略暂不可用或 lease 在请求前失效 | 优先。记录真实 evaluator reason 与请求曝光状态；真实组合测试只在 ack 后使策略过期。若 prepareAdmission 已耐久完成或网络已曝光，则该次失败不能归到这个前置分支 |
| H02 admission command 构造、绑定校验或 checkpoint 原子写失败 | 优先。注入磁盘/版本/绑定错误；证明 hook 失败后网络业务写为 0，并保留旧 acknowledged 原字节。不能将全部异常归为 policy |
| H03 admit 实际已发出/提交，但日志或本地记录落后 | 待查。用原 workflow+command 脱敏关联、prepare 成功回读、request exposed、服务端提交观察与 read 元组联结。阶段不足不能排除旧包、记录不一致或并发覆盖 |
| H04 batch/命令/vault 绑定不一致，或服务端 status 联结/事务异常 | 待隔离 PG 验证。已存在原 batch 不等于命令身份全部匹配；以原记录关系和新事务读取证伪，不更换“最近 batch” |

定位顺序：冻结代码指纹及安装包 → 在隔离环境复现 typed → ack 后失败窗口 → 补齐原阶段/曝光/notice/状态元组 → 按 H01/H02/H03/H04 分流。若失败来自确定策略禁用，只做明确阻断，不以刷新次数或放宽权限“修好”。不连接生产补证，不复制私人会话。

## 5. 最小修改设计

### 5.1 保留两条正交状态轴

不以一个 unavailable 或 checkpoint phase 同时代表写结果与后台进度。

| 分类 | 允许的证据 | 行为 |
|---|---|---|
| notSent | 当前受控执行器证明请求尚未暴露给网络；如 preflight 拒绝、准备落盘失败 | 记录所处 end/ack/admit 阶段、白名单 reason；既有原命令保留。历史缺日志不能补标此状态 |
| outcomeUnknown | 请求已暴露，之后超时/取消/连接断开/5xx/无效回执，或历史曝光未知 | 只读原批次核实；不得自动重发写命令 |
| notObserved | 某次有界只读尚未取得目标阶段的证据 | 保存观察时间、状态元组和原坐标；不是 notSent，也不是业务失败 |
| conflict | schema/binding/hash/CAS/revision/authority 矛盾，或明确冲突业务码 | 精确 blocked；不伪装不存在、不改原版本后重试 |
| 已确认进度/合法终态 | 原绑定 status 合同：queued/running/retryWait、reviewReady、noCandidates、failed/quarantined/invalidated 等 | 原样映射；待确认不等于正式记忆；noCandidates 不单独证明最初无新事实 |

资源未见、待 acknowledgement、readyForAdmission、凭据/权限拒绝应有各自 observation reason；不能全部叫 notObserved。HTTP 409/410 按白名单业务码区分冲突/失效，未知码保守阻断或未知。200 但非法元组为 contractBlocked。

### 5.2 正常关闭流程的就绪与首次发送

1. 只修改 end/ack/admit 相关 UseCase、capture 和 BackendClient 接口所需的局部块。增加阶段化 outcome，保留底层安全原因；不改候选审核写入的通用策略。
2. 命令 ID/参数一旦准备就沿用原值；在网络曝光前确保已有 journal 能耐久恢复。可在现有 checkpoint 增加可选阶段诊断/曝光字段，不新建第二套业务工作流。不保存凭据、策略 token 或正文副本。
3. 对仍在原正常关闭执行器内、**已证明从未曝光**的请求，可在原结束意图有效、同作用域、当前合法 gate/lease 就绪后执行其首次发送。建议一轮最多 30 秒、一次必要策略刷新；这些是待测资源上限，不能成为固定等待修复。失败后不自动新建另一 command。
4. 这不是冷恢复写许可：离开流程后、重建 Controller、App 重启、曝光未知或收到模糊响应，统一进入已有只读恢复；核实按钮只发 GET。不存在“GET 未见所以补发”的分支。
5. 页面始终可返回；仅在新坐标耐久保存成功后才清理可替代旧记录。未知/冲突/保存失败不能删 checkpoint/outbox。旧回调必须核对原执行 generation、scope、命令及记录 revision；新 Live 使用新 productSessionID。
6. 区分本地保存与服务端回执：缺少 end 接受证据时不能无条件提示“服务器已保存”；合法 end 回执存在但 admission 未见，可显示“对话已保存，尚未确认开始整理”，并提供只读核实与返回。

batch status GET 证明的是该批次当前阶段，不是原 command 的精确回执。读到 admitted 可以更新只读业务观察，但不能伪造原 ack/admit receipt 或把历史 command 标成已核实接受。实施时还必须检查 requestJSON/认证适配器/Alamofire 的内部重试配置，确保“协调器不重发”之外，传输内部也没有对未知结果自动重放 POST；用网络边界实际计数证明。

### 5.3 必改与条件改位置

| 位置 | 范围 |
|---|---|
| EchoViewController.swift 的 completion checkpoint、receiveAcknowledgementState、begin/receiveAdmission、recovery receive/safeReason/render | 必改：阶段/原因与曝光保留、准确展示；保留 B6 的 store/registry/round fencing，仅必要局部扩展 |
| OwnerTruthContracts.swift 的 end/ack/admission UseCase 及窄 outcome 类型 | 必改：不能将 preflight、storage、transport/receipt 混成一个 requestFailed；不改 unrelated candidate decision/review |
| DreamJourneyBackendClient.swift 的对应三段关闭传输 | 必改：精确 preflight/task created/exposed/HTTP/decode 证据；不得添加全局 POST 自动重试 |
| OwnerTruthContractsTests.swift / B8 专用测试及受控网络脚本 | 新建本问题独立矩阵；复用真实 B6 组合基础设施但不改 B6 期望 |
| 后端 proposal status/事务 | 仅在 H04 有 PG 红例或旧合同无法提供必需证据时改；不提前动迁移、Worker 或候选提取 |

诊断至少包括 stage、commandPrepared、requestExposure、notice/reason、attempt、recordRevision、serverStatusTuple、receiptValidated、UI state 和 workflow/command/batch 的安全关联码。`taskCreated` 不等于发送，`taskResumed` 不保证服务器收到；提交成功与响应成功单独记录。不输出正文、token、原始业务 UUID、完整错误对象或请求体。

## 6. 先红后绿测试矩阵

下表是实施门禁设计，当前全部 `NOT_RUN`；“预期红”不等于本次已运行失败。已有 B8 status 探针只证明合法合同。

| ID | 场景/当前应暴露的缺陷 | 修复后必须断言 |
|---|---|---|
| B8-R01 | 真实 typed 入口，ack 回执后真实 gate expired；旧实现终态 unavailable | 等待态/原因准确；原执行器内恢复合法后只执行一次首次 admit；磁盘 batch/命令固定；UI 达到真实整理结果 |
| B8-R02 | prepareAdmission 文件写失败；旧实现丢失 storage 原因 | 原 acknowledged 文件保留，admit POST=0，notSent/storage 明确；不伪称准备已耐久完成 |
| B8-R03 | admit 暴露后服务端提交，响应丢失 | outcomeUnknown；只读查同 batch 最终 admitted/reviewReady；未知以后业务 POST=0；候选不重复 |
| B8-R04 | 真实 HTTP 200 readyForAdmission 与明确 409/410 | 前者显示该阶段；后者独立冲突/失效；不共用 notObserved 混淆 |
| B8-T05 | end 前缺序号、最后一轮尚未持久化 | 不提前 end/ack/admit；完整 watermark 成立后才首次推进 |
| B8-T06 | end 提交前后、回执落盘前分别崩溃 | 同一原会话坐标保留；重启不 end；无 batch 时仅走已有严格 discovery，零/多匹配准确阻断 |
| B8-T07 | ack 提交前后、回执落盘前分别崩溃 | 原 batch GET 分别显示 pendingAcknowledgement/readyForAdmission/更后态；恢复 ack/admit=0 |
| B8-T08 | admit 准备后未曝光、已曝光未提交、提交时连接断开 | 精确本地分类；恢复时均不补 POST；未提交场景可停待核实，不强造完成 |
| B8-T09 | admission 响应解码/绑定失败、迟到成功 | 不将解码失败当 notSent；原坐标不变；旧回调不能覆盖新读取 |
| B8-T10 | 重复 ended/ack 回执、重复生命周期触发 | 单调 phase、同命令 no-op；不同身份阻断；不重复 admit、不回 saving |
| B8-T11 | scope/vault/authority/账号切换、合法同主体凭据更新 | 不跨域、不放宽校验；只读刷新有界；旧作用域保留、隐藏 |
| B8-T12 | checkpoint/outbox/follow-up 一致/矛盾、损坏、V1/V2 兼容 | 一致合并，矛盾 fail closed；不覆盖损坏记录；缺曝光字段=unknown |
| B8-T13 | queued/running/retryWait/reviewReady/noCandidates/failed/quarantined | 真实后端 builder 生成所有元组；准确 UI，非法组合被拒绝 |
| B8-T14 | 网络断开、DNS/TLS、401、403、404、409、410、5xx及预算耗尽 | 分层原因、有界 GET、无读时写、无重试风暴、无永久 busy |
| B8-T15 | PG admission 在 Source 后/effect 后/admission 后/根 commit 故障 | 同事务全有或全无；未知 commit 用新连接 GET；没有孤立 Source/effect/admission |
| B8-T16 | PG 同 batch 并发、同 command 重放、异 command 冲突、版本变化 | 维持既有幂等与 CAS；正常一个逻辑 admission/Source/effect，不绕过授权证据 |
| B8-T17 | PG end/ack/status 及 status 提交前后快照 | 查原绑定；已提交后新事务可见；未提交不假可见；GET 前后业务表/回执/outbox 数不变 |
| B8-T18 | 第一进程正常 typed 关闭；第二进程仅磁盘恢复 | 两个 PID、真实 BackendClient+FeatureGate+UIKit；第二进程业务 POST=0；不是直接种成功 UI |
| B8-T19 | 旧任务核实时开始新 Live，页面销毁/重入 | 新旧 product/session/batch 隔离；原生 Live 无音频/快照绑定回归 |
| B8-T20 | 文字结束→新 Live；重启→新 Live | 文字入口能形成明确业务闭环；旧 Archive 不自动重试；不据麦克风可用单独判成功 |

## 7. 本地门禁与验收证据

1. 先保存既有脏差异、源文件指纹和锁定依赖；在隔离副本保存当前版本红例，不能重置用户工程。R01–R04 必须真实执行，用相同业务断言取得修复前后配对结果；编译错误不算业务红。
2. 组合必须使用真实 `EchoViewController` 生命周期、capture/恢复 coordinator、磁盘 store、FeatureGate evaluator、policy store、BackendClient、typed decode；只替换网络、时钟、独立目录及受控服务。第一阶段写计数与第二阶段只读恢复计数分开。不能以 stub admission 成功或直接赋 UI 替代。
3. PostgreSQL 使用隔离库、合成 vault、实际 migrations 和真实 UoW/仓储。若不可用，对应项 `NOT_RUN` 并写明阻塞，不转用生产或用内存探针冒充 PG。命令级 result lookup 如确实需要新增，只能作为只读、原身份绑定的后端合同另审；现有 pending inbox 不提供完整历史命令结论。
4. 局部矩阵通过后跑 OwnerTruth 相关回归、B6 既有保持性、正常文字/Live 音频保持性、模拟器/通用 iOS 设备构建。实际 workspace 为 `DreamJourney.xcworkspace`，设备 runtime 开跑前重新检查；不升级依赖。未跑真机保持 `NOT_RUN`。
5. 每例保存 stage/曝光/原坐标一致性、HTTP 与业务结果、磁盘前后、方法计数、PG 数量/约束、页面截图/状态、超时与迟到回调断言。只有总数或 HTTP 200 不算通过。全部本地必需项通过才可标 `B8_A_LOCAL_PASS / READY_FOR_B8_DEVICE_RETEST`。

## 8. 部署判断、兼容窗口与观察

**首选 iOS 局部修复，不需要后端部署。** 已有 status-v3 能分辨 acknowledged/readyForAdmission/admitted 等阶段；当前后端事务代码没有显示本问题必须修改的证据。若修复仅细分 iOS reason、就绪与首次曝光、磁盘和 UI，保持现有 v2/v3 typed status 兼容，不改变 API。

若 PG 红例证实 admission 的 UoW 原子性、status 绑定/可见性或返回合同存在缺陷，则后端部署变为必要；先明确哪条原子断言失败和最小改动文件，再补红绿、旧/新客户端兼容和部署清单。本次不执行此分支。不得顺带打包当前后端未提交的候选更正预览修改。

本地新增 journal 字段应向后兼容；旧文件没有 exposure 时视为 unknown，不能视为 notSent。迁移失败保留原文件。回退包仍须能读现有 V2/新增可选字段，并继续只读恢复。新客户端配旧服务端、旧客户端配新服务端均需明确测试；若增加新字段/枚举，不得让旧 strict decoder 因未知枚举失效，需版本化而不是宽松吞字段。

后续经授权观察：按入口区分的 ack→admissionPrepared 转化率、notSent 原因分布、outcomeUnknown 经只读收敛比例、readyForAdmission 滞留、绑定冲突数、未知后写计数（必须 0）、同 workflow 重复 admission/Source 数（必须 0）。以关联后的业务结果计数，不能拿“接口 200 比例”作成功率。

## 9. 真机复测步骤（当前 NOT_RUN）

仅在另行授权安装/联网验收后执行，不利用现场旧候选补测。

1. 记录安装包指纹和服务端版本，启用白名单日志；关闭 QA 旁路/故障注入。确认正常登录和两个所需策略均合法。
2. 新建文字会话，只输入一条新的合成事实，等待回答，然后结束。锁定该 workflow，观察 end/ack/admit 阶段、磁盘记录与真实 status 元组。
3. 对已成功 admission 的新场次，确认整理中→待确认或合法 noChange/确定失败；对本次新事实的正确候选数量和内容另行只读核对，不自动审核。
4. 返回并新开 Live，检查声音、持续聆听、打断与正式记忆快照绑定保持；旧整理不得覆盖新场次。
5. 使用另一个合成场次，在明确安排的关闭/排队时点终止 App 再重开；只凭磁盘查询原批次，恢复阶段业务写 0、无自动开麦。与 B6 保持性分别登记。
6. 断网后恢复只验证只读核实；强制 CAS/authority/服务器事务故障留在隔离测试，不人为损坏生产状态。出现未知或冲突立即保留原记录停止追加会话。

设备上必须完成“文字结束→状态与批次一致→安全返回→新 Live→重启后新 Live”的本问题闭环，才可关闭 B8。未自然出现的异常窗口不能标真机 PASS。

## 10. 停止点与局部回退

- 当前仅交设计与本地只读探针；代码实现、Swift 红绿、PG、设备、部署 `NOT_RUN`。现场 B8 保持 `FAIL`。
- 发现真实策略拒绝、作用域矛盾、存储不可读、无法区分曝光、需要新后端合同或安装包不一致时，保留证据并停止相关推进。不能通过放宽 FeatureGate/authority/CAS/hash/revision/Binding、替换 batch 或重发 POST 绕过。
- 回退只涉及 B8 新增的阶段记录/就绪/分类/展示代码块与对应测试。保留 B6 run-02 的只读 registry、磁盘扫描、round fencing 和新进程恢复能力；不能整文件回退。
- 暂停 B8 的首次发送就绪续接时，将原任务保留为待核实并继续允许只读查询；不得回退成冷启动写重放。已有未知任务不自动补写，不清理本地证据。
- 如条件分支修改后端，只回退对应 API/事务局部发布；新增字段保留兼容读，不倒退或删除业务数据。生产候选、正式记忆、历史和 Dead Letter 均不清理、不审核、不重放。
