# DreamJourney B 增强记忆：关键回归与执行证据

日期：2026-09-08。以下均为本地执行的真实命令；测试替身、内存仓储和静态 migration 合同不等于真实 PostgreSQL、模型或设备验收。

## 这次被固定的失败点

1. 家人/文档/未知来源不得被静默覆写成 Owner 第一人称事实。
2. Live 回合不得因重连、重复发送、跨产品会话或缺失尾部回合提前关闭、重复整理或串档案。
3. ChangeSet 审核不得在过期 revision、依赖组不完整或投影延迟时产生重复/部分正式事实。
4. 已替代、未就绪、争议、无权或撤权的记录不得进入文字、Live、整体人生记录或自传上下文。
5. 中文“饮食偏好/口味/学历”等自然问法要找到正确事实；“喜欢什么颜色”不能由“喜欢读书”误召回。
6. 文本 recent turns 只能作为当场回答消歧，不能跨产品会话泄露或直接沉淀为正式事实。
7. Live 快照必须与服务端票据的 checkpoint、授权 epoch 和 context hash 完全一致；不一致时 iOS 不启动。
8. 数据库池不可用时，文档页与只读运行观测不应因无写入需求而错误变成 503。

## 后端核心回归

工作目录：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`

```text
env PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/private/tmp/dreamjourney-pycache \
  .venv/bin/python -m unittest -q \
  tests.test_owner_truth_domain \
  tests.test_owner_truth_candidate_extraction_worker \
  tests.test_owner_truth_memory_changeset \
  tests.test_owner_truth_memory_changeset_activation \
  tests.test_owner_truth_memory_changeset_migration_contract \
  tests.test_owner_truth_memory_changeset_review \
  tests.test_owner_truth_candidate_review_api \
  tests.test_owner_truth_conversation \
  tests.test_owner_truth_interview_input_api \
  tests.test_owner_truth_live_delivery_migration_contract \
  tests.test_owner_truth_product_session_migration_contract \
  tests.test_owner_truth_formal_fact_eligibility \
  tests.test_owner_truth_memory_search \
  tests.test_owner_truth_echo_conversation_context \
  tests.test_owner_truth_echo_text_context_migration_contract \
  tests.test_echo_answer \
  tests.test_formal_memory_conversation_snapshot \
  tests.test_owner_truth_person_memory_profile \
  tests.test_owner_truth_projection_rights_fence \
  tests.test_owner_truth_legacy_migration \
  tests.test_owner_truth_legacy_migration_api \
  tests.test_owner_truth_legacy_backfill_plan \
  tests.test_owner_truth_legacy_backfill_service \
  tests.test_owner_truth_legacy_shadow_parity \
  tests.test_owner_truth_legacy_tail_shadow \
  tests.test_owner_truth_legacy_tail_shadow_service \
  tests.test_owner_truth_b_memory_quality_fixture \
  tests.test_operation_metrics \
  tests.test_operation_metric_coverage
```

结果：`Ran 208 tests in 9.626s ... OK`。

## 后端补充闭环回归

```text
env PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/private/tmp/dreamjourney-pycache \
  .venv/bin/python -m unittest -q \
  tests.test_owner_truth_family_contribution \
  tests.test_owner_truth_context_authority \
  tests.test_owner_truth_media_processing_worker \
  tests.test_owner_truth_memory_projection \
  tests.test_owner_truth_interview_review_batch \
  tests.test_owner_truth_interview_candidate_proposal \
  tests.test_owner_truth_interview_candidate_batch_decision \
  tests.test_owner_truth_interview_session_outcome_read \
  tests.test_owner_truth_interview_session_orchestration \
  tests.test_owner_truth_formal_memory
```

结果：`Ran 71 tests in 3.081s ... OK`。

## DFX 指标中间件回归

```text
env PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/private/tmp/dreamjourney-pycache \
  .venv/bin/python -m unittest -q \
  tests.test_operation_metrics \
  tests.test_operation_metric_coverage
```

结果：`Ran 16 tests in 0.635s ... OK`。覆盖了成功、拒绝、反馈缺失、重试、指标 sink 故障和只读 API 在数据库池未启时的行为。

## iOS 模拟器合同回归

工作目录：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`

```text
xcodebuild test -quiet \
  -workspace DreamJourney.xcworkspace \
  -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=8C90FF12-82E3-41A6-A003-EE0BB26BEAA6' \
  -derivedDataPath /private/tmp/DreamJourney-B-memory-test \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/testInterviewLiveDeliveryCarriesStableSequenceAndCloseWatermark \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/testLiveTurnOutboxPersistsReplaysAndIsolatesAccountScope \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/testInterviewLiveUseCaseQueriesTheSelectedProductSession \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/testInterviewLiveUseCaseRejectsCurrentSessionFromAnotherProductSession \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/testInterviewNaturalInputUseCaseSubmitsLiveAssistantTurnWithContextOnlyRole \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/testRealtimeVoiceRuntimeConfigAcceptsOnlyFutureBackendProxyTicket \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/testRealtimeVoiceRuntimeConfigFailsClosedForDirectExpiredOrIncompleteContract \
  -only-testing:DreamJourneyTests/AudioOwnerLeaseModelTests
```

结果：`xcodebuild` 退出码 `0`，选择器执行成功。它验证 DTO、发送箱和快照合同，不是火山 SDK 或 iPhone 上的音频体验证据。

## 未运行或不能由这些测试替代的项目

- `unittest discover` 的全仓结果不作为本报告结论：其会触及未启动的 PostgreSQL 池；本次没有启动或清理数据库以换取表面绿色。
- 未运行真实 PostgreSQL migration、唯一索引/CAS 并发、回滚、pgvector、连接池耗尽或恢复演练。
- 未运行真实 DeepSeek、embedding 或火山 SDK，也未使用真实用户内容。
- 未运行 iPhone、断网、锁屏、Live 打断、持续聆听、上下文热更新/供应商撤权、压测或成本计量。
