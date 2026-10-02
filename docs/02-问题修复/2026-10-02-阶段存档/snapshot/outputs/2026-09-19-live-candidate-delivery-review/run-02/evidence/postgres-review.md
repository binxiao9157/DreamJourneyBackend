# run-02 PostgreSQL 交付证据独立复核

日期：2026-09-19。范围：只读报告、实测日志与源码；本次复核未启动/重跑 PostgreSQL，未访问网络、生产或手机，未改代码。

## 结论

**真正的 PostgreSQL 候选→确认→正式记忆→仓库重建/幂等链已经实质补齐，BE-08 的关键持久预算场景也有数据库证据。C5.4 / BE-11 的事务回滚和并发核心门禁尚未见本轮实际执行证据，不能把整个 BE-11 标为 PASS。** 这属于原要求未充分覆盖，不是认定新增业务缺陷；无需机械地把所有内存测试搬到 PostgreSQL。

## 已完成且可认可的范围

交付根目录：`/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-candidate-contract-fix/run-02/`。

1. `evidence/green/postgres-live-candidate-formal.log` 与当前 formal smoke 指纹一致（SHA-256 `9e909c0768005db5449dae1f1c7b8dac0ce9070d1c519ba13405d6004a6b9f1c`）。脚本 348–363 行真实建临时数据库、跑 migrator、使用 PostgresStore；495–546 行运行真实 Worker/SourceExtractor/LiveExtractor + 受控 HTTP，并通过 HTTP status/confirmation 验证候选绑定。没有 seed 下游候选/正式记忆代替 Worker 提交。
2. formal smoke 571–656 行逐候选刷新 proposal，以最新 revision/changeSet/hash 经真实 decision/activation API；658–732 行检查 Memory/Version 数量、关闭连接池并重建 Store/TestClient、列表/详情内容集合、Source 正文与确认数量、原 command 幂等重放。实测输出短场 1 条、长场 5 条、正式记忆 6 条；两个源与整条持久链共享同一数据库。**C5 主链和 BE-14 可认可为真实数据库完成。** 它是服务/仓库/连接重建，不是 OS 进程重启；本设计要求前者。合成“长场”包含 6 个 user/5 个 assistant，16+15 输入形状另有单元测试，不等价于物理 20 分钟或真实 Provider。
3. `postgres-live-retry-budget.log` 与 retry smoke 指纹一致（SHA-256 `ac8c9545ed4385e8550a3f66678344e0bdff1aa0ee815feaacf0b9b92324b0b9`）。脚本 245–299 行将 attempt1 合同错误、attempt2 transient 写入真实 lease repository，关池重建后读取原反馈，过期 lease 明确拒绝；315–406 行最后允许 attempt 经真实模型适配链返回后人为使 lease 过期，旧 Worker 返回 lost，再领同 job 以 budgetExhausted 终结且 Provider 请求数仍为 2、候选数为 0。**BE-08 的重建预算与最后 attempt 失租核心场景已实测。** 前两次失败由 repository.release_retryable 构造，不代表 PG 上执行了两次真实 HTTP 失败；这里验证的是持久预算恢复边界。
4. `postgres-review-ready-handoff.log` 证明真实 PG 状态读取、账号/Vault 隔离和 stale/redacted 过滤。它的 epoch 变更发生在读取夹具阶段，不证明提取中的 Source/epoch 变化会拒绝旧模型结果提交。

## 尚缺的原门禁

原复核 C5.4 明确要求“失败回滚、候选/receipt 原子提交、并发 lease、重启预算、失租和 Source/epoch 变化”。原设计 BE-11 还要求模型等待不长期占 DB 事务。

当前三个 PG 交付脚本没有以下实际断言：

- **提交中故障的真实回滚/原子性**：在候选已写入但 receipt/job completion 未完成的事务边界注入故障，重开连接验证候选、extraction、consumer receipt 不出现半提交。现有失租发生在 commit 之前；仅 candidate_count=0 无法替代写入中断回滚。
- **真实并发竞争**：两个独立 PG 连接/Worker 争抢同一 job 或过期重领，验证唯一持有者、旧 lease 不能提交，以及最终候选/receipt 各一次。新 retry smoke 使用顺序 claim/reclaim；heartbeat_interval=60，流程并未刻意触发并发 heartbeat。
- **提取等待期间的授权/Source 变化**：至少代表性 Source/version/epoch 变更，在另一个连接提交变更后放行受控模型结果，验证提交前再次校验且无旧候选/receipt。
- **模型等待不占长期事务**：用受控 HTTP barrier 等待，从另一连接观察事务/执行锁敏感操作，证明 DB UoW 已退出。源码结构只能辅助审查，不能标记数据库实测 PASS。

矩阵所指 `tests/test_owner_truth_candidate_extraction_worker.py` 的 `_Store` 在 135–142 行全部使用 InMemory 仓库，request_unit_of_work 189–192 行仅计数/yield。2221 行 stale/revoked/deleted 用例在 Worker 开始前就改内存状态；2372/2405 行并发/heartbeat 用例仍为内存。`tests/test_async_effect_lease_repository.py:33–36` 也为 InMemory。它们有价值但不能代替上述 PostgreSQL 原子性门禁。

## 已有可复用设施及历史证据边界

- 仓库 `scripts/backend-async-effects-postgres-smoke.py` 已包含真实 PG 并发 claim（573 行）、Source epoch admission（450 行附近）、consumer 回滚（784–833 行）等可复用基础门禁，**run-02 报告、矩阵和日志未引用其本轮实测结果**。补测可以复用，不需重建完整框架；Live 候选/receipt 的联合事务故障需要针对真实当前链断言。
- `backend-async-effect-worker-loss-evidence-postgres-smoke.py` 有并发基础设施，但同样未见本轮执行记录。
- 2026-09-10 的 `postgres-source-rebuild-lease-smoke-final.log` 有 concurrentRecovery=true/staleWorkerFence=true。这是 Source rebuild lease 另一条实现且时间早于本轮共享 lease 变更，不能直接替代当前 candidate extraction Worker 的 PG 回归。
- 三份 PG 日志均为最终摘要，主报告记有 PostgreSQL.app16、Unix socket `/private/tmp`、55432、schema0121；建议补保存实际执行命令及子场景断言摘要，使矩阵每条可追踪。无须保留原始正文、凭据或完整 DSN。

## 验收建议

保留 C5/BE-14 主链、BE-08 核心持久边界的已通过结果。将 BE-11 从总 PASS 改为“内存保护通过，PG 失租通过，事务/并发及提交前失效门禁待补”；完成上述有限数据库测试后再恢复完整 LOCAL_PASS。真机继续保持 DEVICE_NOT_RUN，由用户主动发起；不能将这些本地数据库补验收转为等待手机。
