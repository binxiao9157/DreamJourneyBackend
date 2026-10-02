# A-D 精确验收矩阵

| 项 | 状态 | 实际装配与断言 | 原始证据 |
|---|---|---|---|
| A1 统一实际配置 | PASS | 配置只从磁盘加载；完整配置参与 fingerprint；环境内联配置被清除或拒绝 | `artifacts/logical20-config.json`、`artifacts/logical65-config.json` |
| A2 short 门禁顺序 | PASS | 每个 long 前必须先完成同一源码、配置和构建的 short；最终顺序为 short→20、short→65 | `artifacts/runner-complete.json` |
| A3 完整源码/宿主构建绑定 | PASS | source、host dylib、test executable、xctestrun 均进入门禁；任一复制件变更均在 long 业务请求前被拒绝 | `artifacts/source-build-manifest.json`、`negative/*-gate-negatives.json` |
| A4 旧/失败/跨运行 receipt | PASS | missing、failed、different run/source/config/build 和 normalized config mutation 均返回 412 | `negative/logical20-gate-negatives.json`、`negative/logical65-gate-negatives.json` |
| B1 逐轮跨阶段身份 | PASS | raw callback→磁盘 command→HTTP accepted→server source 逐条对账；20 分钟 220/220，65 分钟 300/300 | `artifacts/*-ledgers/*-cross-stage-ledger.json` |
| B2 错身份负例 | PASS | 同正文但错误 raw owner 被确定性拒绝 | 两个 full-chain JSON 的 `crossStage.identityNegative` |
| B3 最终证据读回 | PASS | 审核前后读回 candidate manifest、atom、source turn index、evidence range；正式记忆重建后仍一致 | `green/logical20-full-chain.json`、`green/logical65-full-chain.json` 中 `evidenceBeforeReview/evidenceAfterReview` |
| B4 跨批语义 | PASS | 重复事实来自两个 organization unit，最终只产生一条候选；补充、纠正、撤回与后半场证据均被读回 | 20 分钟 turn 7/189；65 分钟 turn 7/221；两个 full-chain JSON |
| C1 真实请求正文约束 | PASS | 受控模型校验实际 organization/support 请求正文、turn identity 和 evidence 引用，不从期望答案反填输入 | `artifacts/*-provider-identity-ledger.json`、full-chain `providerBoundary` |
| C2 请求正文篡改负例 | PASS | 同 turn index 替换正文、漏中段均被拒绝；产品链未发出替代请求 | full-chain `providerBoundary.negatives` |
| C3 真实 iOS 候选读取 | PASS | CandidateInbox controller 经 BackendClient 读取本场候选；20 分钟 4 条、65 分钟 17 条均可见 | full-chain `candidateVisibleInRealIOSClient=true` |
| C4 隐藏候选负例 | PASS | 服务端故意隐藏候选时真实 iOS 客户端只读到 2 条；恢复正常响应后读到 4 条，负例没有被候选总数掩盖 | full-chain `realIOSHiddenCandidateNegative` |
| D1 逻辑时钟 | PASS | 生产共用注入时钟实际推进 1200 秒和 3900 秒；不是把配置时长当作已验证 | full-chain `logicalClockAndAuthority` |
| D2 HTTP 401/认证恢复 | PASS | logical20 最后一条 append 在 handler 前返回 401，经真实 `/auth/refresh` 后用同 command 重试并得到 201 | logical20 `httpFaults`：状态 `[401,201]`、refresh=1、command preserved |
| D3 已保存响应丢失 | PASS | logical65 第 300 条 append 服务端已保存后丢失响应；客户端只读 status GET 核实，POST 仍为 1 | logical65 `httpFaults`：drop=1、POST=1、GET=1 |
| D4 429/超时 | PASS | 后端 typed transient 分类、预算和后续合法恢复通过定向回归 | 后端 92 项回归；相关测试位于 `tests/test_owner_truth_live_long_memory_pipeline.py` 和 `tests/test_owner_truth_candidate_extraction_worker.py` |
| D5 PostgreSQL/重建 | PASS | 每场使用独立 PostgreSQL 数据库；候选→审核→正式记忆→store 重建读取完成，运行后隔离库删除 | 两个 full-chain JSON：`storeRecreated=true`、formal count 5/18 |
| D6 工具故障负例 | PASS | 漏中段、漏尾段、错 Source、只检查后端候选数均被工具拒绝 | `negative/*-harness-negatives.json` |

## 场景结果

| 顺序 | 场景 | 用户/总回合 | 候选 | 正式记忆 | 状态 |
|---|---|---:|---:|---:|---|
| 1 | logical20-short | 2/4 | 1 | 纳入本组共 5 条 | PASS |
| 2 | logical20 | 110/220 | 4 | 本组共 5 条 | PASS |
| 3 | logical65-short | 2/4 | 1 | 纳入本组共 18 条 | PASS |
| 4 | logical65 | 150/300 | 17 | 本组共 18 条 | PASS |

逻辑时长只证明注入时钟和 TTL/调度行为，不等于物理 20/65 分钟。
