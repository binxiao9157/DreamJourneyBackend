# run-01 后端复核（2026-09-28）

结论：尚不符合全部本地验收预期。Sol 自报 `LOCAL_INCOMPLETE` 准确；不能将 81 项 memory 单测当作 MIC-15/16 或真实 PostgreSQL 验证。

本轮未启动 API、数据库、Provider、手机或安装软件。只读核对代码/材料后，另执行了纯 AST 函数探针：从当前源码抽取函数，依赖全部为 fake，不导入 app、不访问数据库或网络。只在此目录保存审查材料。

## 1. 新诊断的失败隔离合同未落实

[主设计 D4:136](/Users/gaominge/Documents/liftora/02-设计文档/02-问题修改/Live修复/2026-09-27-DreamJourney-Live开麦迟缓与发前失败-分析及开发指导.md:136) 要求有界日志、失败不影响业务、记录丢弃/不完整。当前 [main.py:7918](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7918) 直接同步 `logger.info`；[realtime_voice_proxy.py:166](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/realtime_voice_proxy.py:166) 和 [同文件:189](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/realtime_voice_proxy.py:189) 直接执行诊断 callback，没有自身异常隔离。

纯函数探针实测：callback 在 `ticketStoreBegin` 抛异常，函数外泄 RuntimeError、fake store 调用 0；在 `ticketStoreWriteComplete` 抛异常，函数外泄 RuntimeError、fake store 调用 1；注入 logger 异常也外泄。普通 Python logger/handler 可能自行处理某些输出错误，不能据此断言生产必抛异常；已证明的是本层未实现 fail-open 合同，也没有可见的有界非阻塞/丢弃合同。

按生产代码推演，正常 PG 根 UoW 中写后未 commit 的诊断异常会进入回滚路径；在 UoW 已退出后的 `ticketStoreCommitted` 或 `responseReady` 记录处失败，则有可能已经提交票据但客户端得不到成功响应。**这些是源码路径推演，不是本轮 PG 回滚或提交实测。** 不得以此自动重发 ticket。

最小收尾：局部实现可控、非阻塞、失败不外泄的诊断适配；分别测试写前、写后未提交、提交后诊断失败及日志丢弃。不要改票据发放/保存业务语义。

## 2. commit 标签条件不够严谨；响应准备不等于发送完成

[main.py:7820](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7820) 在 UoW 正常退出且 write flag 为真、HTTP < 400 时记录 `ticketStoreCommitted`。但 [uow.py:121](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/db/uow.py:121) 支持 `rollback_reason` 非空时正常退出并回滚；当前日志条件没验证实际提交结果。

纯 AST middleware + fake rollback-only UoW + HTTP 200 的反例得到 `fake_uow_effect=rolledBack`，却记录 `ticketStoreCommitted`。这证明标签条件不足；不证明实际业务成功路由会触发这一组合，也不证明 PG 已做过此反例。应绑定明确事务结果，覆盖 rollback-only、commit 失败以及 commit 后归还连接失败。

`responseReady` 在 [main.py:7943](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7943) 发生于返回 response 之前，当前命名没有假称发送完成；[实现报告:28](/Users/gaominge/Documents/liftora/outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-01/implementation-report.md:28) 也承认该缺口。仍缺独立 auth/policy、pool/锁、snapshot 开始/结束、metrics 前后、实际 ASGI send 完成与取消阶段。当前测试只检查日志字符串存在，不验证完整顺序/时长或 commit：[test_credential_response_boundary.py:276](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_credential_response_boundary.py:276)。

## 3. MIC-15/16 与 D5 尚未完成

[矩阵:21](/Users/gaominge/Documents/liftora/outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-01/mic-matrix.md:21) 至 24 明确 PG ticket、有限阻塞注入和本版四场未运行。memory 测试无法验证取锁、事务提交、写前 401 无票据、并发消费及故障退出。

PG 不可用阻塞完整 MIC-15 和最终 PG 四场；但 metrics/auth/pool 的 fake 有限阻塞机制测试、诊断异常及 rollback-only 标签单元反例，可在独立进程加 watchdog 后先完成，不必等待 PG、手机或真实 Provider。本轮纯 AST 探针不等同完整 ASGI/D5 验收。

当前后端四个修改文件的 SHA-256 均与 [run-01 指纹](../../2026-09-27-dreamjourney-live-mic-start-repair/run-01/fingerprints.md) 相符；没有发现改动既有长记忆保存链的后端 diff。

## 4. PG 旧启动方式已找到，但原工具已不存在

历史 [commands.sh:4](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/C-capacity/commands.sh:4) 使用 `/private/tmp/dj-run03-pg/install/bin/initdb`、`pg_ctl` 创建专用 `djtest`/127.0.0.1:55520 cluster；[历史收尾报告:72](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/final-closure-report.md:72) 记录它已受控停止。

本轮只读确认 `/private/tmp/dj-run03-pg` 与 `/private/tmp/dj-closure-20260924-pg` 均不存在；PATH 中未找到 pg_ctl/initdb/psql/postgres/docker/brew/podman/colima，常见 Postgres.app、Docker.app、Homebrew 和 /Library/PostgreSQL 路径也未发现。因此不能靠原路径直接 restart；这是本地可重建的测试环境缺失，和手机、生产账号、真实 Provider 无关。当前按只读范围未安装或重建。

run-01 的 `Connection refused` 当前只在 [实现报告:25](/Users/gaominge/Documents/liftora/outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-01/implementation-report.md:25) 和矩阵中有陈述，交付目录未发现相应原始 nc/runner stdout、退出码与启动前检查材料。应补档后再声称这部分证据可独立复核；本輪未另发端口探测。

## 5. 最小收尾顺序

先修诊断 fail-open 与事务标签，补无需 PG 的受控错误/有限延迟测试；再恢复一个全新、专用本地 PG（导入 app 前隔离全部 store/sink 并拒绝外连），完成 MIC-15/16 和本版短长四场；把 PG 预检/执行/退出日志纳入 run-01。保留 `HISTORICAL_SLOW_START_CAUSE_UNRESOLVED`：合成阻塞复现不是 9/24 的现场首因证明。

探针脚本：[backend-diagnostic-contract-probe.py](backend-diagnostic-contract-probe.py)。实际输出：[backend-diagnostic-contract-probe.json](backend-diagnostic-contract-probe.json)。这两项仅证明 fake 依赖下的源码控制流。
