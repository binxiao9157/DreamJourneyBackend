# B 后端失败归类与 fixture 收尾报告

日期：2026-09-24。结果：**B_LOCAL_CLOSURE_PASS**。

## 1. 本次结论

原 run-03 后端全量中的 46 个错误，已逐项证明为同一类单测装配问题：API 合同测试导入了默认 PostgreSQL Store，却没有运行打开连接池的应用生命周期。旧 HEAD 和当前工作树在相同 venv、相同显式环境下，均可复现同样的 44 个方法 / 46 个 PoolClosed 错误。按仓库已有单测入口规定，在导入应用前选择内存 fixture 后，两版原方法均全部通过。

这次修复的是独立执行器的环境装配；未修改产品代码、原测试方法、预期断言或数据库生命周期语义。未发现这 46 项中的本轮新增产品回归。它们不能再被含糊归为“可能的旧问题”，但本结论也不是对历史真机 Live 保存失败原因的判断。

原有 7 个 259/260 路由计数失败继续列为已有原始旧 HEAD 对照支持的基线例外，未修改或豁免原断言。**未宣称后端全量全绿。**

## 2. 实际执行结果

旧代码来自 `git archive ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`，独立解压到 `/private/tmp/dj-B-closure-20260924-head-ffd02f37`；没有覆盖或回退当前 dirty 工作树。

全部使用 `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python`。启动器传入相同精简环境，子进程再次清除继承环境；不加载 `.env`，无真实账号、密钥或数据库配置。网络守卫拒绝 AF_INET / AF_INET6 连接，六组记录的网络尝试数均为 0。

|代码|装配|范围|结果|
|---|---|---|---|
|旧 HEAD|PostgresStore，普通 TestClient，无 lifespan|原 44 方法|46 PoolClosed，0 failure|
|当前工作树|同上|原 44 方法|46 PoolClosed，0 failure|
|旧 HEAD|导入前 STORE_BACKEND=memory|原 44 方法|44 PASS，0 skip/error/failure|
|当前工作树|同上|原 44 方法|44 PASS，0 skip/error/failure|
|旧 HEAD|同上|原错误所在四个完整模块及模块内前后序|180 PASS，0 skip/error/failure|
|当前工作树|同上|原错误所在四个完整模块及模块内前后序|180 PASS，0 skip/error/failure|

44 方法含一项 malformed-turn 方法的三个子测试，因此产生 46 个原错误记录。生成器已校验：每项方法对应的原错误数量、旧/当前错误类型、事务中间件栈，以及旧/当前定向与模块序列四份通过结果均匹配。新错误在 fresh process 中不依赖此前测试污染；完整四模块结果补充验证了相关前后序。

原失败所在四个测试文件与 `scripts/verify_backend.sh` 在旧 HEAD 与当前工作树中 SHA-256 完全相同。同一 venv 的 FastAPI、Starlette、httpx、psycopg 和 psycopg-pool 版本已逐一写入 JSON 并检查一致。

## 3. 可定位的装配链

1. [scripts/verify_backend.sh](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/verify_backend.sh:18) 已明确规定普通 unittest 使用 `STORE_BACKEND=memory`。因此选内存并非为消除 PostgreSQL 断言而新增的降级策略。
2. [Settings 默认配置](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/core/config.py:51) 默认为 postgres；`app.main` 导入时即构造全局 Store。
3. [连接池构造](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/db/pool.py:31) 明确 `open=False`；[API startup](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7912) 才调用 `init_store(store)`，shutdown 关闭它。
4. 原 API 合同用例普遍直接创建 `TestClient(app)`，例如 [ProfileAPITests](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_core_services.py:2852)，没有进入 TestClient 生命周期上下文。设置全局 `settings.store_backend` 也不会替换已经构造的 Store；[ProviderCostEvidenceRuntimeTests](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/tests/test_provider_cost_evidence.py:173) 就体现了这个边界。
5. [database_request_unit_of_work](/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/main.py:7790) 在目标 API 处理前取 PostgreSQL 连接，最终由未打开的 pool 抛出 PoolClosed。原始全量日志和两版复现栈都到达此位置。

原全量日志没有保存完整命令及环境快照，本次不会猜测原运行究竟由哪个终端环境或命令引入 postgres。可确定的是原错误栈已进入 PostgresStore，且 pool 未打开；这个状态已被同环境旧/新对照稳定重建。

## 4. 分类边界

44 方法的原意分别为账号/档案/家庭/信箱 API 合同、参数与权限校验、隐私脱敏、已 mock Provider 的错误隔离与 dry-run 合同。原方法不包含 PostgreSQL SQL 执行或事务持久化断言。完整逐项分类、源码位置及原错误行号见 `per-method-matrix.md` 和 `results/per-method-classification.json`。

本次没有把真正的 PostgreSQL 集成测试切换为内存。B 不启动数据库，也不使用 C 的数据库；实际 PG、默认 Worker、事务、候选审核到正式记忆的检查继续由独立 C 及既有 final-06 验收承担。B 的 180 PASS 只代表这四个模块，不能代替原 2675 项全量、真实 Provider 或真机。

`backend-core-isolated.log` 的 41 errors 是原 46 条中的 core 模块部分，属于旧的失败产物，并非先前已经通过的证明；这次新增的 `current-memory-four-modules-final.log` 才包含 core 在正确装配下的实际通过证据。

## 5. 已落实的局部修改及复用依据

新增文件全部在本 B 交付目录：

- `tools/run_isolated_contracts.py`：导入应用前固定独立 fixture，拒绝网络，保留原 unittest 方法与子测试，记录完整结果和源码/依赖指纹。
- `tools/run_comparison.py`：以相同进程环境执行旧/当前六组对照；对预期红、绿及无网络进行机器断言。
- `tools/summarize_comparison.py`：对照全部原错误，生成 44 方法逐项归因和机器可读结果。

未修改 backend/iOS 仓库的受保护依赖，无需由 B 使 final-06 的 short receipt 作废。最终是否复用 final-06 由汇总任务结合 A、C 改动后的指纹统一决定。

## 6. 证据入口

- [逐项机器结果](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend/results/per-method-classification.json)
- [逐项人读矩阵](per-method-matrix.md)
- [六组实际命令](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend/results/comparison-commands.json)
- [当前正确 fixture 44 方法日志](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend/logs/current-memory-44-final.log)
- [当前四模块 180 方法日志](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend/logs/current-memory-four-modules-final.log)
- [旧 HEAD 46 错误复现](/Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend/logs/head-postgres-44-final.log)
- [原七项基线证据](/Users/gaominge/Documents/liftora/outputs/2026-09-18-dreamjourney-live-candidate-contract-fix/run-01/evidence/independent-review/backend-head-baseline-seven-route-tests.log)

首轮快速验证日志也已保留，正式结论使用带 `-final` 后缀、由精简启动器环境运行的六组证据。未修改、清理原失败记录。

## 7. 可复制执行

保留的 `backend-head-ffd02f37.tar` 为旧 HEAD 源码归档。若临时解压目录已移除，先解压到该独立临时目录，再运行：

```sh
/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python -B \
  /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend/tools/run_comparison.py \
  --current /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend \
  --head /private/tmp/dj-B-closure-20260924-head-ffd02f37 \
  --python /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python \
  --output /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend

/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python -B \
  /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/B-backend/tools/summarize_comparison.py
```

新一轮执行应另建结果目录保存本轮日志，不覆盖本次审计证据。脚本不会设置真实凭据或连接外部服务。

## 8. 本轮状态边界

`B_LOCAL_CLOSURE_PASS / FULL_BACKEND_7_BASELINE_FAILURES_RETAINED / REAL_PROVIDER_NOT_RUN / DEVICE_NOT_RUN / DEPLOY_NOT_RUN`。

本 B 任务没有访问生产/历史数据，没有手机或 Provider 操作，没有数据库操作，没有 commit/push。总体 LOCAL_PASS 由 A、B、C 汇总验收决定。
