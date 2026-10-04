# 会中草稿重组重复合并 · run-01

- [分析与修复设计](../../../02-问题修复/记忆系统/2026-10-04-会中草稿重组重复合并/分析与修复设计.md)
- [实施与验证](../../../02-问题修复/记忆系统/2026-10-04-会中草稿重组重复合并/实施与验证.md)
- [现场限定证据](private-failure-evidence.json)；原失败不重放、不覆盖。
- [修前红](red.log)、[首次修后绿](green.log)、[最终178项回归](backend-regression-final.log)。
- [普通隔离链](pg-normal/pg-result.json)、[首次拒绝组链](pg-grouping/pg-result.json)、[最终同版拒绝组链](pg-final/pg-result.json)。
- [PG边界正确夹具6项](pg-boundaries-correct-fixture.log)、[首次夹具不匹配5过1错](pg-boundaries-final.log)。
- [iOS实际摘要](ios-summary.json)、[全用例结果](ios-tests.json)、[51项重点回归](ios-critical-tests.json)、[原始xcresult](ios-regression.xcresult)。
- [真实DeepSeek4次结果](provider-result.json)、[运行输出](provider-output.txt)。
- [最终源码指纹](final-fingerprints.json)、[本轮两运行文件diff](repair.diff)、[新增专项测试快照](new-regroup-tests.py)。

## 复现命令

在 `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`：

```sh
.venv/bin/python -m unittest -v tests.test_live_theme_regroup_boundaries tests.test_owner_truth_live_topics tests.test_owner_truth_live_theme_provider tests.test_live_theme_capacity tests.test_live_publication_fallback tests.test_live_independent_theme_semantics tests.test_live_independent_theme_regression tests.test_live_relation_review_applicability tests.test_live_correction_intent tests.test_live_relation_output_contract tests.test_owner_truth_live_recovery tests.test_owner_truth_live_long_memory_pipeline tests.test_realtime_voice_proxy
```

PG脚本 `pg-grouping.py` 使用 LOCAL_LIVE_TEST_ADMIN_DSN 指向专用本地PostgreSQL，在运行时创建独立数据库。它修改了short-A事实夹具为两用户轮，因此旧边界测试的固定sequence=3只能使用普通 `backend-live-recovery-run-terminal-smoke.py` 创建的两正文short-A数据库。

```sh
LOCAL_LIVE_TEST_ADMIN_DSN=postgresql://djtest@127.0.0.1:55548/postgres LOCAL_LIVE_TEST_OUTPUT=/private/tmp/dj-theme-regroup-replay .venv/bin/python /Users/gaominge/Documents/liftora/outputs/2026-10-04-private-theme-draft-repair/run-01/pg-grouping.py
```

iOS在现有DreamJourney.xcworkspace/scheme、iPhone17Pro iOS26.5模拟器执行：`-only-testing:DreamJourneyTests/OwnerTruthContractsTests -only-testing:DreamJourneyTests/AudioOwnerLeaseModelTests`。结果用 `xcrun xcresulttool get test-results summary --path <xcresult>` 核实。

真实Provider脚本 `provider-probe.py` 只用于授权环境；含真实模型调用，不应当作为普通本地回归自动执行。它使用两条全新合成事实，未写业务数据。

本轮：LOCAL_PASS / PROVIDER_REGROUP_PASS；部署、物理真机和火山Live均NOT_RUN。
