# Live 长对话分批整理：交付核查与剩余修复要求

日期：2026-09-20。核查对象：Sol 的 `run-2026-09-20-01` 本地交付及当前 iOS/Backend 工作区。

## 1. 结论与执行边界

**当前不符合原方案的本地放行要求。建议状态改为 `LOCAL_FAIL / NEEDS_LOCAL_FIX`。**

独立本地探针已复现四项问题：密集事实仍受旧 8 条组织结果限制而失败；跨批改写后缺少原文支持复核；发布台账不能阻止丢弃已识别事实；会中整理异常没有正确收尾。它们是实现缺口，不能用增加测试总数、扩大轮询次数或继续真机试错来替代修复。

本结论不否定已有正文持久化、end/ACK/admit、旧短场正式记忆及音频链的通过记录；也不宣称已在生产复现这四项新反例。探针使用合成输入、受控 HTTP 或本地仓库，未连接真实 Provider、数据库服务或手机。

本轮只核查、生成证据和本文件，未修改产品源码。下一步由 Sol 按原开发设计补齐本地实现与测试。`PROVIDER_NOT_RUN / DEVICE_20M_NOT_RUN / DEVICE_65M_NOT_RUN / DEPLOY_NOT_RUN / HISTORICAL_REPROCESS_NOT_RUN` 继续独立保留。**不因无手机暂停本地开发；真机由用户主动发起。**本文件不是部署、付费模型调用或历史重处理授权。

## 2. 本次核对的材料与可信范围

- [原开发设计](../../../02-设计文档/02-架构调整/记忆系统/2026-09-20-长对话分批整理与统一发布/2026-09-20-Astra-Live长对话分批整理与会后统一发布-开发设计.md)
- [原本地与真机验收清单](../../../02-设计文档/02-架构调整/记忆系统/2026-09-20-长对话分批整理与统一发布/2026-09-20-Astra-Live长对话分批整理-本地与真机验收清单.md)
- [Sol 本地交付报告](../../../outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-01/reports/2026-09-20-DreamJourney-Live长对话分批整理-本地交付报告.md)
- [Sol LM/LI 清单](../../../outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-01/reports/2026-09-20-DreamJourney-Live长对话分批整理-LM-LI执行清单.md)

当前后端 31 个、iOS 19 个交付指纹全部一致。核查读取了定向测试、PostgreSQL 日志、完整后端回归日志、iOS 测试源码及 xcresult。OwnerTruth 530/530 与新增 LI 两项确实通过，但通过的断言不等于验收清单中声称的全部场景。

[指纹复核记录](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-01/evidence/delivery-fingerprint-verification.log)

## 3. 已复现问题与修复要求

### DJ-LIVE-LM-R01：按发言条数分批，仍不能解决一条发言中多个事实的容量问题

**本地事实：**单个用户 turn 仅 88 字，含 12 个独立事实。受控模型返回合法结构的前 8 条，support 明确报告遗漏。真实 DeepSeek adapter 路径只完成 organization、support 两次请求，随后抛出 `candidateExtraction.live.supportValidate.factOmitted`。没有继续细分，也没有完成 12 个事实。

这不是用户只能说 8 次，也不是已证明供应商容量不足；是应用仍把旧的组织上限与按用户 turn 数分批组合使用。用户一次说多个事实时，旧矛盾仍在。

定位：

- [deepseek.py:977](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:977)：组织入口默认上限仍为 8。
- [worker.py:1168](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1168)：不足 4000 字的输入直接保留；后续主要按用户 turn 数分批。
- [worker.py:1341](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1341)：support 失败直接抛出，没有设计要求的有限细分。

修复：按原设计实现原文区间、稳定事实 ID 和有界工作单元。组织结果饱和、length、独立核查发现遗漏时，应能在同 Run、同预算链内进行合法细分。完整维度候选生成与原子事实提取分别限量；不能仅把 8 改成更大的固定数字后宣告完成。最小片仍无法处理时才进入明确的终态失败，保留原文。

必需红绿测试：保留本次 88 字/12 事实反例；同时覆盖一条发言中多事实、少量 turn 但高密度、F-R12、F-DENSE、length 及最小片失败。响应必须经过真实 adapter/parser，真值清单独立于模型输出。正例最终保留全部应保留事实，反例不截尾、不假成功、预算有界。

### DJ-LIVE-LM-R02：跨批补充/纠正产生的新文本，未经独立支持复核

**本地事实：**合成原文只包含“周六去杭州看展”和“和女儿一起”。先前草稿通过独立 support 后，让受控 relation 返回结构合法、但额外加入“购买了一套海景房”的 `resolvedMemory`。提取仍返回 `succeeded`，虚构内容进入 CandidateProposal。实际请求顺序仅为 organization → support → relation。

这是测试“不可信模型结果能否被系统拦住”的受控反例，不是声称真实模型在历史场次说过这句话。

定位：

- [worker.py:1885](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1885)：直接采纳关系结果中的新正文。
- [worker.py:1026](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1026)：关系处理后直接构建并冻结 manifest。
- [deepseek.py:1595](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/deepseek.py:1595)：replacement 的结构合法不等于事实有原文支持。

修复：正文、属性、时间地点或关系发生实质变化，旧支持证明立即失效。新候选须按原设计小批回查原始用户证据；关系筛查结果不能直接充当支持证明。补充、纠正、撤回均保留来源及版本绑定；任何新增无证据断言必须阻止发布，不能靠再次请求模型“自称支持”代替证据绑定。

必需红绿测试：合并中新增虚构事实、偷换日期/人物/地点、加强语气或偏好、错误撤回；同一真实 HTTP/parser/台账链应拦截。合法跨批补充与尾部纠正应完整保留，同一事实不重复生成候选。

### DJ-LIVE-LM-R03：覆盖检查仍按 turn 集合，漏掉已识别事实也能通过发布门禁

**本地事实：**调用真实 InMemoryLiveLongMemoryRepository，持久化 12 个 active atoms 和 completed unit；最终只交给 manifest 1 条 memory。其余 11 个 atom 没有合法去向，仍可冻结为 `readyToPublish`。此探针证明发布门禁误放行，不代表执行了真实数据库候选写入。

定位：

- [AtomRecord:161](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_long_memory.py:161)：当前主要记录 turn 索引，缺少设计要求的原文证据区间。
- [manifest:1426](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_long_memory.py:1426)：只汇总 atom 的 `sourceTurnIndices`。
- [manifest:1442](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_long_memory.py:1442)：直接把传入 memories 组成最终 items，未证明每个 atom 的去向。

修复：检查每个应保留 atom 到最终 item 的映射，或者有证据的 merged/superseded/retracted 终结关系。每个 item 绑定当前版本的支持证明与原文区间；检查悬空引用、循环、伪替代及遗漏。一个 turn 被引用过，不能代表其内所有事实被保存。区分“原文区间审阅完整”与“已识别事实归宿完整”，两道门禁都必须存在。

必需红绿测试：从 12 个已完成 atom 中故意删去一个及十一个；伪造 superseded、缺失 replacement、关系循环；全部阻止冻结/发布。合法重复、纠正、撤回的结果无需按 12 个候选计数，但全部 atom 必须有可验证去向。相同断言进入隔离 PostgreSQL 发布事务，验证失败零候选可见。

### DJ-LIVE-LM-R04：预整理异常处理再次异常，耗尽重试后仍处于运行状态

**本地事实：**实际预整理器和 Runtime 的 `_preorganize_live_unit_once` 方法，配合本地仓库、受控时钟和失败 Provider 替身：

| 唤醒 | 受控 Provider 累计调用 | 对外异常 | Run / Unit |
|---|---:|---|---|
| 首次 | 1 | CandidateExtractionFailure 没有 reason 属性 | organizing / running |
| 租约到期后恢复 | 2 | 同上 | organizing / running |
| 再次到期，恢复额度已耗尽 | 2 | 同上 | organizing / running |
| 第四次唤醒 | 2 | 同上 | organizing / running |

本探针只替换时钟及 Provider 失败边界，没有启动完整 CLI 或 PostgreSQL。耗尽后没有第三次模型调用是正确的预算保护；错误在于无法进入可恢复等待或明确终态。

定位：

- [failure 类型:147](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:147)：提供 code/stage 等字段，没有 reason。
- [Runtime 异常出口:2237](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:2237)：访问 `failure.reason`，并且没有持久化 Unit/Run 的失败收尾。
- [PG 工作单元领取:938](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_long_memory.py:938)：运行中租约过期的 unit 可再次被领取。
- [Source job 就绪条件:719](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/lease_repository.py:719)：存在未完成 atomExtraction unit 且 Run 未失败/发布时，父 job 被排除。因此上述状态若出现在 PG，有长期等待风险；须以 PG 集成红测验证。

修复不能止于把 reason 改成 code。需要在当前租约/epoch 下持久化分类、有限恢复、终态及对应唤醒；预算耗尽必须结束可执行状态。Run 的 ready/failure 到父 job 的交接须按原设计完成，父 job 仅发布冻结结果，不在领取后继续承担整场模型整理。失败或失权的 Run 不得不断领取新单元。原正文、恢复依据和已完成片仍保留。

必需红绿测试：首次合同失败、恢复成功、连续失败耗尽、网络未知结果、迟到旧租约、Worker 重启；运行真实默认 Runtime 与隔离 PG，证明预算不重置、无无限领取、终态可读取、不会只留下 organizing。当前四次唤醒探针仅是起点，不能替代这些集成测试。

## 4. 交付清单中需要补证或校正的项目

| 交付声明 | 本次核查 | 要求 |
|---|---|---|
| LM-01 已证明原 12 事实容量问题修复 | 测试实际是 12 个用户 turn；fake 每 turn 一事实，与原 F-R12 77/39 夹具不同；R01 仍失败 | 恢复原夹具与真实 adapter；增加一条 turn 多事实，不从模型结果倒推真值 |
| 密集场、65 分钟全部通过 | 部分测试直接调用 extractor；120 turn 密集样本只有120事实，另一个300事实测试是300 turn；elapsedSeconds 元数据没有驱动真实时钟/认证链 | 按原 F-DENSE、F-65 单独满足规模、300原子、真实admit、状态装配、预算和跨期授权断言 |
| PG 长场候选→正式记忆完整闭环 | 日志中的长场为6个用户 turn、5候选，短场1候选；只能证明该夹具闭环 | 新 pipeline 开关、默认 Worker、Run/Unit 证据明确；使用>32候选的新分批长场走真实admit、分页、审核、正式Memory/Version及重建 |
| LI-01/02 真实控制器、磁盘、网络组合通过 | xcresult 仅两个 Coordinator + Deferred client 测试；第一项瞬时触发24次 pending 后耗尽，未验证7～120秒后完成；第二项没有真实磁盘重建 | 补 Controller→Coordinator→磁盘→FeatureGate→BackendClient，受控时钟和HTTP，迟到成功/超时/重进/冷启动/旧轮隔离 |
| 180 秒后保留已知阶段 | 当前 deadline 无条件设 statusUnknown；未保留已知 organizing | 按原产品状态语义保留服务端已知阶段；轮询到期不代表业务失败 |
| LI-06 65分钟真实编码流量 | 使用 b'a' 的1MiB块累计到1GiB，只证明计数边界 | 补音频格式、编码及双向封装流量计算和受控 relay；真实65分钟仍另列 NOT_RUN |
| LI-08 Audio 5/5 证明长回答/打断/恢复 | 结果包只有5个音频租约模型测试 | 补已存在的长回答、主动打断、恢复监听的受影响组合验收，真实声音仍另验 |
| 完整回归只有基线异常 | 本次日志为2642项、7 failures、46 errors；46项含API池未打开 | 在相同环境以未改版本证明基线，或修复测试装配后重新跑受影响验证；不能未经对照把46项认作已知历史失败 |

进一步的代码缺口应在以上修复中一并落实：实际预留输入目前采用部分 stage/turns 数据，并非完整 HTTP prompt；成功回执把 finishReason 写死为 stop、usage 留空，不能当实际模型观测值。按原预算与诊断要求记录完整序列化请求的保守估计、真实可取得的响应元信息以及不可取得的字段，不能伪造。

这些校正不要求重写已通过的保存和音频能力，也不要求现在做真实 Provider 或真机测试。测试矩阵必须保留原 LM/LI 编号和含义，不能把“有限拆分后完成”换成“会报错”仍标同一项 PASS。

## 5. 已确认的进展，应保留

- 新 Run/Unit/预算、会中私密预整理及跨批关系处理已有实际源码，不是只改页面文案。
- 180秒、24次GET及有界退避接入了真实 iOS 业务路径。
- Live 专用长 profile 在服务端受开关及用途约束，原默认 profile 保持；本地3599/3601/3900/7201边界有测试。
- 当前 OwnerTruth 530项确实通过，包含既有持久化/认证恢复/账号隔离场景；源码指纹匹配。
- 没有以本地结果冒充 Provider/20分钟真机/65分钟真机/部署完成，这些边界声明正确。

Sol 修改共享的 Worker、支持校验、候选发布或状态读取时，将受影响的旧 BE/IR、70/48恢复、短场候选与正式记忆、B8/S01-08、canonical/partial、音频场景明确列入回归。不要为让新测试变绿降低旧断言。

## 6. 修复顺序和本地结束条件

1. 先把 R01～R04 四个反例纳入真实路径测试，保存当前失败证据；原设计及原验收矩阵继续有效。
2. R01+R03 按同一事实/证据台账完善有限细分和发布完整性；R02 补变更后独立核查；R04 补调度/失败收尾。可以内部按依赖拆提交草稿，但本轮不 commit/push。
3. 补新pipeline的真实 Source/admit/默认Worker/PG 长场正式记忆闭环，以及 iOS 慢完成和磁盘恢复组合；使用本地合成数据与受控HTTP。
4. 按原矩阵逐项报告真实 PASS/FAIL/NOT_RUN。四项反例修后通过、缺失组合补齐、受影响回归通过、最终源码与结果指纹一致，才可恢复 LOCAL_PASS。
5. 正常结束本地任务，交付报告和后续 Provider/20分钟/65分钟验收清单。不要检测或等待手机，不自动进入下一阶段。

## 7. 可复现证据

证据目录：`/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-01/evidence/`

- [R01～R03 脚本](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-01/evidence/semantic-probes.py)
- [R01～R03 输出](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-01/evidence/semantic-probes.log)
- [R01～R03 复现说明](../../../outputs/2026-09-20-astra-live-long-memory-delivery-review/run-01/evidence/semantic-probes-README.md)
- [R04 脚本](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-01/evidence/worker-failure-probe.py)
- [R04 输出](/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-01/evidence/worker-failure-probe.log)

运行 R04 的命令（本地、无网络）：

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python /Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-01/evidence/worker-failure-probe.py
```

这些脚本用于稳定保留本次审查反例，不是可以替代原验收矩阵的测试替身。产品修复后需将相同断言融入正常测试，并补默认生产装配和隔离PG验证。
