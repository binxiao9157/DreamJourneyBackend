# run-03 PostgreSQL 收尾门禁独立复核

日期：2026-09-19。范围：原 run-02 收尾清单 §4 及后续 transient 历史。只读报告、日志、当前源码与 SHA-256；未重跑/启动数据库，未访问网络、生产或手机，未修改产品代码。

## 结论

**原 §4 要求补充的并发、事务中断原子回滚、同原 job 恢复、模型等待中 epoch 变化和无长期事务的代表性 PostgreSQL 门禁已经闭合。** 最终 JSON 摘要有具体真实数据库查询和 require 断言支撑，不是仅靠手填 true/false 判 PASS。可保留 run-02 正常正式记忆链及最后 attempt 失租预算的已通过证据，无需继续重做正常链或扩大场景。

## 源码与交付指纹

后端：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`，HEAD `ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`，含未提交修改。

- `scripts/backend-owner-truth-live-candidate-concurrency-postgres-smoke.py`：SHA-256 `ec7eb57f2fa4f0e201f80e9f2d314bf164bec64db270cb6b05ef9ce35644f214`。
- `scripts/backend-owner-truth-live-contract-retry-postgres-smoke.py`：SHA-256 `350874c088c9625c980159f413bacf42e510a56fe2647baeb945efe32610d4a3`。

以上两项与 run-03 `evidence/green/source-fingerprints.txt` 一致。实测日志分别为 `postgres-live-candidate-concurrency.log`、`postgres-live-retry-history.log`，schema head 均为 `0121`。运行环境来自交付报告：本地 PostgreSQL 16.15，一次性库；本次复核没有重新检查已停止实例。

## 各原要求的实际断言

| 原要求 | 源码与实际结果 | 判断 |
|---|---|---|
| 独立 Worker 竞争同 job | concurrency smoke 246 行附近建立两个独立 Store/pool；273–301 行以 HTTP Event 门障使 A 持 lease 等待、B 同时尝试。B 必须 idle；放行 A 后按原 operation 查询 extraction=1、candidate=1、inbox=1、completion receipt=1、operationAccepted=1、job succeeded/attempt1；Provider 仅两次请求。 | PASS，证明活动 lease 竞争，不声称覆盖所有数据库锁调度排列。 |
| 真实提交中途故障及全部回滚 | 320–346 行 patch Postgres consumer.consume，先运行原 consume 再抛故障；真实服务 `record_in_unit_of_work` 已先 persist extraction/candidate 后 consume，因此故障在真实写事务中。独立连接回读 extraction/candidate/inbox=0，completion receipt=0，唯一 operationAccepted 保留，job retryWait。 | PASS，区别于前一版提交前失租。 |
| 同原 job 的合法恢复及单份结果 | 348–382 行仅把本地测试 job 的 available_at 前移，不新建 intent/job、不重置 attempt；换 B Worker 执行同 operation。结果要求 attempt2、succeeded、各业务对象恰一份及 receipt 类型精确。 | PASS；时间前移是测试调度控制，不是生产恢复算法。 |
| 模型等待时权限失效拒绝旧结果 | 409–431 行 A 进入受控 HTTP 等待后，由独立 PG 连接提升对应 Vault authority_epoch，再释放模型。Worker 必须 blocked；extraction/candidate 均 0。实测日志只有合法 blocked completion receipt，无 extraction completion。 | PASS，覆盖本轮要求的代表性 epoch 失效，不冒充全部 Source/version 排列实测。 |
| HTTP 等待不长期占事务 | 121–127 行真实查询 pg_stat_activity；276、412 行在已确认进入 HTTP Event 门障时各断言同临时数据库的 idle-in-transaction / aborted idle transaction 数为0（排除检查连接）。 | PASS，在受控等待点直接观察，不靠固定 sleep 推定。它不声称永远没有短 heartbeat 事务。 |
| 非白名单后续历史不产生反馈上下文 | retry smoke 309–339 行在真实 repository 中形成 attempt1 合同错误 + attempt2 的 repeated contract、authorizationRejected、arbitrary 三种记录；attempt3 load_context 必须为 None。 | PASS；这是 reader 资格断言，不声称测试调用了真实 Provider 的这些失败。 |

`providerWaitHeldTransaction=false` 虽在输出处写为字面值，前面两处真实活动查询非0就会抛错，不能在此条件失败时输出最终 passed。其余四组结果对象来自按原 Source/operation 的真实 SQL counts。

## 保留的边界

- 本次审查认可交付的实际执行日志与相同源码指纹，未独立重跑 PostgreSQL。
- 中间 role、沙箱、receipt 数量、故障异常类型和 available_at 的测试修正单列为非业务红，报告没有把它们算作产品根因。
- 旧 attempt 失租及超限重领零新增 Provider 请求仍由 retry 脚本原有断言本轮再次执行，日志仍为请求数2/候选0。
- 不要求把所有内存场景机械搬入 PostgreSQL；原 §4 的有限补证可关闭。
- 此结论只针对数据库收尾，不替代 iOS/诊断交付复核，不代表真实 Provider、物理长对话、手机、部署或历史重处理通过。
