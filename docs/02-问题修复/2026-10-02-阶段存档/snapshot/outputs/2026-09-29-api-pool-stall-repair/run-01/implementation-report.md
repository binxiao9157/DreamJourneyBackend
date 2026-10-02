# API 连接池阻塞修复 · 2026-09-29

## 结论与范围

本轮针对 DJ-API-READ-STALL-01 / DJ-API-LIVENESS-01 已取得现场故障栈的阻塞机制，完成代码修复及本地同断言验证。不是把历史所有 Live 保存失败归为一个原因。

本地状态：`LOCAL_PASS`。后端发布及线上核验见本文末尾更新；真实 Provider、手机短场及物理长场必须单独验收。

## 已证实机制

9/29 真实故障时 API 主线程停在 HTTP 统计中间件 → evidence 写入 → 同步连接池 getconn，期间服务器本机 `/live` 无响应。同场票据请求约 25 秒返回 503，手机 15 秒截止。见[现场记录](../../2026-09-29-dreamjourney-live-device-acceptance/run-01/device-report.md)。

本地又确认请求事务入口也在事件循环同步取连接。已有请求占住连接、等待下游处理返回；新请求在同一事件循环阻塞等待连接，旧请求无法及时完成并归还连接。统计写入再次争抢同一池并阻塞响应，放大等待。无需假设数据库死锁即可复现这一机制。

**边界：**这是这次已捕获机制的修复。生产现场最先占满池的具体请求组合、9/24 同因关系、历史 seq5 首发原因仍未证实；没有将历史候选整理、语义校验或模型容量问题一并宣布解决。

## 代码变更

仅 4 个产品实现文件，相对接手前工作区的精确差异见 [patch](product-change-vs-entry.patch)。原 dirty 修改完整保留，原同步 `DatabaseUnitOfWork` 与 entry 字节一致。

1. `app/db/async_uow.py`：为 HTTP 请求增加异步事务边界。同步取连接及提交/回滚/归还在不同的专用线程执行，等待连接不能占用全部归还执行位。使用原连接池和原超时预算，等待入口队列所耗时间计入同一截止。ContextVar 仍在请求任务绑定，保留同步仓储和 Worker 原事务语义。
2. `app/services/postgres_store.py`：接入该 HTTP 边界；同步 Worker 的事务入口保持原样。
3. `app/observability/metric_dispatch.py`：HTTP 影子统计改为单个后台线程、最多 128 条排队、满时立即丢弃统计并计数。统计故障不再是响应返回前提；不丢弃业务写。关闭有 1 秒排空预算，无法排空的统计计入丢弃。统计持久化为最终完成，不能据响应返回就声称统计已落库。
4. `app/main.py`：请求事务使用新边界；统计异步投递；`/live` 直接使用 async 纯内存返回；受保护的现有 observations 接口增加队列计数。没有扩大业务重试、池大小或手机截止，没有改变保存/候选整理策略。

取消保护覆盖原生 Task.cancel 和 AnyIO 取消域：取连接途中取消必须等取得/失败后清理；取得后先检查已到达的取消，不能进入业务；提交期间取消等待唯一一次提交完成，绝不重放业务写。

## 修前红与修后绿

- [最初两项修前业务红](red.log)：8 请求/2 连接时旧事务入口出现 503 和事件循环阻塞；慢统计 sink 延迟响应。对应最终同断言均通过。
- [新增取消边界修前红](anyio-checkout-red.log)：AnyIO 取消域在 checkout 期间截止，初版隔离修复仍进入 handler。最终补检查点后同断言通过。这份失败未被覆盖。
- [最终后端回归](final-backend-v2.log)：134/134，覆盖并发、慢统计、取消、队列有界、原 UoW、统计记录、票据诊断与提交、凭据边界、语音代理及 PostgresStore。
- [默认 API / PostgreSQL 对照脚本](pg_default_api_probe.py)：独立 Uvicorn 正常 lifespan、实际默认中间件、迁移至 0124 的随机隔离库；相同 2 连接、0.4 秒 checkout、24 个真实 HTTP 读取请求与 4 个 `/live`。修前代码来自本轮不可变 entry 快照，修后是最终源码。

| 同环境指标 | 修前 | 最终修后 |
|---|---:|---:|
| 24 个请求 | 2 个 200、22 个 503 | 24 个 200 |
| `/live` 最慢 | 17.812 秒 | 0.007 秒 |
| 事件循环最大观测间隙 | 5.670 秒 | 0.011 秒 |
| 收尾活跃 UoW | 见原结果 | 0 |

[修前原始结果](red-pg-api.json) · [修后原始结果](green-pg-api.json) · [对照摘要](pg-comparison.log)。延时仅代表这组本机测试，不是线上 SLA。

[真实 PG 事务探针](pg_transaction_probe.py)及[结果](pg-transactions.json)：成功、显式回滚、业务异常、取消、池满、释放后恢复；最终仅两次成功事务有行，3 次回滚无行；池满约 0.3 秒内按原预算失败，事件循环继续，最终两连接均归还、active=0。

保留两次工具装配失败：`invalid-env-*` 为环境变量名错误，未用预期池参数；`invalid-shutdown-observation-*` 为 Uvicorn 退出信号跳过尾部观测。两者均未用于 PASS，对应遗留隔离库已清理。

## 最终同版保存链

[最终执行结果](four-scene-final-v2/artifacts/runner-complete.json)：严格 short-A → logical20 → short-B → logical65，每场长场有独立、当前版本短场 receipt。模拟器使用真实 iOS 客户端保存链、独立默认 API、官方 Worker、隔离 PostgreSQL，模型 transport 为本地受控 HTTP。

| 场次 | 逐条身份核对 | 长场候选 | 正式记忆（含前置短场） |
|---|---:|---:|---:|
| 逻辑 20 分钟 | 220/220 | 4 | 5 |
| 逻辑 65 分钟 | 300/300 | 17 | 18 |

两份 [20 分钟完整证据](four-scene-final-v2/green/logical20-full-chain.json) / [65 分钟完整证据](four-scene-final-v2/green/logical65-full-chain.json) 均验证客户端候选可见、审核、正式记忆、API 重启重建读取、重复证据合并及错身份负例。这里的逻辑时长不代表物理时长，也不代表真实模型语义质量。

`four-scene-final` 是取消边界最后修改前启动的中间运行，虽然通过，不作为最终同版依据。最终只使用 `four-scene-final-v2`。iOS 源码本轮无修改；没有扩大到完整 iOS 套件或无关后端全量套件，并非宣称所有历史基线全绿。

## 指纹与复现

- [最终修复源码指纹](final-source-fingerprints.json)
- [全部运行源码 949 文件](all-runtime-source-manifest.json)
- [4 文件发布 overlay 指纹](backend-overlay-manifest.json)
- [模拟器构建与依赖指纹](four-scene-final-v2/artifacts/source-build-manifest.json)
- [复现命令](reproduce.md)

已有本地 PG 服务不是本轮创建，保留运行；仅创建/删除本轮隔离库，不停止其他任务的 PG。没有修改模型、历史数据、iOS 业务源码，也没有 commit/push。

## 发布与外部验收

发布在最终本地门禁通过后，沿用用户已明确给出的后端部署授权。发布仅覆盖上述 4 个实现文件，不把全部 dirty 文件无差别上线。

最终状态：`LOCAL_PASS / DEPLOYED / SERVER_READONLY_PASS / DEVICE_NOT_RUN / REAL_PROVIDER_NOT_RUN`。

- 新发布目录：`/opt/services/dreamjourney/releases/api-pool-isolation-20260929`；current 已切换。没有新增数据库迁移，schema 仍为 0124。
- 发布前 `dreamjourney-db-backup.service` 执行成功；旧发布 `live-mic-20260929` 和 API 回滚标签 `dreamjourney-rollback-api:pre-pool-20260929` 保留。[发布脚本](deploy.sh) / [完整发布日志](deploy.log)。
- 六个启用 Worker 按原宽限排空后重建，镜像/schema/activation/稳定性检查通过；最终 API 与六 Worker 均 running、restartCount=0。[运行状态](final-runtime-state.log)。
- 实际 API 镜像运行源码 949 文件与最终本地清单完全一致。[最后源码与只读核验](final-deployed-readonly-result.json)。
- 内网两组九次读取及公网 `/live`、`/ready`、`/v2/release-policy` 全部 200。最后采样：poolExhausted=0、connectionReturnFailures=0、active=0；统计 accepted=completed=72、dropped=failed=queued=0。[公网结果](public-readonly-result.json)。这只是本次只读核验，不能证明所有真实负载已通过。
- 用部署的 Python 3.11 镜像另起 `--network none`、`STORE_BACKEND=memory`、不传入生产配置的独立容器，20 项 UoW/隔离测试通过。[镜像环境测试](deployed-image-isolated-tests.log)。初次测试包缺失及临时目录挂载权限错误保留在 `invalid-test-package-image-tests.log` / `invalid-mount-permissions-image-tests.log`；修正装配后才运行到业务断言，没有隐藏失败。
- 本轮没有安装/启动手机、调用真实模型、生成或审核线上测试候选；检测到 iPhone unavailable。已向用户说明并询问是否连接继续，不能将未执行当作通过。

后续：手机连接解锁后，先跑独立真实短场，核对开麦、实时文字、停止后候选可见、审核到正式记忆及冷读。短场通过后再物理20分钟；65分钟另按原验收计划。历史任务不重放。声学麦克风/扬声器效果与静音PCM工具验证仍分开。

回滚时使用保留旧 release 重建 API 和六 Worker，按已有 `scripts/rebuild-enabled-workers-after-migration.sh` 对齐镜像，再切回 current；本次无 schema 变化，不回滚数据库、不清理候选。回滚属于恢复操作，本文仅记录，未执行。
