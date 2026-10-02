# BE / IR / LM / LI 执行矩阵 run-03

日期：2026-09-20。所有 PASS 均对应当前 run-03 指纹；测试总数不替代下列场景断言。

## BE-01 至 BE-14

| ID | 场景结论 | run-03 证据 | 状态 |
|---|---|---|---|
| BE-01 | 两轮短场经真实 adapter 构建候选，Source/同场状态保留，不自动进入正式记忆 | `backend-live-long-memory-pipeline.log`；`postgres-formal-memory-chain.log` | PASS |
| BE-02 | 16+15 长形状与首中尾事实完整复核；F-R12 保持 77 总 turn、39 user、12 事实真值 | `backend-live-long-memory-pipeline.log` | PASS |
| BE-03 | config、组织、support、proposal/commit 错误按阶段 typed 分类，前置错误零模型请求 | `backend-owner-truth-candidate-worker.log` | PASS |
| BE-04 | 首次组织合同错后沿原 job 恢复，候选只提交一次 | `backend-owner-truth-candidate-worker.log`、`postgres-contract-retry.log` | PASS |
| BE-05 | support 合同错/遗漏不提交部分候选，恢复后整场重建 | 同上 | PASS |
| BE-06 | 连续合同错误/uncertain/非法证据有界失败，Source 不变、候选为零 | `backend-owner-truth-candidate-worker.log` | PASS |
| BE-07 | contract/transient 组合不放大预算，maxAttempts 保持 | `backend-owner-truth-candidate-worker.log`、`postgres-contract-retry.log` | PASS |
| BE-08 | retryWait、重建、失租、重领和持久预算由真实 PG 验证 | `postgres-contract-retry.log`、`postgres-default-runtime-concurrency-rollback.log` | PASS |
| BE-09 | 纯问题、确认问法、助手内容不生成伪事实 | `backend-live-long-memory-pipeline.log`、`post-fix-semantic-regression-probes.log` | PASS |
| BE-10 | 同轮/跨轮纠正、撤回、事实与问题混合只保留受支持终值 | `backend-live-long-memory-pipeline.log`、`post-fix-semantic-regression-probes.log` | PASS |
| BE-11 | epoch、权限、lease、并发 Worker 与迟到结果不能越权提交；Provider 等待不持有长事务 | `postgres-default-runtime-concurrency-rollback.log` | PASS |
| BE-12 | 每 job/attempt 安全诊断独立；日志扫描无正文、凭据或业务原始 hash | `backend-owner-truth-candidate-worker.log`、`log-redaction-scan.txt` | PASS |
| BE-13 | failed Run 不伪称 ready；正常默认 Runtime 状态与候选同场一致 | `postgres-default-runtime-concurrency-rollback.log` | PASS |
| BE-14 | 短场 1 条、长场 40 条经真实审核合同成为 41 条 Memory/Version，Store 重建及重放幂等 | `postgres-formal-memory-chain.log` | PASS |

## IR-01 至 IR-07

| ID | 场景结论 | run-03 证据 | 状态 |
|---|---|---|---|
| IR-01 | failed/quarantined/no-candidate 保留恢复坐标和安全观察 | `owner-truth-full-final.log/.xcresult` | PASS |
| IR-02 | 失败观察写盘异常不伪造成功；已知 organizing 在 deadline、过期 poll、预算终止时不倒退 | 同上 | PASS |
| IR-03 | Controller/Coordinator/Store 重建后只读恢复，未知写不补发 | 同上；run-02 跨进程 UIQA 保留为既有证据 | PASS |
| IR-04 | 离页重进、旧轮 poll、迟到回调、账号/epoch 变化不污染新轮次 | `owner-truth-full-final.log/.xcresult` | PASS |
| IR-05 | pendingReview/empty 清理幂等；失败清理不回退业务终态 | 同上 | PASS |
| IR-06 | V1/V2、缺 observation、损坏、scope mismatch、仅 admitted checkpoint 保持保护边界 | 同上 | PASS |
| IR-07 | queued/retryWaiting/organizing/failed/reviewReady 的真实解码和 UI 映射；当前 trace 第 7 次 GET 自动 pendingReview，零 POST | `li01-targeted-rerun.log/.xcresult`、`owner-truth-full-final.log/.xcresult` | PASS |

## LM / LI 补充闭环

| ID/范围 | 核心断言 | 证据 | 状态 |
|---|---|---|---|
| R01-B / LM-01 | 同一 turn 的事实片与问题片按稳定区间记账；问题片不排除同 turn 事实 | 修前 `pre-fix-semantic-regression-probes.log`；修后 `post-fix-semantic-regression-probes.log` | PASS |
| R02-B / LM-08 | relation 补充后只按本页 owned evidence ranges 复核；无证据新增仍拒绝 | 同上 | PASS |
| R03-B / LM-08 | 精确重复合并使旧 proof 失效并有界重验；跨批重复只发布一份 | 同上 | PASS |
| R04-B | failed/cancelled Run 使父 Source typed 终止，不能触发第 3 次 Provider | 修前 `pre-fix-failed-run-handoff-probe.log`；修后 `postgres-default-runtime-concurrency-rollback.log` | PASS |
| F-R12 | 77 总 turn、39 user、12 事实，其余问题，事实完整且问题不发布 | `backend-live-long-memory-pipeline.log` | PASS |
| F-DENSE | 241 总 turn、120 user、300 事实、正文大于 30k、最终候选大于 32 | 同上 | PASS |
| F-65 / LM-04 / LM-31 | 逻辑 3900 秒、301 turn、150 user、正文 73,728 字符，经真实 HTTP admit 保存同一 Source；61 分钟后纠正夹具保留 | `postgres-formal-memory-chain.log`、`backend-live-long-memory-pipeline.log` | PASS |
| LI-01 | UIKit→Coordinator→磁盘→FeatureGate→BackendClient→URLProtocol；活动 trace attempt 1...7 自动完成，无人工 verify、业务 POST 为 0 | `li01-targeted-rerun.log/.xcresult` | PASS |
| LI-02 | 读取预算/期限结束保留 lastKnownPending，旧回调不释放新 round | `owner-truth-full-final.log/.xcresult` | PASS |
| LI-05 | 3599/3601/3900/7201 注入时钟验证新 Live profile，旧 profile 不越权 | `backend-realtime-voice-proxy.log` | PASS |
| LI-06 | 3900 秒双向 20ms PCM16 帧与封装开销受控 relay，累计约 258.18 MB，小于 1 GiB | `backend-realtime-voice-proxy.log` | PASS |

## 独立边界

`PROVIDER_NOT_RUN / DEVICE_20M_NOT_RUN / DEVICE_65M_NOT_RUN / DEPLOY_NOT_RUN / HISTORICAL_REPROCESS_NOT_RUN`
