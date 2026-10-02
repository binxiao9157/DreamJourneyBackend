# DreamJourney Live 候选整理修复：run-02 放行复核与收尾清单

日期：2026-09-19。范围：复核 run-02 是否满足既定本地验收，不新增产品功能，不开始真机测试。

## 1. 结论

**主链修复和本地持久化验证已取得实质通过，但“原设计全部本地门禁完成”的结论仍不成立。**

这轮已经完成生产默认提取器接线，以及真实 PostgreSQL 中的候选生成、审核、正式记忆、仓库重建读取和幂等。iOS failed 场次的真实组件销毁、同盘恢复也已补齐。这些结果应保留，不要求重新设计或重做。

剩余项集中在三个已复现的后台边界、数据库故障验证和 iOS 验收证据。不能继续以测试总数代表未覆盖的分支；也不能把这些验证缺口描述成“主保存链已经证实仍然失败”。本次未调用真实 Provider，未访问生产或手机。

建议当前整体仍记 `LOCAL_INCOMPLETE / PROVIDER_NOT_RUN / DEVICE_NOT_RUN`，并单列已完成主链。补齐本文限定的剩余项即可重新申请本地放行，不扩展到历史 UI、音频、轮询预算或超长输入架构。

## 2. 复核依据与已确认结果

- [run-02 本地交付报告](../../../outputs/2026-09-18-dreamjourney-live-candidate-contract-fix/run-02/reports/2026-09-19-DreamJourney-Live候选整理修复-本地交付报告.md)
- [run-02 BE/IR 矩阵](../../../outputs/2026-09-18-dreamjourney-live-candidate-contract-fix/run-02/reports/2026-09-19-BE-IR执行矩阵.md)
- [原设计](2026-09-18-Astra-Live记忆候选整理失败-局部修复设计与Sol执行要求.md)
- [上轮 C1～C6 复核清单](2026-09-19-Astra-Live候选整理修复-交付复核与剩余修改清单.md)

当前 Worker、lease repository、DeepSeek adapter、两份 PostgreSQL 脚本、iOS OwnerTruth 测试、生成夹具和 UIQA 脚本这 8 个文件的 SHA-256 均与 run-02 报告匹配。

| 原复核项 | 本轮确认 | 保留的工作 |
|---|---|---|
| C1 默认生产上下文 | 已闭合 | Worker→SourceExtractor→LiveExtractor 显式传递恢复反馈 |
| C2 响应与证据校验 | 上轮列出的反例已修；仍有一个类型边界 | envelope、content、length 截断和 bool 证据保护 |
| C3 诊断 | 跨 job 终态去重已修；阶段日志仍有问题 | 不同任务独立输出终态的修改 |
| C4 范围与上下文 | Live 范围、首次白名单、连续记录、失租一致性已补主体 | 原预算和 Live-only 分类，不回退 |
| C5 PostgreSQL | 正常主链与最后 attempt 失租/重领预算已通过 | 同库候选→确认→正式记忆→关闭旧 pool→新 Store/TestClient 读取 |
| C6 iOS | failed 保留和全组件销毁后重建已通过 | 新 cold-start 主链、原 UI/音频回归 |

原始日志确有后端定向 131 项、iOS OwnerTruth 526 项、Echo/音频/账号 103 项通过。本次另运行了 26 项相关后端定向/lease 测试，通过；没有重新运行全部 iOS 或数据库测试。

后端全量 2619 项中的 7 项仍是旧 HEAD 已独立复现的路由计数基线失败。不将它们算作本次新增回归，也不把后端全量写成全绿。

## 3. 后台仍需修改的有限边界

### 3.1 默认阶段日志实际被关闭，且支持验证成功事件过早

定位：[Worker 默认阶段日志出口](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:119)、[支持复核事件](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1214)。

两个问题已离线复现：

1. 默认 sink 使用 `_LOGGER.info`，独立 Worker 入口未初始化相应输出。按当前直接模块启动配置，logger effective level 为 WARNING，调用默认 stage sink 实际输出 0 字节。测试注入列表 recorder，不能证明部署入口会输出这些阶段。每 job 的最终 JSON 输出已修，应区分保留。
2. `supportValidated` 在 `parse_support_review` 返回字典后就发送；真正完整语义验证在 LiveExtractor 的 `validate_live_memory_support` 中稍后执行。受控 uncertain 响应先记录“已验证”，随后又失败，阶段证据不准确。

收尾要求：

- 让实际 Worker CLI 的安全阶段输出可用，使用本模块受控日志出口或既有输出机制，不全局开启可能含第三方敏感内容的 debug 日志。
- 使用默认 sink/实际入口配置测试；捕获真实输出，不以注入 recorder 代替。继续验证敏感字段不泄漏、记录失败不改变业务结果。
- `supportValidated` 仅在完整 schema、证据、覆盖与语义复核成功后发出。解析成功可用独立 decode 事件表达；对 uncertain、遗漏、非法证据断言不会提前出现验证成功事件。

这是原 BE-12 和阶段诊断要求的收尾，不修改候选语义或扩大重试。

### 3.2 finish_reason 非字符串仍被误分类

当前 `finish_reason` 为 list/dict 时触发 TypeError，Worker 将其分类成 `candidateExtraction.internal.transient`。上轮要求的严格 HTTP 合同检查仍缺这个类型分支。

收尾要求：在比较完成原因前先按正式响应合同校验类型，错误类型返回固定 typed contract failure。补 list/dict 等受控 envelope 反例及合法值对照，断言精确分类、原 attempt 资格和零候选；不能捕获所有异常后统一重试。

这是 C2/BE-03、06 的既定边界，不代表真实供应商曾返回这种响应。

### 3.3 后续 attempt 的错误种类仍未验证

当前 reader 已要求 attempt1 是固定白名单合同错误，后续记录连续且为 retryableFailed，但没有核对后续错误码是否真是允许沿原预算继续的 transient。

离线合成记录显示：attempt2 即使填入再次合同失败、authorizationRejected 或 arbitrary，也能为 attempt3 生成恢复上下文。正常 Worker 可能不会写出这些历史，不能夸大成正常重试必然失败；它仍违反原设计“连续合法历史”的明确保护要求。

收尾要求：为沿用 attempt1 反馈的后续记录定义固定合法暂态集合；拒绝再次合同失败、权限拒绝和未知错误历史生成修复上下文。内存与 PostgreSQL 用同样的允许/拒绝夹具；保留 contract→合法 transient→success，不增加预算、不改历史记录。

对应 C4/BE-07、08。拒绝异常上下文不等于允许新建任务、重置 attempt 或自动重放未知写。

## 4. 数据库验证：主链已通过，补故障原子性和并发

当前两份新 PostgreSQL 脚本和执行日志能支持：

- 默认生产提取链、真实数据库迁移和持久层；短场 1 条、长场 5 条合成候选，经真实 API 审核/activation 后共 6 条正式记忆。
- 关闭旧 pool、重新创建 Store/TestClient，读取正式正文、版本及 Source；同命令重放不产生新版本。
- 持久反馈跨 transient 保留，最后允许 attempt 模型返回后失租，旧 worker 无法提交，重领后不新增模型调用。

但当前 run-02 的数据库证据没有覆盖原要求中的**并发 Worker 竞争和提交中途故障回滚**；对应矩阵主要引用内存测试与顺序失租用例。它们不能证明 PostgreSQL 的真实事务原子性。

最小补证：

1. 两个独立连接/Worker 在受控同步点竞争同 job，断言只有合法 lease 结果可以提交，候选、extraction、receipt 和任务状态不重复、不矛盾。
2. 在真实提交事务的中间阶段注入故障，回读数据库确认候选、extraction、receipt、任务结果没有部分成功；恢复后的同 job 仍沿原幂等与预算规则执行。
3. 补齐原 BE-11 对模型等待期间 Source/version/epoch/权限失效的持久场景或引用已经实际运行、可覆盖同样断言的数据库证据；旧结果不能提交。只要已有有效证据，不重复搭建。
4. 在受控 HTTP 等待点验证不会长期占用数据库事务；可复用现有异步门障和数据库活动检查，不以固定延时推定。

无需重写现有正常链；在隔离一次性 PostgreSQL 中扩展现有脚本即可。仓库已有 `backend-async-effects-postgres-smoke.py` 的并发、epoch admission 和 consumer 回滚设施可复用，但目前 run-02 没有其执行日志。无需把所有内存用例机械搬到数据库。不能改用生产数据库，也不能因为实例已停止就跳过门禁或转去要求手机。

## 5. iOS：不重做冷启动主链，只补三个验证组

### 5.1 IR-02 的实际故障分支

新 failed 冷启动用例已经证明三类记录可保留并重建。仍需参数化补：

- quarantined 三类记录和失败观察保留。
- failed/quarantined 的 observation 写盘失败后，原坐标仍在、状态不伪造、不会进入删除待办或触发清理。

当前引用的 `testB8S01TerminalObservationPersistenceFailureKeepsRecoveryCoordinates` 返回 succeeded/reviewReady，不能替代失败分支。正常保留 `cleanupPending=false` 的源码方向正确，本轮无需改回。

### 5.2 IR-05 的成功清理故障

补 pendingReview/empty 的重复终态、文件已不存在、三步清理中局部删除失败：业务成功不回退、不新增业务 POST，幂等与剩余恢复记录按原规则处理。终态观察落盘失败、页面重进与删除失败是不同动作，不能互相替代。

旧 failed 测试中的 0.05 秒固定等待应清理，但新 cold-start 测试已有真实生命周期完成依据，**该冗余等待不单独作为重做主链的阻断项**。

### 5.3 IR-07/原设计 §9.3 的跨端响应链

当前生成夹具只被 `testBackendGeneratedCandidateStatusV3FixturesDecodeExactRecoveryStates` 直接解码；真实 BackendClient/cold-start 和 UIQA 仍用手写 JSON。生成脚本构造 status 对象并调用 serializer，没有经真实路由。

因此矩阵“Controller/UIQA 消费同版本后端夹具”的表述不准确。这是验证链未完成，不是已证明 Controller 业务损坏。

最小补证：用同版本后端真实状态路由测试生成 queued/retryWait/failed/reviewReady 响应；把相同响应接入已有 URLProtocol→BackendClient→FeatureGate/Coordinator→Controller 装配。附生成命令、源码指纹，断言同 batch 的 UI 与只读请求次数。无需另建端到端框架。

## 6. 原则、交付和后续步骤

- 只完成 §3～5 的有限收尾，保留已通过主链；原 BE/IR 编号不再变更。各子项有证据才标 PASS，不能靠 526/526 等总数代替。
- 改动后执行相应定向测试和受影响回归；若修改共用审核/正式记忆逻辑，保留 revision/hash/CAS、授权及幂等回归。未触及模块不无理由反复重测。
- 短场两轮加补充、长场物理至少 20 分钟五项首中尾事实加补充/纠正的真机清单已符合原要求，应原样保留。
- 真实 Provider、真机、部署、历史重处理仍分别 NOT_RUN。**真机由用户另行主动发起；本地修复和测试不检测、不等待手机。**
- 7 项旧路由失败继续单列基线；超长 support 输入限制继续单列，不在本轮改架构或篡改验收内容。
- 不部署、不访问生产、不处理历史、不审核真实候选、不 commit/push。完成本地收尾并交付后正常结束任务。

可直接发送给 Sol：

> 请读取本文件，在 run-02 已通过主链上只完成 §3～5 的剩余项：默认阶段日志及正确语义时点、finish_reason 类型校验、后续合法 transient 历史验证、PostgreSQL 并发和事务故障门禁，以及 IR-02/05/07 的真实分支与跨端响应组合。保留默认生产接线、数据库正常正式记忆链和已通过 failed 冷启动链，不重做、不回退。使用同断言先红后绿，按原 BE/IR 编号补证并校正报告。完成受影响本地回归后交付；不部署、不访问生产、不处理历史、不 commit/push，不检测或等待手机。真机仍由我后续主动发起。

## 7. 独立证据

- [本轮后台离线探针](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-candidate-delivery-review/run-02/evidence/c1_c4_offline_probe.py)
- [探针结果](/Users/gaominge/Documents/liftora/outputs/2026-09-19-live-candidate-delivery-review/run-02/evidence/c1_c4_offline_probe.txt)
- [本轮数据库证据复核](../../../outputs/2026-09-19-live-candidate-delivery-review/run-02/evidence/postgres-review.md)
- [run-02 PostgreSQL 正式记忆链日志](/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-candidate-contract-fix/run-02/evidence/green/postgres-live-candidate-formal.log)
- [run-02 PostgreSQL 预算日志](/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-candidate-contract-fix/run-02/evidence/green/postgres-live-retry-budget.log)

以上新增复核探针只使用本地合成数据与受控网络边界；没有重新执行真实供应商、生产数据或手机操作。历史精确失败输出仍未知，不将当前反例写成历史唯一根因。
