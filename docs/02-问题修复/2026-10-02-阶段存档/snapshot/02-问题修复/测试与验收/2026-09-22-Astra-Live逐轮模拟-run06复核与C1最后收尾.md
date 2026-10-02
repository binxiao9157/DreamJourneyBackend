# DreamJourney Live run-06 复核：仅剩 C1 事实与证据错绑定

日期：2026-09-22。

**结论：认可 A3、B1、B3、C2 及本轮正常短长链结果；C1 仍有一个已复现的验收漏检，暂不能把完整本地验收标为通过。剩余问题发生在受控模型验收代码，没有证据表明 Live 正常保存又被改坏。**

本文件仅承接 [run-05 有限收尾文档](2026-09-22-Astra-Live逐轮模拟-run05复核与有限收尾.md) §4 对“事实、正文、证据引用与独立真值表”的要求。不是新增产品功能，也不要求重新处理已经关闭的 A3/B1/B3/C2。

## 1. 已认可的部分

| 项目 | 本次独立核对结果 |
|---|---|
| A3 源码门禁 | 上轮点名的 4 个漏项已纳入 25 项清单；25 项当前文件 hash、整体 source fingerprint、宿主 dylib、测试可执行文件及 xctestrun 与交付一致。实际消费路径会重算源码，临时副本变化负例返回 412、业务计数不增加 |
| B1 逐轮身份 | Swift 从磁盘 canonical→deliveryMessageID 读取映射；服务器校 command/message、路由作用域、201 receipt 及 Source sequence。独立重跑交付探针，正常映射通过，同角色错 canonical 和错确认 identity 均被拒绝 |
| B3 正式记忆关系 | 通过持久化 candidate→decision receipt→memory/current version 的 SQL 关联核 Source/evidenceRefs，再核重建 API 中对应 memory 可见，并定位 publication manifest 中对应候选的双 turn 证据。两场错候选关系负例有原始结果 |
| C1 已完成部分 | 伪造事实正文、缺必要输入证据、关系 incoming 伪事实均在受控 transport 被拒绝，正常对照通过。此次独立重跑了交付探针 |
| C2 合法 unit | short、logical20、logical65 使用合法 unit 的正文替换负例均真实命中 transport 1 次后拒绝，不再因超过 200 轮而在前置层失败 |
| 正常链顺序 | 四份原始 xcresult 各 1/1 PASS、0 failed、0 skipped；runner 顺序为 short→20→short→65 |
| 正常链结果 | logical20：220 总回合、4 条长场候选、含 short 共 5 条正式记忆；logical65：300 总回合/150 用户轮、17 条长场候选、含 short 共 18 条正式记忆 |
| 既有成果 | 真实客户端候选读取、隐藏一条的负例、401/刷新/原命令恢复、保存后原响应丢失只读核实、逻辑时钟与隔离 PG 结果保留；报告对数量、SQL、授权分支和证据类型的校正已完成 |
| 回归复用 | 原交付日志明确后端 92/92 通过。run-05 指纹中列出的 OwnerTruthContracts、EchoViewController、后端 conversation、long memory、Worker 业务文件与现文件相同；iOS 测试文件发生变化，与报告范围一致。复用旧回归应继续标明复用，不写成本轮重跑 |

本次读取了报告、原始 xcresult、源码与构建摘要，并执行了轻量离线探针；没有重新运行完整模拟器/HTTP/PG 集成、构建、真实 Provider 或手机测试。

## 2. 唯一剩余：正确事实仍能引用本场错误的用户发言

问题编号：**SIM-C1-EVIDENCE-BINDING-01**。

### 2.1 可直接理解的反例

使用交付原有 logical20 合成场景，没有新造产品需求：

- turn 1：“我的逻辑二十分钟测试偏好是阅读历史故事。”
- turn 3：“第 2 轮普通问题：今天适合阅读吗？”
- 待验证事实保持“我的逻辑二十分钟测试偏好是阅读历史故事。”不变。
- 正常对照引用 turn 1；错误样本仅把 `sourceTurnIndices` 改成 `[3]`。原对话正文、索引、用户角色和条数均保持合法。

实际调用交付原适配器与原后置验证器的结果：

| 环节 | 正常引用 turn 1 | 错误引用 turn 3 |
|---|---|---|
| support 受控 HTTP 边界 | accepted，supported，[1] | **仍 accepted，supported，[3]** |
| relation 受控 HTTP 边界 | accepted | **仍 accepted，并返回 duplicate** |
| 后置请求校验 | accepted | **两种错误输入都 accepted** |
| 实际 transport | 每项 1 次 | 每项 1 次 |

因此当前校验能识别“凭空编造事实”，但尚不能识别“事实存在，却把证据指到另一句无关的话”。这里不是页外合法上下文，也不是模型改写同义表达：turn 3 是问题，没有表达该偏好。

### 2.2 代码原因

- [受控适配器:352](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:352) 在生成事实 key 时排除 `sourceTurnIndices` 和 `evidenceFragmentIds`，让语义与引用分离本身是正确方向。
- 但 [validate_memory_evidence:419](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:419) 后续仅分别确认“语义属于场景已知事实”与“引用属于本场用户且在当前输入中”，没有确认这条事实与这份证据相匹配。
- [后置 validate_prompt_facts:1382](/Users/gaominge/Documents/liftora/outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-06/tools/cap15_round_simulation_server.py:1382) 也做了相同的分开检查，因而无法补救边界误放行。

这不是已证明的生产模型错误、短场保存故障、B7 产品回归或容量问题。它是 C1 原要求中“错误引用必须拒绝”仍未完成的验收缺口。不要据此修改 Live 音频、保存、B7 或重试策略。

## 3. 最小收尾设计

### 3.1 只补验收侧事实—证据关系

在受控适配器与后置校验中，为独立场景真值建立“事实语义身份→允许支持它的原文证据”的关联，而不是两个互不关联的集合。

- 普通事实必须由其对应 turn/fragment 支持；不能只要引用本场任一用户句就接受。
- 原文证据继续使用既有 turn 身份、range/hash 与片段身份；不能把请求自己声称的引用直接当作正确答案。
- 合法跨批重复允许同一事实有多个有效出现位置，例如摄影事实 turn 7/189、古琴事实 turn 7/221。不能把第一次位置写死，也不能因合并后有两个引用而拒绝。
- 合法补充、纠正、撤回和关系处理后的表达，按照已有场景真值及已验证输入关系继承对应证据。仅增加一个“可信事实 key”不能代替证据关联。
- 分页按本页责任范围校验，保留合法完整原 turn 或精确 evidence fragment。不要要求每页包含整场，也不要把合法页外证据误判成遗漏。
- 受控 HTTP 边界与后置检查应复用相同证据关联规则，避免一边拒绝、另一边继续放行。无需改业务 API 或新增 App 字段。

### 3.2 最小红绿测试

直接使用本次 [离线复现脚本](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-06/evidence/provider_wrong_binding_probe.py) 的四个场景：

1. support：原事实 + 正确 turn 1，必须通过。
2. support：原事实 + 错误 turn 3，必须在受控 transport 拒绝；同请求后置校验也不能通过。
3. relation：原事实 + 正确 turn 1，必须通过。
4. relation：原事实 + 错误 turn 3，必须在受控 transport 拒绝；不能返回 duplicate 后视为通过。

修前证据已保存，错误场景当前均被接受。修后相同事实、相同错误引用和相同断言应转绿，且实际 transport 命中 1 次，不能通过输入上限、参数格式错误或不调用边界获得假通过。复现脚本是诊断记录器，Sol 应将上述期望转换为明确的正反断言。

保留 run-06 已通过的伪事实、缺必要证据、正文替换负例，以及合法重复/补充/纠正/撤回/分页对照。优先复用现有场景，不扩展新产品语义。

### 3.3 结束条件

1. 只修改验收适配器、相关探针和矩阵。若未复现新业务缺陷，不修改产品。
2. 上述错误引用拒绝、正常对照通过，C1 即可关闭；A3/B1/B3/C2 保持已通过，不重新定义验收范围。
3. 验收脚本属于 source fingerprint，本次修改会使旧 short 凭证失效；最终版本按既有顺序重新完成 short→20→short→65。两条 short 都验证待确认候选、审核、正式记忆及重建读回，然后才进入各自 long。
4. 重跑本次受影响的模型验收/后端专项与集成，更新最终指纹；无业务变化的 iOS/audio 回归可继续准确复用，不为了数量重复全量。
5. 本项及现有正反例完成后即可结束本地交付。真实 Provider、部署、物理 20/65 分钟与 iPhone 仍是独立阶段，由用户主动发起；不能因没有手机而中断本地任务。

## 4. 可直接发给 Sol 的提示词

请按《2026-09-22-Astra-Live逐轮模拟-run06复核与C1最后收尾.md》完成最后一个 C1 验收缺口。Astra 已认可 A3、B1/B3、C2、正常短长链及报告校正，保留这些成果，不要重做保存、音频、B7 或认证系统。

唯一已复现问题：logical20 中“喜欢阅读历史故事”原本由 turn 1 支持，只把 sourceTurnIndices 改成指向 turn 3 的“今天适合阅读吗？”后，support 仍 supported、relation 仍 duplicate，后置校验也通过。请用文档中的原脚本和结果做红测，只在验收侧补事实与原文证据的对应规则；不能仅分别判断事实存在、引用属于用户。保留合法分页、同义表达、跨批双位置重复及补充/纠正/撤回，不能把 sourceTurnIndices 塞回事实名字后要求整数组绝对相等。

修后同错误引用在实际受控 transport 被拒绝，正常对照继续通过；在最终源码/构建上依次 short→20→short→65，交付原始结果、准确矩阵和指纹。产品未变的有效回归可以注明复用。本地持续完成，不连接或等待手机、不调用真实 Provider、不部署、不访问生产或历史、不 commit/push；后续真机由我主动发起。

## 5. 独立复核证据

- [原始四份 xcresult 与当前源码/构建核对](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-06/evidence/source-and-xcresult-check.json)
- [run-05 列出文件与当前文件比较](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-06/evidence/run05-listed-files-comparison.json)
- [交付 B1 探针独立重跑](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-06/evidence/delivered-cross-stage-probe-rerun.json)
- [交付 C1/C2 探针独立重跑](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-06/evidence/delivered-provider-probe-rerun.json)
- [错误引用探针原始结果](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-06/evidence/provider-wrong-binding-result.json)
- [Sol run-06 主报告](../../outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-06/reports/2026-09-22-DreamJourney-Live逐轮模拟-run06有限收尾报告.md)

所有本次探针使用合成内容、内存对象与 MockTransport；没有真实模型调用、数据库连接、真机操作或业务重放。未修改产品及 Sol 原始交付。
