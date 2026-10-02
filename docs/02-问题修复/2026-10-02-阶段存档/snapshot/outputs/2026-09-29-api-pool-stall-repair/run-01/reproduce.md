# 本地复现与核验

以下均不调用真实 Provider，不连接手机，不访问生产。真实 PG 脚本只允许本文所用本地 55520 端口，在随机新库内操作，结束删除自身库。

```sh
cd /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend
.venv/bin/python -m unittest tests.test_api_pool_isolation tests.test_db_uow tests.test_operation_metrics tests.test_voice_launch_diagnostics tests.test_realtime_voice_proxy tests.test_credential_response_boundary tests.test_postgres_store
```

为了保留当前证据，重复 PG 脚本前将脚本、entry 和 baseline 复制到一个新的输出目录。不要覆盖本次红绿结果。默认 API 对照脚本运行 red 和 green 两组，均做正常应用启动/关闭。

```sh
/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python <新目录>/pg_default_api_probe.py
/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python <新目录>/pg_transaction_probe.py
```

最终四场，使用全新输出目录：

```sh
/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python \
 /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/tools/run_round_simulation.py \
 --admin-dsn postgresql://djtest@127.0.0.1:55520/postgres \
 --output-root <新目录>/four-scene \
 --scenario all
```

真实 Provider、手机短场、物理 20 分钟、声学通道和未来 65 分钟均有独立验收边界。短场未完整通过时不得执行长场，不得用上述逻辑时长冒充真机结果。
