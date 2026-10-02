# DreamJourney Live 长对话分批整理 run-03 本地交付报告

日期：2026-09-20\
交付目录：`/Users/gaominge/Documents/liftora/outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-03/`

## 1. 状态

`LOCAL_PASS / PROVIDER_NOT_RUN / DEVICE_20M_NOT_RUN / DEVICE_65M_NOT_RUN / DEPLOY_NOT_RUN / HISTORICAL_REPROCESS_NOT_RUN`

本结论只覆盖当前源码的本地、Simulator、隔离 PostgreSQL 和无签名构建。未连接手机、未调用真实 Provider、未部署、未访问生产或历史数据，未 commit/push。

## 2. 基线

- Backend HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`。
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`，分支 `feature/prd-stitch-ui-adaptation`。
- 两端均保留开工前未提交修改；未 reset/clean，未整文件覆盖。
- 工作树、diff stat、diff check 和 HEAD：`evidence/*git-status.txt`、`*diff-stat.txt`、`*diff-check.txt`、`*head.txt`。两端 diff check 均为 0 字节。

## 3. 本轮确认并关闭的缺口

### R01-B：同 turn 事实与问题

`owner_truth_candidate_extraction_worker.py` 以稳定 fragment/range 投影覆盖台账。局部问题片不再把含事实的原 turn 整体排除；事实来源仍可精确回查，B7 纯问题边界未放宽。

### R02-B：合法补充后的分页支持验证

最终候选支持复核只校验该页拥有的 atom/evidence ranges；其他事实作为上下文而非本页遗漏项。完整性仍由全场 manifest 保证，无证据购房等新增继续拒绝。

### R03-B：精确重复后的 proof

精确重复、补充、纠正和语义合并统一使旧 support proof 失效并触发有界重验。最终一份事实绑定有效来源和 atom disposition，不填假 hash、不取消 manifest 检查。

### R04-B：failed Run 的父任务交接

`ModelAssistedOwnerTruthSourceExtractor` 与默认 Runtime 在父任务进入模型前读取 Run 终态。failed/cancelled 返回 typed `candidateExtraction.live.pipelineInput.runTerminal`；预整理失败两次后，父 job、重启唤醒都不产生第 3 次 Provider 调用。

### LI-01 / LI-02

`EchoLiveMemoryRecoveryCoordinator` 的 deadline、过期 poll 和预算终止统一保留 last-known pending 阶段并释放当前 round。真实 Controller 测试用 trace/attempt 绑定受控响应：旧轮 poll 只能得到 pending，活动轮次第 7 次 GET 才返回 reviewReady；无需人工核实，业务 POST 为 0，磁盘坐标保留。

### F-65 真实 admission 发现的尾段缺口

隔离 PG 红测发现，admission 用最后一条用户消息而不是 batch `through_message_sequence` 截取 `conversationTurns`，会遗漏停止水位内的尾部助手片段。`PostgresOwnerTruthInterviewCandidateProposalRepository.prepare_admission` 现以确认水位作为上界。相同断言转绿：HTTP 201、同一 Source 73,728 字符、301 turn、150 user turn。

## 4. 红绿证据

- R01/R02/R03 修前：`evidence/pre-fix-semantic-regression-probes.log`；修后：`post-fix-semantic-regression-probes.log`。
- R04 修前：`evidence/pre-fix-failed-run-handoff-probe.log`；修后：`postgres-default-runtime-concurrency-rollback.log`。
- F-65 修前：`evidence/pre-fix-f65-admission-failure.txt`；修后：`postgres-formal-memory-chain.log`，最终 JSON 明确给出 301/150/73728。
- LI-01 最终：`li01-targeted-rerun.log/.xcresult`；OwnerTruth 整体：`owner-truth-full-final.log/.xcresult`。
- 首次沙箱 CoreSimulator 失败和一次不确定全局 GET 计数的测试失败均保留为工具/测试装配证据：`owner-truth-full.log`、`owner-truth-full-rerun.log`，不冒充业务红测。

## 5. 实际执行结果

### Backend

- 长场 pipeline：22/22 PASS。
- Realtime/3900 秒 relay：23/23 PASS。
- Candidate Worker：64/64 PASS。
- Candidate proposal service/API：10/10 PASS。
- 语义压力夹具：F-R12、F-DENSE、F-65 均按原规模断言通过，详见执行矩阵。

### 隔离 PostgreSQL 16

- 默认 Runtime 的私密 Run→关闭/admit→父 Source 交接：PASS；默认关系 adapter 实际执行。
- 预整理两次失败→父 job→重启：Provider 始终 2 次、候选 0、终态可读。
- 并发 lease、epoch 变化、事务中途回滚、manifest 失败零残留、重启幂等：PASS。
- 短场 1 条 + 长场 40 条候选，41 条真实审核形成 41 条正式记忆，Store 重建及命令去重：PASS。
- F-65 大 Source 真实 HTTP admission：PASS。

### iOS

- OwnerTruth：532/532 PASS。
- 音频租约：5/5 PASS。
- 长回答、主动打断、迟到回复、恢复聆听、PCM：25/25 PASS。
- Simulator 无签名构建：PASS。
- generic iOS 无签名构建：PASS。

完整摘要：`evidence/test-summary.txt`。原编号映射：`reports/2026-09-20-BE-IR-LM-LI执行矩阵-run03.md`。

## 6. 指纹与日志安全

- Backend：`evidence/backend-source-fingerprints.sha256`。
- iOS：`evidence/ios-source-fingerprints.sha256`。
- 构建：`evidence/build-artifact-fingerprints.sha256`。
- Simulator executable：`6c984d2f3e22ded9b82f56a0808b94015dc5279831ac29f3cfacd885e062f4eb`。
- generic iOS executable：`08684c2e1a5f081e3b33bf07a04a20905a8c20ab11e914f5757bdabe085793cf`。
- `log-redaction-scan.txt` 的 4 个命中均为依赖源码文件名 `AccessTokenPlugin.swift`，无 token 值、正文、密钥或业务原始 hash。

## 7. 发布和回退

发布准备顺序不变：备份并执行 migration `0122`，部署 Backend API 与 Candidate Worker，受控开启长 pipeline/profile，最后发布 iOS。本轮未执行。

局部回退：关闭长 pipeline/profile 只停止新 v3 Run；回退 Worker 的 fragment/range、proof revalidation 和终态交接代码块时保留既有 Run/Source/manifest 只读；iOS 可回退 pending-stage 保留与 poll 隔离代码块，但不得恢复未知写重发或删除坐标。Migration 不做破坏性 down。

## 8. 残余风险

- 真实 Provider 的长上下文质量、真实 length 分布和耗时尚未执行。
- 物理 20 分钟、密集 20 分钟和物理至少 65 分钟尚未执行。
- 部署后的真实 Worker/API 版本及生产 migration 尚未验证。
- 历史失败检查点、候选和 Dead Letter 未读取、清理或重放。

后续步骤已单独保存为 `reports/2026-09-20-后续真机验收清单-run03.md`。本轮到本地交付为止，不自动进入真机或部署。
