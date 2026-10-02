# DreamJourney Live 采集中断修复本地交付报告

## 1. 结论

状态：`LOCAL_PASS / PROVIDER_NOT_RUN / DEVICE_SHORT_NOT_RUN / DEVICE_20M_NOT_RUN / DEVICE_65M_NOT_RUN / DEPLOY_NOT_RUN / HISTORICAL_REPROCESS_NOT_RUN`

本轮按 D1-D4 完成 iOS 局部修复，并在 CAP-15 真实客户端桥接中补出一处后端 Live Source 绑定缺陷。所有必需的本地场景、隔离 PostgreSQL、OwnerTruth 回归、音频保持性及两种无签名构建均通过。

本地通过不等于 run04 现场缺陷已经关闭。历史现场缺少可唯一归因的首错记录，不能宣称某一个回调就是当时唯一首因；真实 SpeechEngine SDK 回调顺序、真实 Provider、iPhone、部署和历史重处理均未执行。

## 2. 基线与边界

- iOS：`11d0d0051b9be3cce57822dd059472d1e2536866`，分支 `feature/prd-stitch-ui-adaptation`，保留全部未提交修改。
- Backend：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`，分支 `main`，保留全部未提交修改。
- 未执行 reset/clean、commit、push、部署、真机安装、真实 Provider、生产或历史数据操作。
- run04 已证实：33 个 Owner 回合中仅前 13 个进入 Outbox/Backend，后续 close/end/ACK/admit 缺失；SDK 回调与预整理曾继续工作。
- 本地组合复现：同 canonical identity 的差异正文会令 Coordinator 进入 unavailable，Controller 随后释放活动采集所有权，后续正文与 stop 丢失。

## 3. 已确认缺陷与修改

### D1 共享生产记忆事件适配器

文件：

- `DreamJourney/Sources/Services/DialogEngineManager.swift`
- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`

行为变化：

- 原始消息入口冻结账号/场次/generation/question 或 reply/角色/来源/finality/callback ordinal，再异步交接；停止或换场后不重新读取 current identity。
- ASR observation 与 ASREnded sealing 分离；final 先到、boundary 先到、无 boundary 由下一 question/stop 收尾均按入口顺序处理。
- QueryConfirmed 只确认已登记 text query，不再把 ack 字段当语音第二份终稿。
- Chat 和 TTS 使用独立 assistant stream；Chat 模式仅 Chat 全文成为记忆正文，TTS 保持播放但不重复写正文。
- 原生 raw-code classifier 与本地测试共用同一生产 dispatcher；覆盖 3012/3013/3014/3021/3008/3009/3011/3015/3016。真实 SDK 顺序仍为 `DEVICE_NOT_RUN`。

### D2 观察、封存、不可变投递与冲突

文件：`DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`

行为变化：

- 新增 `observeOnly`/`observeAndSeal`；观察版本、入口 ordinal、seal/boundary、selected observation、issue 与 delivery 分开持久化。
- 封存前合法 final 修订可选择更新后的完整快照；封存后 delivery/command/hash 保持不可变。
- 严格句号等价仅允许末尾一个中文 `。`；问号、数字、内部标点、否定词等差异仍是冲突。
- 实质冲突持久化为 typed issue，不改原命令、不生成伪回合，同时允许后续合法回合继续落盘；完整 end 被阻止，close intent 与恢复坐标保留。
- 旧 envelope 保持兼容；新增观察或 issue 会进入 `recoverableProductSessionID` 判断。

### D3 生命周期与停止屏障

文件：`DreamJourney/Sources/Modules/Echo/EchoViewController.swift`

行为变化：

- Controller 不再因一次 unavailable 文案释放活动 Coordinator；Coordinator 由场次生命周期与安全移交决定释放。
- 每个 observation/boundary 使用独立 handoff identity；stop 冻结 ingress watermark 并等待水位内逐项处理，不以“已入队”冒充“已落盘”。
- `finish()` 按 stopRequested/completed 幂等推进，不因 capture 已关闭提前 return。
- 真实冲突仍可保存后续回合和 close intent，但不会假报整场完整或发送不完整 end。
- 只读队列排空后的临时状态不再抢先结束采集；旧场、旧账号和迟到回调不能释放新场所有权。

### D4 磁盘异常与首错诊断

文件：

- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
- `DreamJourney/Sources/Modules/Echo/EchoViewController.swift`

行为变化：

- 区分 register/body/dispatch/201 confirmation/close intent/watermark/读取/解码/原子替换失败。
- 相同本地操作允许一次读回核实后的有界恢复；不把本地重试扩大成 start/append/end/ACK/admit POST。
- 临时故障缓冲上限为 128 个操作或 4 MiB；去重并优先保留完整 final/member。溢出后即使磁盘恢复也保留 coverage gap，阻止完整成功。
- 每场独立固定首个关键失败；10,000 个后续高频事件、重建和换场不能覆盖。诊断仅含安全枚举、计数、ordinal 和随机关联，不含正文、凭据或原始身份。

### CAP-15 发现的后端缺陷

文件：

- `app/domain/owner_truth/interview_candidate_proposal.py`
- `app/services/owner_truth_interview_candidate_proposal.py`

真实客户端 start/messages/end/ACK/admit 后，Live Source 绑定错误读取了不存在的 `OwnerTruthCommandContext.authority_epoch`，会在已成功 admission 后阻断 Worker。修复为：

- `OwnerTruthInterviewCandidateProposalPreparation` 显式携带并校验非负 `authority_epoch`。
- InMemory/PostgreSQL prepare 从 review batch 取得同一 authority epoch。
- `LiveLongMemoryRunIdentity` 与 `bind_source` 使用冻结 preparation 值，不借用当前上下文或伪造默认值。

该缺陷是本地 CAP-15 确认的真实缺陷，但不能反推为 run04 现场唯一首因。

## 4. 红绿证据

| 场景 | 修前红证据 | 修后绿证据 |
|---|---|---|
| 冲突关闭整场、后续及 stop 丢失 | `red/cap06-coordinator-conflict-red.log` | `green/cap06-conflict-green.log` |
| 磁盘恢复未补原操作 | `red/cap08-recovered-disk-red.xcresult` | `green/cap08-disk-recovery-bounded.xcresult` |
| 全量旧夹具漏 ASREnded 边界 | `red/ownertruth-full-pre-fixture-red.xcresult` | `green/ownertruth-full-two-regressions-green.xcresult` |
| CAP-15 客户端短场 watermark | `red/cap15-client-bridge-short-watermark-red.xcresult` | `green/cap15-client-bridge-export.xcresult` |
| 后端缺失 authority epoch | `red/cap15-client-bridge-authority-epoch-red.log` | `green/cap15-client-to-formal-postgres.log` |
| 预整理夹具漏提事实 | `red/cap15-client-bridge-preorganization-diagnostic.log` | 同上；修正夹具为每个 Owner 回合提供受控事实，生产漏提门禁未放宽 |

## 5. CAP-01 至 CAP-15

| 编号 | 状态 | 核心证据 |
|---|---|---|
| CAP-01 | PASS | `green/cap01-cap08-focused-v5.xcresult`：相同、句号等价、反例、封存前修订和封存后不可变 |
| CAP-02 | PASS | `green/cap02-cap05-router-v2.xcresult`、最终全量：final A→B→ASREnded 仅投递 B |
| CAP-03 | PASS | `green/cap03-cap05-source-isolation.xcresult`：voice 与 QueryConfirmed 隔离，text ack 保持 |
| CAP-04 | PASS | `green/cap04-boundary-before-final.xcresult`、`green/cap04-cap09-lifecycle-v2.xcresult` |
| CAP-05 | PASS | `green/cap03-cap05-source-isolation.xcresult`：Chat 全文与 TTS 播放流隔离 |
| CAP-06 | PASS | `green/cap06-conflict-green.log`：冲突后至少 10 回合与 close intent 仍保存 |
| CAP-07 | PASS | `green/cap01-cap08-focused-v5.xcresult`：六个本地写阶段逐点故障与原子恢复 |
| CAP-08 | PASS | `green/cap08-disk-recovery-bounded.xcresult`：持续失败、损坏、128/4MiB 上限、恢复后仍保留 overflow gap |
| CAP-09 | PASS | `green/cap09-stop-barrier.xcresult`、`green/cap04-cap09-lifecycle-v2.xcresult`：冻结逐包排空与崩溃重建 |
| CAP-10 | PASS | 最终 OwnerTruth 全量中的页面重建、冷启动、恢复坐标和旧回调隔离 |
| CAP-11 | PASS | `regression/cap-keep-core.xcresult`：delivery-status 排空后下一轮及 stop 正常 |
| CAP-12 | PASS | 最终全量：多 TTL、401、deny、断网、同账号恢复、切账号和迟到回调 |
| CAP-13 | PASS | `green/cap13-first-failure-retention.xcresult`：首错经 10,000 事件和重建仍在 |
| CAP-14 | PASS | `green/cap14-logical-long-sessions.xcresult`：逻辑 20m/100 Owner 与 65m/150 Owner |
| CAP-15 | PASS | `green/cap15-client-to-formal-postgres.log`：真实客户端 6+150 问、312 消息、2 个动态批次、156 候选→审核→正式记忆→Store 重建；无 seeded acknowledged batch |

## 6. KEEP 回归

| 编号 | 状态 | 证据 |
|---|---|---|
| KEEP-01 短场补充 | PASS | 最终 OwnerTruth 全量、CAP-15 6 问全链 |
| KEEP-02 长场分批 | PASS | CAP-14、Backend long-memory 26 项、CAP-15 150 问全链 |
| KEEP-03 跨批重复/补充/纠正/撤回 | PASS | Backend candidate worker 与 long-memory 回归 |
| KEEP-04 B7 事实证据 | PASS | Backend candidate worker 64 项内 B7 语义场景；最终合并 119 项 |
| KEEP-05 审核→正式记忆 | PASS | CAP-15 156 条逐条 fresh Proposal/CAS/activation/rebuild |
| KEEP-06 音频/打断/租约 | PASS | `regression/audio-owner-lease.xcresult` 5/5；`backend-realtime-audio.log` 23/23 |
| KEEP-07 strict final/非ASR/partial | PASS | CAP-02/03/04/05 与最终全量 |
| KEEP-08 TTL/认证/账号/旧 poll | PASS | 最终全量 547/547；B6 崩溃项单跑 1/1 后全量稳定重跑 |
| KEEP-09 未知写/预算 | PASS | `regression/cap-keep-core.xcresult` 与 Backend retry PostgreSQL smoke |
| KEEP-10 当前场状态/坐标 | PASS | CAP-06/09/10 与最终全量 |

## 7. 最终测试与构建

- iOS OwnerTruth：`regression/ownertruth-full-final3.xcresult`，547/547 PASS。
- 一次全量运行发生 Simulator 测试进程 crash，证据为 `ownertruth-full-final2.xcresult`；对应 B6 用例单跑 1/1 PASS，随后全量 547/547 PASS，未修改业务预期掩盖。
- iOS 音频租约：`regression/audio-owner-lease.xcresult`，5/5 PASS。
- Backend 受影响合并回归：`regression/backend-affected-final.log`，119/119 PASS。
- 隔离 PostgreSQL：正式链、并发、事务重试均 PASS；CAP-15 bridge 使用 schema head `0122`，156 条正式记忆重建后保持。
- Simulator 无签名构建：`builds/ios-simulator-build-final.log`，BUILD SUCCEEDED。
- 通用 iOS 设备目标无签名构建：`builds/ios-generic-device-build-final.log`，BUILD SUCCEEDED。
- 两端 `git diff --check`：PASS。
- 日志脱敏扫描：`reports/log-redaction-scan.txt`，PASS。
- 指纹：`reports/source-build-fingerprints.txt`。

## 8. 未运行与残余风险

- `PROVIDER_NOT_RUN`：未调用真实模型；受控 HTTP 验证合同、解码、漏提和 finish_reason，不替代供应商稳定性。
- `DEVICE_*_NOT_RUN`：未连接/安装/启动 iPhone；真实 SpeechEngine SDK 的事件编号与物理回调顺序只能由真机验收关闭。
- `DEPLOY_NOT_RUN`：后端与 iOS 均未部署。
- `HISTORICAL_REPROCESS_NOT_RUN`：未读取、清理、补发或重放 run04 历史场次。
- run04 首次触发没有完整首错证据，仍不能唯一判断当时最先来自 Owner 冲突、assistant 来源冲突、磁盘异常或其组合。
- 新 envelope 已向后兼容读取；若回滚到旧 writer，旧版本可能不知道新 observation/issue 字段，因此不能在已有新格式数据后盲目降级写入。

## 9. 发布准备、顺序与回退

建议授权后的顺序：

1. 备份并应用既有 `0122` migration，先发布 Backend API/Worker；验证 candidate Worker readiness、Source binding 和状态只读接口。
2. 发布 iOS 修复版本；先短场，再物理 20 分钟，最后 65 分钟以上。
3. 观察仅含安全枚举的首错与 coverage 指标；禁止自动处理历史失败场。

局部回退：

- iOS 可按 `DialogEngineManager` 共享适配器、`OwnerTruthContracts` observation/seal、`EchoViewController` 所有权/缓冲三个代码块回退，但必须保留新 envelope 读取兼容，不能删除恢复坐标。
- Backend `authority_epoch` 回退只可在长场管线关闭且无新 admission 时进行；否则会恢复已确认的 Source binding 崩溃。
- 不回退/删除 migration、Source、候选、正式记忆或审计数据；不通过重放未知写来恢复表面状态。
