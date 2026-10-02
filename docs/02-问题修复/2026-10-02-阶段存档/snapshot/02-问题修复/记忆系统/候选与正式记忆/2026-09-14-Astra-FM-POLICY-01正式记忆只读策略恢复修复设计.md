# DreamJourney FM-POLICY-01 正式记忆只读策略恢复修复设计

日期：2026-09-14。交付对象：Sol。性质：独立代码核查及局部修复设计，不是实现完成报告。

## 1. 先看结论与执行边界

**确认存在 iOS 读取恢复缺口，不是正式记忆没有写进去。**

1. 正式记忆列表在 FeatureGate 初始检查失败后直接返回，尚未进入网络请求；候选列表已有策略恢复，两条路径没有统一。
2. 正式记忆详情、人物记忆归纳入口同样存在这段直接失败逻辑。本轮一起补齐这三个入口，不能只修列表、留下详情或上级页面不可读。
3. 独立发现列表页面的另一个确定性错误：`isLoading == true` 也会触发 `failClosedForAccountChange()`。恢复过程中再次搜索/筛选可能被误当成账号变化。必须一并修复读取所有权。
4. 不能机械复制候选重试代码。现有 FeatureGate 会复用捕获的 route decision，刷新缓存并不必然替换它；还必须测试并实现本次只读请求的安全重新捕获。
5. 预计只修改 iOS，**不需要新增 API、数据库迁移、Worker 改动或后端重新部署**。已有 correctionBinding 与 decision-result 后端能力必须保留。

Sol 本轮先独立完成本地代码、失败反例、组合测试、模拟器和通用设备目标编译。未经另行授权，不部署、不安装 iPhone、不访问或操作生产业务数据、不 commit/push。真实复测留到本地完成后，由用户连接设备并逐步配合。

本次 Astra 仅阅读本地代码、差异和留存证据，并生成本文；没有修改 iOS/后端，没有运行应用测试，没有连接生产或真机。下面的新增测试当前全部为 `NOT_RUN`，不是已通过测试。

## 2. 核查基线与证据边界

### 2.1 必读资料

- [本次问题输入](../../../outputs/2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-正式记忆读取发布策略过期失败-Astra分析输入.md)
- [本次完整真机报告](../../../outputs/2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-B4更正预览与真机闭环复测报告.md)
- [正式记忆失败状态](/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/evidence/device/formal-memory-policy-expiry-failure.txt)
- [更正写入及派生状态](/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/evidence/device/correction-write-readonly-status.txt)
- [部署后检查](/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/evidence/device/deployment-postcheck.txt)
- [前序候选读取与审核设计](2026-09-12-Astra-B4连续审核假成功与候选读取恢复修复设计.md)
- [前序更正双重绑定设计](2026-09-13-Astra-B4更正预览200后失败与双重绑定修复设计.md)

生产状态以最新 9 月 14 日留存报告为准，不能照抄旧设计中的“接口尚未部署”。本次没有重新远程检查，以下镜像与现场状态是留存证据，不是现在重新测得的结果。

### 2.2 当前代码与已有未提交工作

| 项目 | 独立读取到的基线 |
|---|---|
| iOS | `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`；分支 `feature/prd-stitch-ui-adaptation`；HEAD `11d0d0051b9be3cce57822dd059472d1e2536866` |
| 后端 | `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`；分支 `main`；HEAD `a25b993922fc90dde1e689d19e51becb68fccdbf` |
| iOS 已有差异 | `OwnerTruthContracts.swift`、`MemoryArchiveViewController.swift`、`DreamJourneyBackendClient.swift`、`OwnerTruthContractsTests.swift`，另有 correction preview JSON 夹具 |
| 后端已有差异 | `app/main.py`、`app/services/owner_truth_candidate_review.py`、对应 API 测试，另有 correction preview PG smoke 与夹具导出脚本 |

后端差异主要是 `preview_changeset_result()` 和 `correctionBinding`；客户端适配器差异是 `previewOwnerTruthCandidateCorrectionChangeSet()`。这些是已完成的前序修复，不是本次新增成果，不能覆盖。正式列表/详情/归纳的初始 gate 尚未修复；实际读取路径见第 4 节。

### 2.3 证据强度

| 编号 | 可以确认的内容 | 不能据此声称的内容 |
|---|---|---|
| E01 | 留存现场：正式列表在客户端 FeatureGate 被拒绝，`http_request_started=false`、无 HTTP 状态 | 不能伪造正式 GET 的 HTTP 错误码 |
| E02 | 候选随后出现 `expiredPolicyCache`，策略 200 后候选 GET 200，attempt 2、UI ready 39 条 | 候选 trace 不是失败的正式列表 trace；不能把它移接为正式列表的直接日志 |
| E03 | 留存只读核验：更正已持久化，revision 69→70，MemoryVersion current，投影/搜索/embedding ready | 数量变化本身不等于持久审核证明；本次未重新查生产 |
| E04 | 后续正式记忆搜索、文字回查、新 Live 回查成功 | 不能替代“不经候选页预热的首次自动恢复”验收 |
| E05 | 代码：正式读取初始 gate 拒绝后未调用 `requestJSON`；无恢复分支 | 不代表该手机失败瞬间已保存了具体 deny reason |
| E06 | 代码：列表 `load(reset:)` 把正在加载与账号无效共用失败分支 | 这是独立潜在复现路径，不能说已在本轮真机触发 |

重要校正：输入报告把现场触发直接标为 `expiredPolicyCache`。现有正式列表记录只有通用 FeatureGate 失败；具体 `expiredPolicyCache` 来自随后候选读取。代码及前后行为强力支持策略过期解释，但原正式 trace 的精确子原因仍缺失。可以确认“缺少只读策略恢复”这一代码根因；不能把未捕获的正式列表 reason 写成原始日志。

已检查 `evidence/device/server-access-sanitized.log`，长度为 **0 字节**。没有把这个空文件当成服务端请求存在/不存在的独立证明。Sol 后续应保留安全阶段日志及受控网络计数，不能只交付人工整理摘要。

## 3. 根因、假设与排除项

### 3.1 代码已证实

**RC1：正式记忆只读适配器缺少初始/发送前策略恢复。** `fetchOwnerTruthFormalMemories`、`fetchOwnerTruthFormalMemory`、`fetchOwnerTruthPersonMemoryProfile` 都是同步 gate → 拒绝即 completion failure；只有允许才进入 `requestJSON`。后者的 401 恢复根本覆盖不到前面已经返回的请求。

**RC2：列表请求占用误分类。** `OwnerTruthFormalMemoryListViewController.load(reset:)` 的 guard 同时包含 `!isLoading`、租约和 vault 校验，任一不满足都调用 `failClosedForAccountChange()`。该函数递增 generation、清空内容/游标、禁用按钮；之前真实请求晚到又被 generation 丢弃。这不是正确的“单飞合并”。

**RC3：读取诊断与所有权没有贯穿。** 正式读取 protocol 仅传 vault/query 和普通 Result；页面的租约没有作为固定入参传到 BackendClient，也没有 read intent、最终 attempt、整体期限和可取消句柄。`requestJSON` 仍会捕获并验证租约，所以不能把它描述成“没有认证”；缺口是页面到网络没有同一个不可变读取上下文。

### 3.2 必须验证的恢复边界

- **旧 route decision 未替换**：`FeatureGateService.requestDecision()` 会按同一 accountGeneration 复用 `routeDecisions[feature]`；`FeatureGateEvaluator.revalidateForRequest()` 对原已拒绝 decision 直接拒绝，对原过期 decision 返回 `capturedPolicyExpired`。`ReleasePolicyStore.save()` 只保存缓存，不替换 route decision。必须测“旧 route 存在，策略刷新成功后仍继续同一次读取”，不能只用每次都 fresh capture 的测试替身。代码证明该组合有风险，尚无本次真机触发证据。
- **复用候选单飞的边界**：现有 `refreshCandidateInboxPolicy` 有队列、scopeKey、waiters、超时完成门；但没有逐等待者取消句柄，其底层诊断/预算属于发起者。不能直接给多个页面共用并假设每个等待者的 deadline、trace、取消均正确。
- 原正式失败的子原因究竟是 `expiredPolicyCache`、`capturedPolicyExpired` 还是 `policyVersionChanged`，需要新增 reason 或隔离反例进一步定位。
- 多页读取不是数据库快照：正式列表响应只有 schema/vault/memories/nextCursor，没有全局 memoryRevision。不能虚构一个 revision 参数宣称已实现跨页快照一致性。

### 3.3 本次不成立的推断

- 没有证据要求重新审核、更正或补写已成功的记忆。不得建议普通接受原内容绕过问题。
- 正式列表错误不是 pgvector/BGE-M3 未检索到、DeepSeek 不理解、火山没采纳正式记忆造成的。
- 不能因时间相邻就认定登录轮换或 F3 历史 checkpoint 为此次原因。
- 不能用延长 TTL、强制常开 gate、进入候选页预热、循环刷新或展示缓存假成功代替修复。

## 4. 实际调用链和修改定位

### 4.1 当前正式读取

```text
OwnerTruthFormalMemoryListViewController.viewDidLoad / refreshTapped / updateSearchResults
  -> load(reset:)：页面 lease、query、generation、isLoading
  -> OwnerTruthFormalMemoryClient.fetchOwnerTruthFormalMemories
  -> DreamJourneyBackendClient.requestFeatureDecision(.ownerTruthCandidateReview)
  -> FeatureGateService.requestServerPolicyManagedDecision -> requestDecision
  -> FeatureGateEvaluator + ReleasePolicyStore
     拒绝：featurePolicyDenied -> 页面错误，网络根本未开始
     允许：requestJSON
        -> privateUI / authentication / BackendAccountLease / applicationLease
        -> RecoveryRuntimePolicyStore（必要时 runtime config 恢复）
        -> featureRevalidated -> taskCreated/resumed -> HTTP
        -> deliverRequestResult lease 校验 -> typed decode -> UIKit generation/lease 校验
```

后端正确链：`app/main.py:list_owner_truth_formal_memories()` → `_owner_truth_formal_memory_context()` → `_owner_truth_captured_release_policy_context()` → `OwnerTruthFormalMemoryService.list()` → `list_current()`。读取当前正式版本，不靠向量召回；本次没有证据要求改变查询服务。

### 4.2 现有候选参照链

`fetchOwnerTruthCandidateInboxRead()` 创建 context/budget/completion gate → private `fetchOwnerTruthCandidateInbox()` 识别策略原因 → `refreshCandidateInboxPolicy()` → `fetchReleasePolicyForCandidateInbox()` 真 HTTP、解码、scope/revision 校验、保存 → 再读候选 → typed outcome → UseCase/UI。

已有 30 秒 intent 默认期限、一次逻辑策略刷新、一次认证恢复、最多两次候选 GET 预算。保留其语义与已有回归，不把私有类型整套复制成 FormalMemory 版本。

### 4.3 文件与函数清单

以下行号是本次工作树定位，实施时以函数名重新查找。

| 文件 | 位置/函数 | 本次动作 |
|---|---|---|
| [BackendClient](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:8978) | `fetchOwnerTruthFormalMemories` 8978；detail 9031；profile 8939 附近 | 三个正式读入口接统一只读 context、预算、恢复、typed outcome |
| 同文件 | `CandidateInboxReadAttemptState` 6894；completion gate 6969；refreshPolicy 8665；policy fetch 7547 | 小范围提取共享只读基础设施；候选业务层保持原行为 |
| 同文件 | `FeatureGateService.requestDecision` 499；`requestServerPolicyManagedDecision` 570；`requestFeatureDecision` 7322 | 新增严格限于本次只读 intent 的 fresh request capture，不能改写通用 gate 允许条件 |
| 同文件 | `requestJSON` 14874；初始重验 15100 附近；401 15360 附近 | 支持 opt-in 只读上下文，取消/期限/预算贯穿；不全局改变请求重试 |
| [正式记忆合同](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthFormalMemory.swift:862) | `OwnerTruthFormalMemoryClient`、`OwnerTruthPersonMemoryProfileClient`；Query/Page | 明确页面 lease 和只读 outcome；保留 schema/typed/重复 ID/游标校验 |
| [正式记忆页面](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Archive/OwnerTruthFormalMemoryViewControllers.swift:690) | List `load(reset:)`、`refreshTapped`、`updateSearchResults`、`failClosedForAccountChange`；Detail `load()` 1016；Profile `load()` 143 | 独立读 intent 所有权、单飞/替代、取消、安全错误展示、活动期恢复 |
| [候选合同](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:4263) | CandidateInboxReadContext/Outcome 及其预算接入 | 仅共享基础类型所必需的改动；不能重写审核状态机 |
| [策略存储与 evaluator](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/ReleasePolicyStore.swift:373) | `capture`、`revalidateForRequest`、`save/evaluate` | 保留语义；只按测试需要增加时钟/依赖注入，不降低校验 |
| [已有测试](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:1491) | 真实 policy HTTP/UIKit 组合；timeout/singleflight 1713/1810 | 复用真实网络夹具；新增正式读取组合类，保留前序测试 |

可以新增窄文件 `OwnerTruthReadRecovery.swift` 与 `OwnerTruthFormalMemoryReadTests.swift`，不得顺便拆分整个巨型 BackendClient/OwnerTruthContracts。名称可沿用仓库等价实现，行为合同不能省略。

### 4.4 相邻只读入口盘点与本轮范围

| 入口 | 代码现状 | 本轮处置 |
|---|---|---|
| 正式记忆列表/搜索/分页、详情 | 初始拒绝直接失败 | 必修 |
| 人物记忆归纳 `memory-profile` | 同类 gate；是“人生记录→记忆记录”的上级入口 | 必修 |
| 候选 inbox | 已有恢复 | 共享基础设施及回归，不能退化 |
| `/memories/{id}/versions` 独立版本历史 | BackendClient 8889 附近同类直接失败；HistoryUseCase 19107 还有布尔前置 gate | 记录为相邻缺口，不自动扩展到本次历史审核系统；详情内已有最近版本展示保留 |
| 审核历史 `candidate-review-history` | BackendClient 8841 附近同类路径，HistoryUseCase 18955 也可能先截断 | 记录后续专项，不声称已覆盖 |
| source-records 列表/详情 | BackendClient 9119/9165 附近同类路径；列表也有 busy/账号共用 guard | 记录后续专项，不修改素材/历史入口 |

本轮验收名称必须是“三个正式记忆只读入口修复”，不能写“所有记忆档案读取已修复”。相邻缺口不是新增算法/数据库故障，未来可采用本次共享能力，但需补自己的调用端和测试，不能只改适配器就标通过。

## 5. 目标合同：小范围共享，只读恢复与审核授权分离

### 5.1 不可变读取意图

建议 `OwnerTruthReadIntent`、`OwnerTruthReadOutcome<Value>`、`OwnerTruthReadHandle`，包含以下语义：

- `intentID/traceID`：随机生成，一次读取意图不变；另有 `pageGeneration`。
- `accountLease`：固定 subject/vault/lifecycle generation/generationId/authorityEpoch；每次异步边界验证，不能从另一个新账号偷偷重建原 intent。
- `resource`：封闭 enum，仅 `formalList(query)`、`formalDetail(memoryID)`、`personProfile`、已接入的 `candidateInbox`。不是任意 URL/HTTP method 的自动重试器。
- 冻结规范化 query、facet、kind、cursor、limit；这些值用于内存比较，不写日志。
- 共享 `attemptState`：当前 attempt、逻辑恢复预算、实际请求计数、绝对期限、active/terminal 状态；所有递归/恢复持有同一对象。
- outcome 带最终有效 attempt、typed result 与失败 stage/reason；UIKit 不自行猜测最终 attempt。
- handle 可取消当前消费者，并保证一次 completion；取消只解除自己的占用和策略订阅。

旧 protocol 便捷签名如需保留，必须转调新实现，不能保留第二条生产旧路径。三个真实页面都要显式传入其持有的 lease；QA 客户端和其他 conformer 一并更新。不要给默认实现返回固定成功掩盖漏接。

### 5.2 新鲜策略与新鲜 request decision 是两件事

执行顺序：验证 lease/vault → 现有 gate → 根据严格原因分类 → 只读恢复 → 重新捕获当前 request decision → `requestJSON` 再验证 → 发 GET。

| 条件 | 处理 |
|---|---|
| 当前策略、decision 均有效 | 立即读取；不预刷新策略、不额外 auth |
| `expiredPolicyCache/capturedPolicyExpired/policyVersionChanged`，当前 cache 已有同主体合法有效 successor | 使用该 cache 做一次 **新的请求捕获**，不再下载同一策略；记录恢复 attempt |
| 同上，但没有可用 successor cache，原 lease 合法 | 加入/发起一次 policy refresh；校验成功后 fresh request capture，继续同一个读取 intent |
| 策略刷新 200 但仍过期、schema/audience/cohort/revision/emergency 不合法 | 失败；不发正式 GET、不二次循环刷新 |
| 功能明确关闭、产品关闭、身份/范围不符、升级要求、未知原因 | fail-closed；不能按缓存过期自动放行 |
| 认证合法 successor、同生命周期 lease 仍有效 | 使用现有 CAS/successor 验证，在共享预算内更新本次读的 request decision |
| 主体/vault/lifecycle generation/authorityEpoch 改变 | 终止旧 intent、清屏/撤销动作；不得自动查询新主体来完成旧 intent |

新增窄 `capture...ReadRequestDecision` 应调用同一真实 evaluator/currentPolicy，并保持 server-managed feature 和 product-closed 规则。它为本次已核验的只读 intent 获取新的 `purpose=request` 决策，**不清空全局 routeDecisions、不使旧审核闭包重新有效、不把旧 decision.allowed 改成 true**。原 request 发送前仍用 `revalidateServerPolicyManagedRequest` 检查同一已捕获 decision。

必须覆盖“路由先以过期状态捕获为 denied”和“路由原允许但后来到期”两种。当前部分候选测试注入的是每次从新缓存直接 `evaluator.capture` 的闭包，它不能证明生产 route cache 这两种情形正确。

只读刷新成功不构成写授权。旧 Candidate、Proposal、Binding、correction preview、关联组选择、正式记忆编辑基线和发布选择，都不能因此被自动重新确认。保留现有不可变 Binding 和写前 gate/CAS 校验；受影响编辑/发布动作恢复前应重新读取并由用户重新确认。已经进入 unknown 的 command/binding 必须原样保留，仍只能查询，不能清除或自动补发。

### 5.3 统一预算与 deadline

- 沿用 intent 默认 30 秒；策略单飞自身最长 15 秒。使用可注入时钟测试，整体期限不因 retry/页面显示/单飞加入重置。策略有效期继续使用真实绝对时间；本地超时可用单调时钟，不能修改服务器 TTL。
- 每 intent：逻辑策略恢复最多 1 次、认证恢复最多 1 次、runtime recovery refresh 最多 1 次；正式资源 GET 实际发起最多 2 次。
- 401 重发要消耗共享认证/重放预算；不能每个递归 `requestJSON` 重新获得 `allowsRefresh=true`。policy GET 或 runtime GET 如因同一次 auth 恢复重发，同样计入物理请求计数。
- 区分“调用了适配器/重验 gate”和“实际创建任务”。被 preflight 拒绝不计为已发送；但逻辑恢复预算必须消耗，不能无限反复过期。
- 本轮最多一次自动 HTTP 401 恢复重放，覆盖 policy/runtime/resource 全链，不能三个端点各刷新一次。`requestJSON` 目前按 `/candidates` 后缀扣业务 GET 预算，必须改成 opt-in 的明确 resource 语义，查询串不影响匹配。
- `RecoveryRuntimePolicyStore` 仍可拒绝读取；其 runtime fetch 也纳入相同 intent 期限、trace 和单飞/预算。不得在 global config/其他业务请求上改变默认重试语义。
- 超时/取消后不仅丢弃完成回调，还要在恢复回调、任务创建、解码/UI 前检查 active/deadline，避免结束后继续发 GET。底层可取消就取消，但正确性不能依赖取消一定成功。

### 5.4 策略单飞：共享任务不等于共享页面所有权

从现有 `refreshCandidateInboxPolicy` 提取只读策略 broker，不新建第二套 FormalMemory 专属规则。候选仍经过其原 UseCase/typed decoder。

- key 至少包含原账号/vault/lifecycle generation+generationId/authorityEpoch、经验证的凭据作用域、build、audience/cohort；跨主体或未经验证的 session 绝不合并。以比需求更严格的精确作用域合并为安全默认。
- 每个 flight 有随机 `flightID`，每个等待者有 `waiterID`、自己的 trace/attempt、deadline、取消状态。底层 policy HTTP 可以只有一个发起者 trace；每个等待者日志通过 `flightID` 关联，不能伪造多次实际 HTTP。
- 取消一个页面只移除自己的 waiter，不取消另一个页面需要的策略任务。最后一个 waiter 离开或 flight 超时后再结束该 flight。
- 删除 waiters/释放占用必须同时匹配 scopeKey 与 flightID。旧请求超时后启动新 flight，旧回调不能删除新 flight 或给新等待者派发。
- 保留缓存 scope/hash/schema/revision/emergency 防回退校验；至少在缓存采用前校验 flight 有效性和租约。已结束 flight 的响应不恢复任何旧 intent，不允许通过写 cache 间接复活旧页面。
- 合法刷新引起的审核授权失效，与当前只读 intent 的生命周期分开；不能清掉读 generation 使自己刚恢复的 GET 无法提交。

## 6. 页面状态机与竞态规则

```text
idle -> validating -> [recoveringPolicy / recoveringAuth / recoveringRuntime]
     -> requesting -> decoding -> ready 或 empty
任意 active -> failed(reason) / cancelled / superseded / accountInvalidated
所有终态只收尾一次；旧回调不能重新进入 active/ready。
```

1. 首先独立验证租约；只有实际不合法才执行账号 fail-closed。`isLoading` 不得作为账号错误依据。
2. 同一页面、相同规范化查询的重复刷新/回前台事件，合并到现有 active intent，不重置 deadline，不创建新策略任务。不处于 active 时，手动刷新是新 trace/新 intent。
3. 查询、facet、kind 改变：立即废弃旧 request ownership；原 0.35 秒搜索 debounce 可保留，只执行最后一个查询。新的 query 不能拿旧 cursor；在 debounce 等待期间旧回调也不能更新成新查询的结果。
4. 加载下一页固定同一 query 和 cursor；同 cursor 只追加一次。全量刷新取代分页；旧分页晚到不能追加到新首屏。当前 API 不提供跨页 revision 快照，本轮不杜撰；发生权威更新事件时丢弃游标重新从第一页读取。
5. 失败/超时一定释放本 intent 的占用。只能由当前 intent 清理其 `isLoading`/handle；旧 attempt 不得释放新 intent。错误明确区分策略不可用、网络离线、超时、服务端拒绝、合同无效和账号变化。
6. 无结果前显示“正在读取正式记忆…”或“正在更新访问状态…”，不是“保存失败”。只有成功 GET + typed decode + lease/intent 校验后才可显示 empty。失败不能解释成“没有正式记忆”。
7. 导航离开时取消或解绑当前消费者；回到页面需要按明确策略创建新读取，不能同时由 viewDidLoad/viewWillAppear/foreground 回调开三份。后台不重置 deadline；恢复活跃时一次判定超时/继续，不能重复刷新。
8. 账号失效先撤销全部显示/选择/确认权限，再忽略旧回调；不能仅 `return` 而让加载按钮永远禁用。列表、详情、归纳均需要生命周期测试。
9. 正式详情在旧数据失效或刷新失败后不得继续用旧 edit action 默认为当前版本；发布选择同理。此处只撤销/重新取得页面动作依据，不改审核或发布业务流程。

## 7. 日志设计

复用现有 `PrivacySafeDiagnostics`、URLSession task lifecycle 观测；增加明确 subsystem `OwnerTruthRead` 与 resource 类型，不能把正式 GET 的日志仍全部标成 CandidateInbox。

允许记录：随机 trace/intent/flight、attempt、operation generation 数字、endpoint 枚举、stage、白名单 reason、HTTP 状态、计数、耗时、是否创建/恢复任务、是否丢弃旧回调。源代码/构建 SHA 可用于构建证据；业务原始 hash 不可记录。

禁止记录：搜索词、facet 值、cursor、正文/转写/Prompt、业务 ID、原始 Candidate/Proposal/Binding/hash、token、cookie、请求头、完整 URL/响应、密钥、未过滤 `localizedDescription`。`safeCode()` 的字符清洗不等于白名单，reason 必须枚举或固定映射。

建议事件：`readIntentCreated`、`leaseChecked`、`requestDenied`、`policyRefreshStarted/Joined/Completed/Failed`、`readDecisionRecaptured`、`authRecoveryStarted/Completed`、`runtimeRecoveryStarted/Completed`、`taskCreated`、`taskResumed`、`transportCompleted`、`responseReceived`、`typedDecoded`、`uiCommitted`、`resultDiscarded`、`readIntentFinished`。

白名单原因至少包括：`expiredPolicyCache`、`capturedPolicyExpired`、`policyVersionChanged`、`freshSuccessorAdopted`、`policyRefreshFailed`、`policyRefreshTimeout`、`policyStillUnavailable`、`featureDisabled`、`scopeMismatch`、`accountScopeChanged`、`credentialSuperseded`、`recoveryRestricted`、`readDeadlineExceeded`、`readBudgetExhausted`、`supersededQuery`、`cancelled`、`lateCallback`、`offline`、`contractMismatch`、`unknownDeniedReason`。HTTP 原因按固定状态映射，不能拼业务错误正文。

attempt 表示一次读取尝试/恢复代次，不必为每个阶段加一。例：intent attempt1 在初始 gate 过期；attempt2 完成 policy fetch 和正式 GET；若合法 401 恢复则 attempt3。同一 attempt 的 policy GET 与正式 GET 用 resource/task 标识区别。网络内部递增后，decoder/outcome/UI 必须拿最终 attempt，不能捕获外层旧值。

正式读取的真机成功证据必须同时看到原 intent 的原因、恢复、实际请求、HTTP、typed、UI。`taskCreated` 不证明已发到后端；taskResumed 也不替代 HTTP 或服务端访问记录。

## 8. 先红后绿的自动化矩阵

先记录当前工作树/依赖/构建基线，建立反例再修实现。不得回退用户未提交改动取“红测”；可在隔离副本保存修复前源码与相同测试。不得修改业务断言制造绿测，环境/编译失败不算业务红测。

测试基础：真实 UIKit controller + 真实 FeatureGateService 的 route/request 行为 + evaluator + 隔离 ReleasePolicyStore + BackendClient + 可控 URLProtocol。测试通过依赖注入置换时钟、scope、网络，不可使用常开 QA gate 绕过目标分支。至少一个用例真实经历 policy HTTP → JSON decode → store.save → fresh decision → formal GET → typed → UI。

| ID | 失败反例/输入 | 修复后必须观察到的断言 | 层级 | 当前 |
|---|---|---|---|---|
| M01 | 初始过期直接打开正式列表 | 旧代码未触发 policy/GET；修复后一次策略恢复、一次资源 GET、真实页面 ready，零业务 POST | UIKit 组合，先红后绿 | NOT_RUN |
| M02 | 正式读取中再次搜索/筛选 | 旧代码出现账号变化；修复后最新 query 生效、不清空为账号错误、按钮可用 | UIKit 组合，先红后绿 | NOT_RUN |
| M03 | 同主体已捕获旧 denied/expired route，刷新取得新合法策略 | 不因旧 route 继续失败；新 request decision 正确，未放行旧写授权 | 真实 service/evaluator 组合，先红后绿 | NOT_RUN |
| M04 | 新鲜策略正常读取 | 零策略刷新、单次 GET；错误不被变成 empty | 三入口参数化 | NOT_RUN |
| M05 | 缓存已有合法 successor | 不再下载策略；新 decision 经过重验继续原意图 | BackendClient | NOT_RUN |
| M06 | 初始允许、发送前 capturedPolicyExpired/policyVersionChanged | 同 budget 一次恢复并读；二次过期终止不循环 | 真实 requestJSON | NOT_RUN |
| M07 | policy 200 但错误 scope/schema/hash/revision/emergency、仍过期或 disabled | 正式 GET 零次；准确失败，旧缓存不污染 | store/evaluator/HTTP 参数化 | NOT_RUN |
| M08 | 明确权限关闭、注销、vault/主体错误、未知 deny | 不自动恢复越权、不发送读取 | lease/gate 组合 | NOT_RUN |
| M09 | policy 网络失败/超时/不回调 | 期限内一次失败、释放占用；用户再刷新能发起新 intent | UIKit+时钟 | NOT_RUN |
| M10 | 正式 GET 不返回；deadline 后迟到 200 | 一次超时；晚到不能 ready，也不能释放新请求 | UIKit+HTTP | NOT_RUN |
| M11 | policy 恢复后正式 GET 401 | 合法 successor/refresh 后有界续读；全链同 trace、最终 attempt 一致 | auth/gate/HTTP | NOT_RUN |
| M12 | policy 本身401，再遇业务401或runtime拒绝 | 共享预算不相乘；第二次不允许的 auth 恢复终止，真实请求数有上限 | 组合故障 | NOT_RUN |
| M13 | 同 scope 候选+正式列表/详情同时策略过期 | 策略物理任务单飞；每个页面各自 decode/outcome/trace，不串结果 | 多消费者组合 | NOT_RUN |
| M14 | M13 取消发起者或一个等待者 | 另一个仍完成；退出者不再 GET/UI commit；没有新策略风暴 | 生命周期 | NOT_RUN |
| M15 | flight超时，新flight启动，旧响应先后到达 | 旧flight不能删除新waiter、写回旧策略或完成新intent | broker/store 竞态 | NOT_RUN |
| M16 | 策略恢复期间账号切换/同人重新登录/authorityEpoch变化 | 旧读全部终止；新主体不显示旧值；不能靠“用户ID相同”放行 | lease/HTTP/UIKit | NOT_RUN |
| M17 | 快速查询A→B→C；A最后返回 | 只展示C，query/cursor冻结；旧结果不能污染C或结束C loading | UIKit搜索 | NOT_RUN |
| M18 | 分页与刷新、重复点击加载更多交错 | 同cursor只追加一次；首屏刷新废弃旧分页；不混页 | UIKit分页 | NOT_RUN |
| M19 | viewDidLoad/appear/foreground/手动刷新交错 | 相同active意图合并；退出重入不永久loading；deadline不重置 | UIKit生命周期 | NOT_RUN |
| M20 | 详情、归纳分别初始过期 | 不经候选/列表预热均独立自动恢复；成功后才能启用相关动作 | 两真实controller | NOT_RUN |
| M21 | 错vault、无效schema、重复ID、损坏游标/详情字段 | 沿用typed fail-closed，显示读取失败，不吞字段/伪造empty | decoder+UI | NOT_RUN |
| M22 | 策略/凭据更新后调用旧候选/关联组/更正确认闭包 | 不复用旧Proposal/Binding，不自动审核，不释放unknown command | 前序审核组合 | NOT_RUN |
| M23 | 调用更正预览/审核/正式修订等POST进入恢复设施 | endpoint allowlist 拒绝接入自动读恢复；没有自动POST重发 | 负向合同 | NOT_RUN |
| M24 | 修复前后相同只读fixture、隔离PG current版本 | 读出同一当前正式事实；候选/receipt/MemoryVersion未新增，schema不变 | 隔离后端/PG | NOT_RUN |
| M25 | 所有日志注入敏感哨兵文本/URL/令牌/hash | 日志不存在哨兵；阶段reason齐全，trace/attempt不丢链 | 安全审计 | NOT_RUN |
| M26 | 已有candidate policy恢复、typed差异/精度、八种操作、关联组/unknown恢复 | 原断言保持；不以重写预期消除回归 | 原有回归 | NOT_RUN |
| M27 | 原生Live/声音/打断/快照、文字无朗读 | 相关文件无业务修改；既有自动化保持，真机另验 | 保持性回归 | NOT_RUN |
| M28 | 已显示detail/发布选择时读取授权失效 | 撤销动作，不用旧版本提交；恢复后重新确认，旧闭包仍失效 | UIKit动作隔离 | NOT_RUN |

M03 不得由“每次 evaluator.capture 新值”的替身冒充真实 route 缓存测试。M14 必须取消**最早发起策略请求**的消费者，不能只测试最后加入者。M15 必须包含旧flight响应在新flight仍等待时到达，而不仅新缓存已更新后才送旧响应。

## 9. 实施任务与本地交付门禁

| 任务 | 要求 → 代码 → 测试 | 必交证据 | 开始状态 |
|---|---|---|---|
| W0 | 冻结两仓HEAD、dirty diff、锁文件、运行环境；读取本文全部资料 | baseline、范围清单、代码指纹，标记前序差异 | NOT_RUN |
| W1 | 建立M01/M02/M03原业务反例，不动生产 | 修复前xcresult、失败断言、受控请求清单 | NOT_RUN |
| W2 | 共享只读上下文/预算/broker及fresh read decision；不改全局权限 | 文件/函数diff；M05–16、M22–23 | NOT_RUN |
| W3 | 三入口及真实页面接入；busy/query/page/lifecycle所有权 | M01–04、M17–21、M28；真实UIQA截图 | NOT_RUN |
| W4 | 安全reason、同trace/attempt、物理请求和晚到隔离 | M09–16、M25的时间序列和敏感哨兵检查 | NOT_RUN |
| W5 | 回归、隔离PG、模拟器和通用设备编译 | M24/26/27、构建日志、xcresult | NOT_RUN |
| W6 | 逐项结论、残余风险、安装计划和回退 | README、执行清单、交付报告；停止于等待真机授权 | NOT_RUN |

建议证据输出到 `/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-formal-memory-policy-read-fix/`，按 run 编号追加。原始失败证据只读，不覆盖。

本地门禁：

1. M01–M03 配对红绿；其余矩阵按实际 PASS/FAIL/NOT_RUN/BLOCKED 逐项填写，不能只报总数。
2. 使用真实模拟器跑相关 XCTest/UIKit 组合；保存正式列表、详情、归纳成功/失败/小屏大字截图。仓库现有 `run-owner-truth-formal-memory-uiqa-smoke.sh` 的固定 UIQAClient 只能证明渲染，不能替代真实 BackendClient 过期恢复。
3. OwnerTruth 及账号/策略相关完整回归；音频/Live/文字保持性。源码扫描不能替代运行测试。
4. 隔离 PostgreSQL 用合成数据验证既有正式读取接口，先检查脚本连接目标和清理范围。可参考 `scripts/backend-owner-truth-formal-memory-postgres-smoke.py`，不得默认连接生产或沿用真实账号环境变量。业务写前后对比仅针对测试库；不需要为本缺陷重新跑真实模型或重建向量。
5. 模拟器 build/test + generic iOS device build；工作区使用 `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney.xcworkspace`，实际 scheme/模拟器从本机配置读取，不安装手机、不随意更新 Pods。
6. 两仓 `git diff --check`；列出本轮差异与前序差异。只读后端若无改动就明确“本缺陷后端差异为0”，而不是把工作树已有更正差异算成本轮后端修改。

若隔离环境缺少依赖，独立修复安全的本地环境或写清 BLOCKED；不能让用户真机代替开发人员应完成的本地测试。所有强制本地项通过才可写 `A_LOCAL_PASS / READY_FOR_FM_POLICY_DEVICE_RETEST`，否则写 `A_LOCAL_INCOMPLETE` 并列阻塞项。

## 10. 部署、兼容与数据判断

| 项目 | 决策 |
|---|---|
| 正式记忆HTTP协议 | 不变，继续现有三个GET与typed schema；新增的是iOS内部读取context |
| 后端业务代码/数据 | 本次无需改动；无迁移、无历史清理、无补写审核 |
| PostgreSQL/pgvector/BGE-M3/DeepSeek | 无需新组件、重新索引或改变模型 |
| 后端部署 | 预计不需要。交接报告笼统说“重新部署匹配版本”，对本次纯iOS修复应具体化为“构建并获准安装新iOS包”；不能无理由重复部署 |
| 当前后端基线 | 留存部署证据为 `dreamjourney-b4-correction:20260914-0055`，image `sha256:cf4b531c6f5cfddbd29e7b6a15eddbb8120c3ce04e9a9f10a58cddd713e28115`，schema0121 |
| 两个decision-result | 最新报告记载已部署并通过鉴权分类探针。未知写恢复真机能力仍未由本轮明确成功写入验证；不能再列“接口未部署”，也不能直接标完整恢复PASS |
| 真机安装 | 等用户明确授权并接入iPhone，再安装与验收源码指纹一致的诊断构建；只读测试无需再次审核候选 |

新旧iOS均使用同一HTTP读合同，无新的服务端兼容窗口。不能回退已部署的 correctionBinding API 来“匹配”本次读修复。若Sol发现必须新增后端字段/改权限，先提供新的直接证据与范围说明，停止该扩展等待决定，不擅自迁移部署。

## 11. 真机复测：每步展示、每步等待用户反馈

开发人员不得把下表一口气暗中执行。先告诉用户当前一步、预期、是否会产生数据变化；得到反馈后再进入下一步。下面仅是后续操作说明，本次不执行。

| 步骤 | 告知用户的操作 | 开发人员记录与判定 |
|---|---|---|
| D0 | “请连接并解锁iPhone，暂时不要审核任何候选。” | 获得安装/只读诊断授权；确认构建SHA、后端合同基线、账号合法；安全日志开始 |
| D1 | “先直接进入记忆记录，确认能看到现有内容。” | 新鲜策略基线：正式GET/typed/UI正确；无业务写；若路径经人生记录，分别记录两入口 |
| D2 | “现在先停留，不打开待确认记忆。我会确认策略自然到期后再通知你。” | 不改TTL、不改生产策略、不注销以制造过期；后台自动刷新可能已续期，必须看到本次intent实际过期再计此用例 |
| D3 | “请直接打开记忆记录，先不要手动刷新，也不要进入候选列表。” | 原intent过期reason → 一次策略恢复/合法successor采用 → 正式GET200 → typed → UI。页面自行成功才PASS；自动被其他页面预热则本项未覆盖 |
| D4 | “请搜索刚才已确认的测试内容，再清除搜索并切换一次筛选。” | query隔离、内容正确、无假账号错误；不把搜索词写日志；已有权威事实只读，不重写 |
| D5 | “打开这条正式记忆的详情，查看版本后返回。” | detail读取正确；权限到期的detail场景需另次自然等待，不能由列表已刷新替代 |
| D6 | “请进入人生记录，再返回记忆记录。” | profile/list均可读；profile独立过期自动恢复需要独立证据，无证据留NOT_RUN |
| D7 | “短暂切换到其他App，再返回；然后刷新一次。” | 原intent结束/取消后正常恢复；无请求风暴、永久loading、旧回调覆盖 |
| D8 | “这次只看结果，不点击接受、更正、拒绝或发布。” | 汇总每条trace与实际GET，FM状态单独结论；业务写请求0，不重新生成测试候选 |

离线、延迟、账号切换、多人并发、未知写等强制故障优先在隔离模拟器/测试后端执行。生产不注入损坏策略、不改时钟/有效期、不丢弃真实审核回执。若自然遇到失败，先保留现场，不能连续刷新把证据抹掉。

本次只读问题闭环不要求重复修改已成功的正式记忆。用户另行要求继续B4-8时，再使用其批准的新表达逐步测试，不拿FM读取通过代替会后候选生成/关联/审核。

## 12. 保持、回退与最终验收状态

### 12.1 不可破坏

- 火山原生Live的音频、低延迟、连续聆听、打断、sessionSnapshot正式事实绑定不改；不恢复ASR→DeepSeek→TTS串行链路。
- 文字问答继续DeepSeek、不朗读；正式记忆仍是唯一跨会话事实来源，未审核内容不能参与回答。
- 保留typed结构差异、数值精度、八种ChangeSet操作、关联组、correctionBinding/VerifiedCorrectionPreview、CAS/hash/revision以及unknown只读结果核实。
- 不代用户接受/拒绝/更正，不删除或补写生产候选、正式记忆、审核历史、投影或Dead Letter；F3不搭车处理。

### 12.2 局部回退

若共享恢复出现循环、权限越界或候选回归，停止本轮安装/发布，只撤销本轮只读恢复接入或局部关闭本次自动恢复，恢复准确的失败提示；将该项重新标FAIL。不能把gate常开或把旧缓存当新授权。

保留前序未提交更正修复；不得整仓reset、按HEAD覆盖工作区或回退生产事实。回退不需要数据库操作。若已安装本次包，授权后换回保留correctionBinding能力的前一诊断构建，并保留失败日志；禁止单独回滚依赖中的后端API。

### 12.3 当前状态表

| 验收项 | 当前状态 | 状态依据/升级条件 |
|---|---|---|
| 代码与本地留存证据独立核查 | PASS | 本文给出当前调用链/差异；不等于新实现通过 |
| 更正预览双重绑定、用户更正真实写入 | PASS | 保留9月14日原报告证据，本次未重测 |
| 正式记忆写入、投影、向量、文字与新Live回查 | PASS | 保留原成功证据；不能因本次列表失败降为写失败 |
| 候选列表首次策略过期恢复 | PASS | 原现场证据；共享层修改后还须跑保持性回归 |
| FM-POLICY-01直接首次读取自动恢复 | FAIL | 真实问题尚未修复、未复测 |
| busy被当账号变化的代码路径 | FAIL | 静态控制流已确认；运行反例M02为NOT_RUN |
| M01–M28本次自动化 | NOT_RUN | Sol执行并逐项记录 |
| 本轮模拟器/PG/编译 | NOT_RUN | 本文未执行这些工作 |
| 本轮新iOS包真机安装及FM复测 | NOT_RUN | 后续授权后执行 |
| B4-1至B4-7 | PASS | 保留历史已通过项 |
| B4-8与B4整体 | FAIL | 尚无本轮新会后候选闭环；FM修好也不能直接升级 |
| 历史三条候选重复性/新查询候选语义 | NOT_RUN | 不在本次只读缺陷测试中操作 |
| F3历史缺checkpoint实际影响 | NOT_RUN | 不推测为本次根因 |
| decision-result接口部署 | PASS | 最新报告记载已发布、分类探针通过；不是本次远程复查 |
| decision-result真实unknown恢复 | NOT_RUN | 明确成功写入未触发该分支，不能以接口200替代 |
| 原失败正式trace的精确deny子原因 | NOT_RUN | 留存缺失，需安全诊断；不能用候选trace伪补 |

最终交付必须有：修改文件/函数、红绿配对、M矩阵、源码和构建指纹、策略/资源实际请求数、真实UIKit证据、权限与旧写授权保持性、后端零改动判断、残余缺口、真机逐步脚本及停止点。不能仅写“测试数增加，全部通过”。

## 附录：本次关键源码指纹

以下为源码文件SHA-256，不是业务内容hash。Sol开始时重新核对，漂移时保留新变化并重新定位，不用这些值强制回退。

| 文件 | SHA-256 |
|---|---|
| iOS BackendClient | `5328149a84d5b422b238f01afc86dedea535060d39f42ecd514dec33bd131cad` |
| iOS ReleasePolicyStore | `00089524fac56154293965e55900f8a2d3628db4fdaf6b6fe7cf6a332587c3e7` |
| iOS OwnerTruthFormalMemory | `1485f74fadc8be7aaeebf3f7f9fa297823c7cdc6d360681712ee4d29042e9d39` |
| iOS OwnerTruthFormalMemoryViewControllers | `d8853fed7a4ed515752c4a31ce2b73020ce2af56bf0a17001b9f0455f08b48d6` |
| 后端 app/main.py | `e3bd08833e801347a296f0c5a1d1007576a385f8e67b6fb041aa51345d0c8ddc` |
| 后端 owner_truth_formal_memory.py | `dd3afb845090c585a15a1cad9a9d3c816d6ca3293f422aaf221392d262feeb73` |
