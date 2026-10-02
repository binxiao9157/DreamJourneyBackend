# DreamJourney Live 长对话分批整理 run-02 本地交付报告

日期：2026-09-20\
交付目录：`/Users/gaominge/Documents/liftora/outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-02/`

## 1. 最终状态

`LOCAL_PASS / PROVIDER_NOT_RUN / DEVICE_20M_NOT_RUN / DEVICE_65M_NOT_RUN / DEPLOY_NOT_RUN / HISTORICAL_REPROCESS_NOT_RUN`

`run-2026-09-20-01` 的 `LOCAL_PASS` 被 Astra 核查证据否定；本报告只基于本轮修复后的源码和 `run-02` 原始证据恢复 `LOCAL_PASS`。未连接或等待手机，未调用真实 Provider，未部署、访问生产、处理历史、commit 或 push。

## 2. 基线与工作区保护

- 后端：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`，HEAD `ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`。
- iOS：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`，分支 `feature/prd-stitch-ui-adaptation`，HEAD `11d0d0051b9be3cce57822dd059472d1e2536866`。
- 两端开工前已有大量未提交成果；未执行 reset/clean，未整文件覆盖，未回退 B6/B7/B8、正文同步、认证恢复、保存链和音频修复。
- 工作树与差异：`evidence/backend-git-status.txt`、`backend-diff-stat.txt`、`ios-git-status.txt`、`ios-diff-stat.txt`。
- 两端 `git diff --check` 均通过，证据为对应 `*-diff-check.txt` 空文件。

## 3. 四项核查缺口的实际修复

### R01 密集单 turn 与旧 8 条限制

已确认原因：旧组织结果饱和或 support 报漏项后直接失败，按 turn 分批无法拆开“一条发言中的多事实”。

修改：`owner_truth_candidate_extraction_worker.py` 新增基于原文证据区间的有界语义拆分；组织饱和、`finish_reason=length`、support 漏项时在同 Run/预算内细分。最小片仍不能处理时记录 typed 终态，不截尾、不假 empty。`deepseek.py` 解析并保存真实 finish reason 和 usage；输入预算基于实际序列化请求，而非局部 turns 估算。

红证据：`/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-01/evidence/semantic-probes.log`。\
绿证据：`test_lm_r01_dense_single_turn_refines_until_all_twelve_facts_are_supported`、`test_lm_r01_length_finish_is_recorded_and_refined_without_fact_loss`、`test_lm_r01_unsplittable_minimum_fragment_fails_without_false_success`，见 `evidence/backend-affected-final.log`。

### R02 relation 改写后的独立支持核验

已确认原因：原实现接受结构合法的 `resolvedMemory` 后直接构造候选，旧 support 仍被误当作新正文的证明。

修改：正文、属性、人物、地点、时间或关系实质变化后，旧证明失效；改写结果必须重新对原用户证据及 evidence range 做独立 support 校验。新增无证据断言、偷换日期/主体/地点、语气加强和错误撤回均阻止发布。

红证据：同上 `semantic-probes.log`。\
绿证据：`test_lm_r02_relation_text_is_revalidated_against_original_user_evidence` 及既有跨批纠正/B7 语义回归。

### R03 atom 级发布台账

已确认原因：旧 manifest 只证明某个 turn 被引用，不能证明该 turn 内每个已识别事实都有归宿。

修改：`owner_truth_live_long_memory.py` 将 manifest 升级为 atom 级 disposition；每个 atom 必须进入 final item，或有可验证的 merged/superseded/retracted 关系。证据区间、replacement、悬空引用、循环和伪替代均在冻结前校验；失败事务不得留下 manifest 或 candidate。

红证据：同上 `semantic-probes.log`。\
绿证据：`test_lm_r03_manifest_requires_a_disposition_for_every_recognized_atom` 与 PG `manifestFailureTransaction`。

### R04 预整理失败收尾

已确认原因：异常出口读取不存在的 `failure.reason`，且没有在预算耗尽时持久化 Run/Unit 终态，可能长期停在 organizing/running。

修改：统一 typed failure code/stage；首次合法恢复进入 `retryWait`，第二次合同失败使 Unit/Run `failed`，第三次唤醒 `idle`，Provider 总调用保持 2。旧 lease、迟到提交及 Worker 重建不得重置预算。`lease_repository.py` 同时修复真实 PG 查询中 UUID 与 text 的比较类型错误。

红证据：`/Users/gaominge/Documents/liftora/outputs/2026-09-20-astra-live-long-memory-delivery-review/run-01/evidence/worker-failure-probe.log`。\
绿证据：`test_lm_r04_preorganization_failure_retries_once_then_terminalizes` 与 `evidence/postgres-concurrency-rollback.log` 的 `preorganizationFailureTerminal`。

## 4. iOS 慢完成与冷启动补证

`EchoLiveMemoryRecoveryCoordinator` 现在保留服务端已确认的 pending phase。180 秒读取期限释放当前轮次网络和 poll 所有权，但不会把已知 `organizing` 倒退成 `statusUnknown`；用户重新核实时建立新 round/trace，旧回调不能清除新 poll 或覆盖新状态。

新增生产装配组合测试经过 UIKit Controller、Coordinator、临时磁盘 Store、真实 FeatureGate evaluator/policy cache、账号租约、BackendClient/requestJSON 和受控 URLProtocol：首个 GET 返回 organizing，逻辑到期后无额外 GET/POST，随后新鲜 GET 恢复 pendingReview，磁盘坐标保留。

跨进程 UIQA 另证实：seed 进程实际 append/end/ACK/admit 各一次；销毁进程后，第二进程仅凭同盘坐标通过 BackendClient 发 1 次只读 GET，显示 pendingReview，不启麦、不创建新 capture。

证据：`evidence/ownertruth-full.xcresult`、`uiqa-b6-cold-start.log`、`uiqa-b6-cold-start/run-02/`。

## 5. PostgreSQL 与正式记忆闭环

使用隔离 PostgreSQL 16.13、临时数据目录和合成数据；实例已停止。

- 正式链：真实 HTTP admit，默认 Source/Worker 且长 pipeline 开启；5 个 bounded chunks 经受控真实 adapter 产生 40 条长场候选，另有 1 条短场候选。41 条逐一审核并成为 41 条正式记忆；重建 Store 后 Source/Memory/Version 可读，命令重放去重。
- 并发/事务：单 Run 仅两个 Provider unit lease；第三个无租约。manifest 缺失 2/3 atom 时事务阻断且 0 manifest/0 candidate；中途提交回滚、epoch 变化、独立 worker 竞争均通过。
- 失败恢复：同 job 恢复、旧 lease 拒绝、合同恢复调用数 2、预算耗尽零候选、未批准 later history typed 拒绝。

证据：`evidence/postgres-formal-memory-chain.log`、`postgres-concurrency-rollback.log`、`postgres-contract-retry.log`。

## 6. 实际测试和构建

- 后端受影响回归：`177/177 PASS`，`evidence/backend-affected-final.log`。
- OwnerTruth 全量：`531/531 PASS`，`evidence/ownertruth-full.xcresult` 和 summary。
- 音频租约：`5/5 PASS`；长回答、主动打断、迟到回复、恢复聆听、PCM 编码：`25/25 PASS`。
- 双向 relay：encoded bytes/text 双向转发，共享 session budget，Realtime proxy `22/22 PASS`。
- 候选 UIQA：typed 详情、2 条原子审核、回执消费、候选移除、正式记忆和版本历史全部通过，含截图。
- B6 跨进程 UIQA：同 workflow、不同 PID、1 次只读状态 GET、零恢复业务写，PASS。
- Simulator 无签名构建：PASS，`build/simulator-build.log`。
- generic iOS 无签名构建：PASS，`build/generic-ios-build.log`。

本轮未重复执行已知存在全局 pool/路由清单装配问题的仓库级 discovery；没有把旧 `2642` 项中的 46 errors/7 failures算成本轮绿测或业务红测。受影响模块、默认装配、真实 PG 和两端构建均已单独通过。

## 7. 指纹与发布准备

- 后端源码：`evidence/backend-source-fingerprints.sha256`
- iOS 源码：`evidence/ios-source-fingerprints.sha256`
- 构建产物：`evidence/build-artifact-fingerprints.sha256`
- Simulator executable：`6c984d2f3e22ded9b82f56a0808b94015dc5279831ac29f3cfacd885e062f4eb`
- generic iOS executable：`08684c2e1a5f081e3b33bf07a04a20905a8c20ab11e914f5757bdabe085793cf`

发布依赖顺序：先备份并执行 migration `0122`，再部署后端 API 与 Candidate Worker，开启服务端长 pipeline/profile 的受控开关，最后发布 iOS。当前全部为准备状态，未执行部署。

## 8. 回退方案

1. 关闭服务端 Live 长 pipeline/profile 开关，停止创建新的 v3 Run；不删除已存在 Run、原文或恢复记录。
2. 回退 Worker 的自适应拆分、relation 二次 support 和 manifest v2 读取代码块；已落盘 v3 数据保持只读，不降级为旧整场路径发布。
3. iOS 可按 `EchoLiveMemoryRecoveryCoordinator` 的已知阶段保留与注入调度代码块局部回退；不得恢复未知写重发或删除坐标。
4. migration `0122` 不做破坏性 down migration；保留表和历史，仅停止新写入。

## 9. 残余风险与真机清单

- `PROVIDER_NOT_RUN`：真实 Provider 的长上下文语义质量、length 行为和延迟未知。
- `DEVICE_20M_NOT_RUN`：物理 20 分钟短长混合场未执行。
- `DEVICE_65M_NOT_RUN`：跨 3600 秒的 SDK、认证、音频、网络和电量行为未执行。
- `DEPLOY_NOT_RUN`：生产尚无 migration/Worker/API/iOS 本轮版本。
- `HISTORICAL_REPROCESS_NOT_RUN`：历史失败检查点、生产候选和 Dead Letter 未读取或重放。

20 分钟真机必须包含首/中/尾事实、后半补充、纠正及撤回；停止后检查全部应保留候选、重复/旧值不发布，逐条审核，正式记忆可查，杀进程重启仍在且不重复。65 分钟场至少五项首中尾事实，并在 61 分钟后补充和纠正，重复同一候选→审核→正式记忆→冷启动幂等链。保存提示或候选总数不能单独替代验收。

## 10. 结论

R01-R04、默认生产接线、隔离 PostgreSQL 大于 32 候选正式记忆链、iOS 慢完成/磁盘恢复、音频保持性、UIQA 和两类构建均已通过，恢复 `LOCAL_PASS`。该结论严格限于本地，不代表 Provider、真机、部署或历史场次已经完成。
