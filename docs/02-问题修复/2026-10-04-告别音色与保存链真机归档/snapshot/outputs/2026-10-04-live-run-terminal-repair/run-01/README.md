# 发布后Run终态修复：本地证据

- 设计：../../../02-问题修复/记忆系统/2026-10-04-发布后Run终态未收敛/分析与修复设计.md
- 修前 `red-pg.log`：默认Worker已发布snapshot，但历史基准短场run=organizing，同断言红。
- 最终集成 `final-smoke-02.log`、`final/pg-result.json`：可复用脚本在新隔离PG执行。
- `pg-boundaries-final.log`：事务、身份、未完工作与并发行锁边界。
- `regression-final.log`：相关回归；40×1000历史矩阵压力项不计通过，原中断栈留在regression-02.log。
- `fingerprints.json`：最终四个源码/测试/工具文件指纹。
- `recovery-before.py`、`worker-before.py`：修前原文件，仅证据，不用于线上回填。

中途尝试全部保留：green-pg.log遇到受控模型缺themeRelationScreen协议；green-pg-03.log手动tick未通过完整scan规划尾批，产生draftCoverageMissing；更正测试入口为默认scan后通过。pg-boundaries.log/regression.log使用了环境没有安装的pytest；已改为项目自带unittest，不新增依赖。以上不冒充产品通过，也不修改原始失败记录。

## 复现（仅本机临时PostgreSQL）

在Backend仓库执行：

```sh
LOCAL_LIVE_TEST_ADMIN_DSN=postgresql://djtest@127.0.0.1:55548/postgres \
LOCAL_LIVE_TEST_OUTPUT=/private/tmp/live-run-terminal-result \
.venv/bin/python scripts/backend-live-recovery-run-terminal-smoke.py
```

脚本只接受localhost/127.0.0.1，为每次执行新建dj_live_recovery_*数据库，走真实本机HTTP、普通API lifespan、默认Worker、持久化与正式确认；模型响应来自本地受控HTTP。根据输出数据库名执行：

```sh
TEST_LIVE_RECOVERY_DSN=postgresql://djtest@127.0.0.1:55548/dj_live_recovery_替换输出名 \
.venv/bin/python -m unittest tests.test_live_recovery_run_terminal_postgres -v
```

边界测试含6个unittest入口、身份及未完工作各4个子场景；仓储并发使用真实行锁，受控坐标故障在finally恢复。它不替代默认HTTP迟到正文链（另由集成脚本验证）。全部内容是合成测试；没有真实Provider、手机、生产或历史业务数据操作。
