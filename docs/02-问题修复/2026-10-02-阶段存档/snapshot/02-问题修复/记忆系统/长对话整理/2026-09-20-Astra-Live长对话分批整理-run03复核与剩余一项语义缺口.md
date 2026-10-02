# Live 长对话分批整理 run-03：复核与剩余一项语义缺口

日期：2026-09-20。范围：核对 Sol run-03 修复及上一轮指定缺口，不修改产品代码，不重新执行真机或供应商调用。

## 1. 结论

**上轮明确反例已有实质修复，仍有一项 R02-B 的正常语义场景失败，因此不能整体放行 LOCAL_PASS。建议当前状态为 LOCAL_FAIL / NEEDS_LOCAL_FIX。**

问题集中在证据范围仍依赖“生成文本与原话完全相同”。模型正常改写、不增加新事实时，分页支持复核仍可能误判漏项，导致没有候选。下一步应聚焦修复这一点，保留当前已经通过的功能；不要再次改动保存文案、音频或重做正文持久化链。

本文件另列证据范围及少量交付补证，不将它们混称为新业务根因。Provider、20/65分钟真机、部署、历史重处理继续独立为NOT_RUN。真机由用户主动发起，不检测或等待手机，不因手机不连接暂停本地开发。以前8次真实模型诊断额度不构成本轮付费调用授权。

## 2. 已独立确认的修复

| 项目 | 当前本地结论 |
|---|---|
| 单turn 12事实原反例 | 通过：8条饱和后细分为6+6，12条候选 |
| R01-B，同一发言既有事实又有问题 | 通过：9事实形成9候选，问题片段排除，原turn不再整体排除 |
| R03-B，跨批精确重复 | 通过：前8条事实加第9次重复，9atom合并成8候选，重新验证完成 |
| R02-B，原逐字表述的合法补充分页 | 通过：12atom合并成11候选，各页不误报遗漏 |
| R04-B，失败预整理再交接父Source | 独立探针通过：预整理最多2次调用，父提取返回runTerminal，调用仍为2；PG记录也覆盖父job失败、候选0及重启idle |
| 无原话支持的新事实、缺atom | 两项负向保持性测试通过，未被放松 |
| LI-01，第7次GET自动完成 | 新真实Controller/磁盘/FeatureGate/BackendClient/受控HTTP组合已补，活动trace第7次自动reviewReady、无人工核实、零业务POST |
| LI-02，多个超时出口 | deadline、逾期poll与预算结束统一调用结束观察逻辑，保留已知阶段 |

源码指纹：run-03新增记录与run-02累计基线合并核对，后端31个、iOS19个均与当前文件一致。iOS本轮532/532、音频租约5/5及长回答/打断/PCM25/25属于本地测试结果，不能替代真实设备声音验证。

## 3. 唯一已复现的剩余语义问题：R02-B-PARA

### 输入与结果

使用上一轮“12个事实、其中两个合法补充合并”的同一个合成场景，只让组织结果把：

> 我的研究主题是古琴。

正常整理为：

> 我的研究方向为古琴。

受控独立support明确认可该表述有原文支持，未加入人物、经历或新事实。12个atom已全部提取；合法补充完成后，第一页只有4条最终候选，却携带了包含6个事实的原片段，再次得到`candidateExtraction.live.supportValidate.factOmitted`，整场提取失败，Run仍在organizing。

这是当前真实DeepSeek adapter、完整schema及MockTransport的本地确定性反例。并非假定真实供应商必然给出此响应，也不是原私人对话重放。前四个正向场景使用同一脚本成功，排除了旧模拟响应不适配新拆分导致的误判。

### 根因

- [worker:1472](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1472)在`_bind_support_proof`中使用`text.find(primary_value)`寻找生成正文。
- 若找不到完全一致的字面文本，[1473处回退](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1473)使用整个原片段作为该memory的证据范围。
- [最终候选复核:2209](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:2209)每页仅4条候选；[owned evidence拼接:2346](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:2346)将前述大范围重新传给support。
- 现有support合同仍要求输入范围内的事实都有最终草案。本页之外的正确事实因此重新变成“遗漏”，重现R02-B，区别只是触发它的是正常释义。

“原文证据范围”不能由“模型生成的候选是否逐字出现在原文中”决定。记忆表述允许整理，事实及证据身份必须稳定。

## 4. Sol 局部修复要求

1. **证据与表述分离。**在原文分片/原子提取时建立稳定证据标识及原文区间，与atom绑定。组织后的claim/summary只作为待验证表达；换措辞不会改变其拥有的原子事实集合和证据身份。
2. 若需要模型返回证据引用，引用必须指向提供给它的不可变原文片段/ID；服务端校验ID、范围、版本、hash及原文一致性。不能信任模型自行给出的任意坐标，也不能把生成文本当证据原话。
3. **分页支持与全场覆盖使用不同责任集合。**本页验证明确的atom/item ID及其当前表达；其他原话可作为上下文，但不能要求本页再次输出上下文中全部事实。全场原文审阅完整性、atom去向和跨批关系仍由发布台账完整检查。
4. 当一句原话同时支持多个事实，允许证据范围共享；共享范围内的页外事实不能凭空变成本页责任，也不能因此从全场覆盖中删除。
5. 无法建立可信绑定时保留原文、进入有界补证或明确失败；不能退回整段后继续把它当本页全覆盖范围。不能用模糊匹配、强制逐字抄写、删掉omitted、填假proof hash或把事实标成query让测试通过。
6. 沿用同Run、同Source、稳定Unit和现有预算；成功后的候选仍只在会后统一发布。不要影响已通过的失败Run终态、未知业务写保护、短场正式记忆、音频及iOS观察行为。

这是一处证据绑定/复核合同的局部修复，不要求再重写采集、end/ACK/admit、历史UI仲裁或B7语义规则。

## 5. 本地红绿验收

- **必须转绿：**保留本次第5个探针，正常释义的12个atom应得到11份最终候选，支持引用正确，分页没有页外遗漏误报。
- 保持同一脚本前4项成功；额外增加语序变化、同义表达、跨多个原句的合法合并、同句多事实跨页，以及重复原句出现在不同位置的证据绑定场景。
- 负向保持：新增购房等无证据事实、错误人物/时间/地点、无效引用、越界范围、缺atom、伪替代/撤回仍阻止发布。
- 沿默认Runtime验证私密草稿→同Source关闭admit→支持复核→统一候选事务；错误修复不能让failed Run再调用模型。
- 同一规定压力夹具需经过真实请求构造和parser，不能让Fake Provider直接返回超过真实合同上限的草案来证明容量。只替换HTTP/时钟等边界，独立真值清单不能由模型输出倒推。
- 复跑受影响的短场候选→用户审核→正式Memory/Version及重建、旧R01～R04反例、B7、预算与权限保护。未触及的音频/iOS实现不为凑测试再次修改。

## 6. F-65和交付证据应准确区分

### F-65目前证明了什么

`postgres-formal-memory-chain.log`中，F-65的73728字符、301turn、150user确实是实际HTTP admission后从Source读回的断言。停止水位内尾部助手片段遗漏也已做修前失败/修后通过验证。

但是，该脚本中F-65部分在[大Source读取核对:778](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:778)后没有继续为该Source生成候选、逐项审核至正式记忆；41条正式记忆来自另外的40条长场和1条短场。**不能将两组结果合称为73728字F-65完整正式记忆闭环已通过。**

此外，F-DENSE目前通过`_FactQuestionBatchProvider`直接返回草案，[测试替身:293](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_owner_truth_live_long_memory_pipeline.py:293)不经过实际adapter的数量上限和完整请求预算。样本规模达标有进展，但不能据此证明真实模型合同路径下也能完成同样规模。

补证应把已规定的同一长场Source贯穿受控真实adapter/默认Worker/候选/审核/正式记忆，保留超32候选、首中尾与末尾纠正的真值核对；无须真实Provider或手机。若尚未完成，清单应准确标为本地组合未完成，不冒充业务故障或供应商故障。

### 构建和流量报告

- Debug构建中小型`DreamJourney`可执行文件可能只是启动stub；run02/run03该文件hash相同，不足以判断构建旧，也不足以证明业务代码一致。构建日志有本轮Swift编译/链接；补`DreamJourney.debug.dylib`及相应测试bundle指纹，绑定构建日志和结果。
- 3900秒relay验证属于所选采样率/帧大小/封装假设下的合成负载。明确上下行配置和公式，不把假定包头或双向相同采样率的结果称为真实全场精确流量。该报告准确性补证不构成新的候选业务根因。

后续真机标准仍按原规范：当前先物理20分钟，至少30用户回合、15项应保留事实；未来物理至少65分钟、至少120用户回合、60项有效事实，61分钟后补充/纠正；短长场均进入待确认、用户审核、正式记忆及重启回查。现在仅准备，用户主动安排。

## 7. 证据及结束条件

审查目录：`/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-03/evidence/`

- [语义探针](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-03/evidence/semantic-regression-probes.py)、[运行输出](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-03/evidence/semantic-regression-probes.log)
- [复现说明](../../../outputs/2026-09-20-astra-live-long-memory-delivery-review/run-03/evidence/semantic-regression-probes-README.md)
- [负向保持性测试](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-03/evidence/semantic-negative-controls.log)
- [failed Run交接复跑](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-03/evidence/failed-run-handoff-rerun.log)
- [累计源码指纹](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-03/evidence/cumulative-fingerprints.log)

探针全部无真实网络；其中脚本退出0代表反例与对照符合预设断言，并非全部产品场景通过。不要照搬失败期望作为修后绿测，应保留失败证据并将产品测试断言改为正常释义成功。

完成本节语义修复和指定本地补证、受影响回归及最终指纹核对后，再恢复LOCAL_PASS。正常结束本地任务；不自动部署、不操作历史、不commit/push，不因未连接手机而停工。

原[开发设计](../../../02-设计文档/02-架构调整/记忆系统/2026-09-20-长对话分批整理与统一发布/2026-09-20-Astra-Live长对话分批整理与会后统一发布-开发设计.md)和[验收清单](../../../02-设计文档/02-架构调整/记忆系统/2026-09-20-长对话分批整理与统一发布/2026-09-20-Astra-Live长对话分批整理-本地与真机验收清单.md)继续有效。本文件只收敛run03剩余缺口及证据表述，不降低原标准。
