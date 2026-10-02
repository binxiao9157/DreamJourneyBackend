# Live 长对话分批整理 LM/LI 校正执行清单

状态说明：`PASS` 只代表本地合成数据、受控 HTTP、隔离 PostgreSQL、模拟器或编译门禁通过。真实 Provider、真实设备和部署不由本表替代。

## LM-01 至 LM-32

| 编号 | 状态 | 本轮校正后的具体断言与证据 |
|---|---|---|
| LM-01 | PASS | 保留 Astra 的 88 字/12 事实真值清单；默认真实 adapter/parser 在组织饱和后有界细分，12 项全部进入稳定 atom。`test_lm_r01_dense_single_turn_refines_until_all_twelve_facts_are_supported`。 |
| LM-02 | PASS | 短场 Source→候选→审核→Memory/Version→Store 重建完整；隔离 PG formal chain 与候选 UIQA。 |
| LM-03 | PASS | 合成 20 分钟密集场为 120 用户/241 总回合；另以 PG 40 条长场候选验证超过 32 的分页、逐条审核和重建读取。 |
| LM-04 | PASS | 逻辑 65 分钟、150 用户/301 总回合，保留 61 分钟后的纠正；仅属注入时间本地证据。 |
| LM-05 | PASS | 超长单 turn 的 20 个事实按原文区间拆分，父子覆盖不重复。 |
| LM-06 | PASS | `finish_reason=length` 会留下真实元数据并继续有界细分；最小片仍截断时 typed 失败、不假成功。 |
| LM-07 | PASS | 300 独立事实走有界 relation page，调用预算不随全量笛卡尔积增长。 |
| LM-08 | PASS | 跨批 duplicate 折叠，所有 atom 仍有可验证归宿。 |
| LM-09 | PASS | supplement/correction/retraction 改写正文后旧 support 作废，必须重新对原用户证据核验。 |
| LM-10 | PASS | 同实体不同事件及过去/现在偏好不误合并。 |
| LM-11 | PASS | relation 页回执需完整，受控 HTTP typed decode；漏页或不确定阻止冻结。 |
| LM-12 | PASS | B7 纯问题、助手回答、引述及混合问句保持原语义保护。 |
| LM-13 | PASS | typed facts/facets/dimensions/qualifiers/affect 和数值精度保持。 |
| LM-14 | PASS | manifest v2 对每个 atom 要求 final/merged/superseded/retracted 归宿；遗漏、伪替代、悬空和循环均拒绝。 |
| LM-15 | PASS | 关系改写后的新正文、人物、地点、时间和属性重新绑定 evidence range；relation 结果本身不是 support。 |
| LM-16 | PASS | 会中仅私密预整理，Source 封定前无用户可见候选或正式记忆。 |
| LM-17 | PASS | 预整理异常首次进入 retryWait，额度耗尽后 Run/Unit 明确 failed，后续唤醒 idle，不无限持有或领取 lease。 |
| LM-18 | PASS | 尾部不足一批和 partial 均进入 WorkUnit/coverage 台账。 |
| LM-19 | PASS | Source、顺序、输入哈希和 pipeline version 不匹配时旧草稿不可发布。 |
| LM-20 | PASS | 重复唤醒复用同 Run、Unit、ProviderAttempt 和恢复预算。 |
| LM-21 | PASS | 父 extraction job 仅等待 ready/failed 交接，不在领取后重跑整场模型。 |
| LM-22 | PASS | PG 中途提交故障整批回滚；manifest 不完整时 0 manifest/0 candidate 可见。 |
| LM-23 | PASS | 单 Run 最多两个 Provider unit lease；第三个无租约，旧 lease 不能覆盖新 worker。 |
| LM-24 | PASS | 暴露前失败、响应后失败及未知结果分离；既有 start/append/end/ACK/admit 未知写禁止重发。 |
| LM-25 | PASS | 单 Unit 有限恢复、全场共享预算持久化；重建不清零。 |
| LM-26 | PASS | 自适应拆分树、关系分页、最大深度和最小片失败均有界。 |
| LM-27 | PASS | authority epoch、账号和 FeatureGate 变化阻止迟到提交。 |
| LM-28 | PASS | 隔离 PG 可重建 Run、Unit、Attempt、Atom、Relation、Manifest 和失败状态。 |
| LM-29 | PASS | 真实 HTTP admit、默认 Worker、新 pipeline、40+1 候选，经 41 次审核成为 41 条正式记忆；Store 重建后 Source/Memory/Version 可读且命令去重。 |
| LM-30 | PASS | 默认生产装配使用长场 SourceExtractor/Worker；短场及旧 pipeline 回归保持。 |
| LM-31 | PASS | Live 专用大 Source 只由服务端 admit 合同启用，客户端 metadata 不能越权。 |
| LM-32 | PASS | 100k 用户字、200k 总字、2 MiB 和单 turn 边界保持；越界 typed 拒绝。 |

## LI-01 至 LI-10

| 编号 | 状态 | 本轮校正后的具体断言与证据 |
|---|---|---|
| LI-01 | PASS | 同场状态 GET 超过旧 6 次仍可完成；最多 24 次、零恢复业务写。OwnerTruth 531。 |
| LI-02 | PASS | 整体 180 秒只结束当前读取轮次；已确认的 organizing 不倒退为 unknown；后续“核实整理状态”使用新 trace 恢复 pendingReview。真实 Controller→Coordinator→磁盘→FeatureGate→BackendClient→URLProtocol 组合。 |
| LI-03 | PASS | 页面重进、组件销毁重建、跨进程同盘冷启动、旧 round/poll 隔离；B6 UIQA 不启动麦克风、不创建新 capture。 |
| LI-04 | PASS | 网络失败、磁盘失败及后台 typed failure 分离，坐标和原文保留。 |
| LI-05 | PASS | 服务端长 profile 与时长/账号/epoch 保护保持；真实物理 65 分钟仍未执行。 |
| LI-06 | PASS | iOS PCM16→mono 24k WAV 编码与静音判断通过；代理对 encoded bytes/text 双向转发并共享同一 session budget；1 GiB 边界保持。 |
| LI-07 | PASS | canonical outbox、generation、迟到 final 和断网恢复仍绑定原场次；不宣称 SDK 自动续连。 |
| LI-08 | PASS | 长回答各 segment 完成后才结束、主动插话立即终止旧播放并恢复 listening、迟到 finish 不复活；相关音频行为 25/25、租约 5/5。 |
| LI-09 | PASS | 阶段日志仅含白名单 stage、attempt、计数和脱敏 correlation；真实 finish_reason/usage 可取时记录，不伪造。 |
| LI-10 | PASS | 候选 UIQA 显示 typed 详情、批量审核、正式记忆、版本历史和候选移除；40+1 PG 链验证超过 32。 |

## 独立边界

| 项目 | 状态 | 说明 |
|---|---|---|
| 真实 Provider | NOT_RUN | 本轮禁止调用；受控 HTTP 不等于供应商语义和延迟验收。 |
| iPhone 20 分钟 | NOT_RUN | 后续由用户主动安排。 |
| iPhone 65 分钟以上 | NOT_RUN | 本地逻辑时间不能替代物理时长及真实 SDK 顺序。 |
| 部署 | NOT_RUN | 迁移、后端、Worker、开关和 iOS 均未发布。 |
| 历史重处理 | NOT_RUN | 未读取、清理或重放历史场次、生产候选及 Dead Letter。 |

## 证据索引

- R01-R04 及后端回归：`../evidence/backend-affected-final.log`
- PostgreSQL：`../evidence/postgres-formal-memory-chain.log`、`postgres-concurrency-rollback.log`、`postgres-contract-retry.log`
- OwnerTruth：`../evidence/ownertruth-full.xcresult`、`ownertruth-full-summary.json`
- 音频：`../evidence/audio-full.xcresult`、`audio-live-behavior.xcresult`
- UIQA：`../evidence/uiqa-candidate/run-02/`、`../evidence/uiqa-b6-cold-start/run-02/`
- 构建：`../build/simulator-build.log`、`generic-ios-build.log`
