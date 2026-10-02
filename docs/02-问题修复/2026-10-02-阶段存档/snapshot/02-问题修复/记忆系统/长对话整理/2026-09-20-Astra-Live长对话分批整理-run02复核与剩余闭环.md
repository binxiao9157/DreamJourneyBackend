# Live 长对话分批整理 run-02：复核与剩余闭环

日期：2026-09-20。对象：Sol `run-2026-09-20-02` 交付及指纹对应的当前工作区。

## 1. 结论

**已有实质修复，但仍不满足整体 LOCAL_PASS。当前应标 LOCAL_FAIL / NEEDS_LOCAL_FIX。**

独立重跑本次 R01～R04 六项定向测试，6/6 通过；新增的真实模型 adapter 对照也确认，原 12 个同 turn 事实已能拆成小片、完整生成 12 条候选。原来的无证据新增内容拦截、缺 atom 发布门禁及预整理异常收尾都有实现进展，不能再说它们完全没有修改。

但是，正常重复、合法补充、同一发言中事实与问题混合，仍可导致整场提取失败；预整理终态与关闭后的父任务交接仍能再次调用模型。另有 iOS 超时出口及自动慢完成证据未闭合。以下是本次限定的剩余修复要求，不替换原方案、不要求推倒已经通过的保存/音频链。

本次只读产品代码；仅在审查目录新增本地合成探针、日志和本文件。未调用真实 Provider、连接手机、访问生产、启动 PostgreSQL、修改产品源码、部署、处理历史或 commit/push。所有后续真实 Provider、20/65分钟真机、部署、历史重处理仍为 NOT_RUN；**手机不构成本地任务依赖，真机继续由用户主动发起。**

## 2. 已确认、必须保留的进展

- 后端31个、iOS19个交付源码指纹全部匹配当前文件；本次结论针对当前源码。
- R01密集单turn、length细分、最小片失败；R02无证据合并拒绝；R03漏atom拒绝；R04两次失败后预整理停止，六项原定向测试均复跑通过。
- 隔离PG日志确实记录40条长场候选、1条短场候选，41条审核进入正式记忆并重建回查。这个实际链条的通过应保留，但其装配边界见第5节。
- OwnerTruth结果包确为531/531；音频行为25/25包含长回答分段完成、主动打断、恢复监听、迟到播放及PCM编码；租约5/5。
- 新增真实Controller→Coordinator→临时磁盘→FeatureGate→BackendClient→URLProtocol测试，验证了deadline先执行时保留organizing，人工核实后pendingReview、坐标保留、零恢复业务POST。
- B6跨进程UIQA记录不同PID、同workflow、同盘恢复、只读GET、不启麦/不创建新capture。

## 3. 四项已复现的剩余后端问题

### R03-B：正常重复合并后，支持证明被清除却没有重新生成

**反例：**前8次用户发言各说一个事实，第9次重复第1个事实。两批组织和独立支持核验均完成，生成9个atom；精确去重后本应得到8份最终事实，但提取失败：`publication item lacks current atom support binding`。

**路径：**[精确去重:1898](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1898)调用证据合并；[合并处理:2580](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:2580)清除`_supportProofHash`；[重验条件:1151](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1151)只看后续relation阶段的`relations_changed`。精确去重的修改没有进入该判定，去重后只剩一条尤其容易遗漏。

**修复：**精确重复、语义合并、补充、纠正统一管理证明有效性。允许可验证地合并未改变命题的原支持证明，或触发有界重新复核；不得仅填任意hash或取消manifest检查。最终一份事实绑定全部有效来源和atom。

**验收：**同批重复、跨批首尾重复、重复后再补充，以及没有重复的保持性对照；真实adapter全链最终只发布一份事实，全部atom有归宿。将此正常成功场景加入原LM-08，不只测伪造内容被拒绝。

### R02-B：补充后的分页复核又把完整原发言交给只有4条草案的一页

**反例：**同一turn的12个事实已完整细分、得到12个atom；其中“在杭州工作”与“在杭州的图书馆工作”进行合法补充合并。随后第一页support只包含4条草案，却再次看到该turn的全部12个事实，于是正确报告`factOmitted`，整场失败。

**路径：**[新候选复核:2093](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:2093)按4个memory分批，但`evidence_turns`仅用turnIndex挑出完整原turn，没有按原子事实/证据片段确定本页必须覆盖的内容；继续使用要求覆盖输入中全部事实的旧support合同。

**修复：**区分“本页最终候选的原文支持验证”和“全场事实覆盖”。本页明确待验证atom ID、证据范围及只供理解的上下文；完整性由全场台账继续保证。可以新增明确的最终候选支持合同或严格划定ownership，不能清空omitted、把真实事实标成query、改用有损摘要或扩大固定上限绕过。

**验收：**12事实加合法补充应成功；无证据购房仍拒绝；长turn多页、跨批纠正/撤回、一个范围支持多个事实、上下文中含其他应保留事实的情况均覆盖。必须验证合法输入能完成与非法新增不能发布这两个方向。

### R01-B：同一turn分成事实片与问题片后，覆盖台账自相矛盾

**反例：**一段话中包含9个事实和9个纯问题。细分后的事实片、问题片均正确处理，9个atom全部生成，但完成记录同时写入`requiredUserTurnIndices=[1]`和`excludedUserTurnIndices=[1]`，manifest以`fact-bearing Source turn cannot be excluded`拒绝整场。

**路径：**[细分结果合并:1725](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1725)将各子片的required/excluded索引直接取并集。子片共享原turnIndex，因此一个turn局部是问题被误当成整条turn排除。

**修复：**使用原文区间/子片稳定ID承载覆盖与排除；投影回turn时，局部问题不能排除同turn的事实。检查原文分片覆盖和已识别atom去向，而不是只删除集合交集来变绿；保留B7纯问题与引用、助手内容的现有边界。

**验收：**事实→问题、问题→事实、含事实的问句、引述、长句边界及Unicode片段；所有事实保留，问题不凭空变候选，精确来源可回查。同一组真值同时走直接提取和会中预整理后关闭交接。

### R04-B：预整理已终态失败，父Source任务仍可重新调用模型

**反例：**前两次预整理失败后Run/Unit为failed；第三次预整理唤醒idle，Provider累计2次。这部分已经修好。但紧接着让同Run、同Source进入最终extract路径，模型调用变成第3次，`recoveryRequestCount`仍为1。

**路径：**

- [父job领取条件:719](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/lease_repository.py:719)允许failed Run交接，但也会在仅atom单元完成、尚无冻结manifest时放行。
- [父任务提取入口:2999](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:2999)仍调用模型extract路径，没有把failed Run转为对应终态的专门分支。
- [extract:949](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:949)绑定Run后未拒绝failed；failed单元又进入组织，父任务首次调用没有retry_context，作为正常调用预留。
- [PG预算领取:1137](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_long_memory.py:1137)未将Run/Unit终态作为不可调用条件。

**证据边界：**探针使用真实预整理器、实际extract与本地仓库，确定复现第3次调用；父Runtime及PG领取路径由源码核对。本轮没有把该组合伪称为已运行PG红测。现有PG失败脚本只到第三次预整理idle，没有继续运行父任务。

**修复：**按原设计完成整个交接：未就绪Run不消耗父job attempt；readyToPublish只读取不可变manifest并事务发布；failed/cancelled只能完成相应终态及可读取回执，不重新组织，不重新领业务/模型预算。后端接口、仓库预算边界和默认Worker都要防止绕过，不能只在测试用提取器外面加条件。

**验收：**将“预整理两次失败→Source封存/admit→父job处理→Worker重启再唤醒”作为一条组合测试，Provider最终仍恰好2次、无候选、终态可读。另测正常完成交接、关系工作仍未完成时不提前领父job、发布事务失败后只重试同manifest且零新增Provider。使用默认Runtime与隔离PG验证。

## 4. iOS仍需关闭的两处要求

### LI-02：超时出口没有统一

[deadline处理:4260](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:4260)已保留已知状态，但[逾期poll:3921](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:3921)仍直接变为statusUnknown(timeout)，[轮询调度:4122](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift:4122)仍直接变为statusUnknown(budgetExhausted)。迟到GET/poll先于deadline执行时，finishRound会取消deadline，已知organizing仍可能被覆盖。

这是源码确认的遗漏路径；本轮没有另起模拟器运行新红测。Sol应统一“结束观察但保留已知服务端阶段”的出口，区分真正后台失败与读取预算到期。补deadline先执行、逾期poll先执行、迟到organizing响应先执行三种顺序；旧轮不能取消新轮，坐标保留，零新增业务POST。

### LI-01：超过第6次GET后的自动完成仍缺证据

原[6991测试](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:6991)仍是fake Coordinator瞬时执行24次pending后耗尽；新增[7114真实组合](/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift:7114)是第1次organizing→180秒停止→人工核实第2次pendingReview。

这两项都通过，但没有验证原要求的“第7次以后、7～120秒内自动完成”。在新增真实组合上使用受控时钟和退避，补自动收到同场reviewReady、当前页面正确显示、磁盘坐标归属正确、无人工核实和无POST的场景。

## 5. 测试装配和清单必须如实补齐

1. **40+1 PG闭环不能叫完整默认装配验证。**[formal脚本:344](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:344)注入直接返回distinct的关系审核器；[脚本:625](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py:625)注入extractor；[Runtime构造:2810](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:2810)在该分支不创建`_live_preorganizer`。PG的正式记忆事务结果有效，但会中预整理、默认关系adapter、终态交接未被这条链覆盖。保留现有测试，补默认Runtime，仅在HTTP/时钟边界控制，实际先形成私密Run再关闭admit。
2. **LM-01/03/04不能变更原验收含义。**88字反例是补充，不替代F-R12的77总/39用户turn；120用户turn并不自动等于原要求的300个事实原子；elapsedSeconds元数据不等于真实认证/时钟装配。按原F-DENSE/F-65和大Source真实admit路径逐项给证据，不以40候选PG结果替代全部密集输入、预算和跨期授权要求。
3. **LI-06仍缺3900秒负载证明。**新的relay测试仅双向短帧和done文本，PCM/WAV编码测试与1GiB计数边界分别有效，但不等于65分钟负载。按实际上下行格式、采样率、封装和累计开销推导3900秒流量，并执行有界受控relay；无需等待物理65分钟或手机。
4. **恢复后续真机清单原标准。**当前先测物理20分钟，至少30用户回合、15个应保留事实；未来物理至少65分钟、至少120用户回合、60个有效事实，包含61分钟后的补充与纠正。run-02报告“65分钟至少五项事实”不能替代原标准。短长场均保留候选→用户审核→正式Memory/Version→重启回查；现在只准备，用户另行发起。
5. 受影响回归177项、PG和构建的通过保留；未跑仓库全量如实注明，不用之前46个pool错误冒充本次绿测或断言均是历史基线。本次局部验收结论由实际场景决定，而不是总数。

## 6. Sol后续工作的本地结束条件

先保存本文件四个后端反例和iOS顺序反例的红测，按上述顺序完成最小修复；保留已通过的六项定向测试、40+1正式记忆链、531项中受影响场景及音频25项/租约5项。对共享链补原BE/IR、B7、B8、70/48认证恢复、canonical/partial及未知业务写保护，不重写未受影响模块。

以上反例修后通过，默认装配PG、规定本地压力夹具和iOS自动慢完成补齐，最终指纹对应结果后才恢复LOCAL_PASS。正常结束本地任务，Provider/真机/部署/历史处理独立保留NOT_RUN，不检测或等待用户手机。

原规范继续有效：[开发设计](../../../02-设计文档/02-架构调整/记忆系统/2026-09-20-长对话分批整理与统一发布/2026-09-20-Astra-Live长对话分批整理与会后统一发布-开发设计.md)、[原验收清单](../../../02-设计文档/02-架构调整/记忆系统/2026-09-20-长对话分批整理与统一发布/2026-09-20-Astra-Live长对话分批整理-本地与真机验收清单.md)。本文件补充具体漏掉的路径，不授权删除完整性/支持校验、扩大未知重试或简化产品语义。

## 7. 本轮独立证据

目录：`/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-02/evidence/`

- [指纹复核](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-02/evidence/fingerprints.log)
- [原R01～R04六项复跑](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-02/evidence/r01-r04-targeted-rerun.log)
- [正常语义组合探针脚本](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-02/evidence/semantic-regression-probes.py)、[输出](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-02/evidence/semantic-regression-probes.log)
- [失败Run交接探针脚本](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-02/evidence/failed-run-handoff-probe.py)、[输出](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-02/evidence/failed-run-handoff-probe.log)

语义探针使用真实DeepSeek adapter及完整响应schema，MockTransport根据当前请求的原文片段自适应返回；包含12事实成功对照，避免将旧脚本固定响应与新拆分不匹配误判为产品错误。正常语义的三个失败是确定性合成反例，不是声称供应商真实响应或用户历史场次具有相同内容。脚本用产品`.venv/bin/python`执行，设置`PYTHONDONTWRITEBYTECODE=1`，不需要密钥、网络、手机或数据库。
