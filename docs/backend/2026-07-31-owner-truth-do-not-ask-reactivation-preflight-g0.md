# Owner Truth `doNotAsk` 自然重开确认预检（G0）

## 范围

`DoNotAskReactivationDetector` 和
`OwnerTruthDoNotAskReactivationPreflightService` 只服务于已认证 Owner 的正式自然叙述写入前路径。
它们仅识别明确的“愿意重新聊 / 可以继续问这个话题”等重开表达，并且不保存、返回或记录输入正文，不调用模型或 Provider。

## 默认关闭与边界

默认配置：

```dotenv
OWNER_TRUTH_DO_NOT_ASK_REACTIVATION_PREFLIGHT_ENABLED=false
```

开关开启后，只有当前已持久化为 `paused + doNotAsk` 的 Owner Session 命中明确重开表达时，`POST /v2/vaults/{vault_id}/owner-truth/interview/narrative` 才返回 `409`：

- `persisted: false`
- `nextAction: restoreDoNotAsk`
- `preflight.status: confirmationRequired`
- `preflight.reasonCode: doNotAskRestoreConfirmationRequired`

该预检不会自动解除禁问、不会写入当前输入，也不会创建 Thread、Candidate、MemoryVersion 或 Provider 效果。真正恢复仍必须调用既有 Owner 专用 `restore-do-not-ask` 命令并带 `confirmed=true`。

安全覆盖优先于预检：高风险自然输入仍由既有 `SafetyPolicy` 返回无正文安全 override，不会被本功能改写。

## 本地验证

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_do_not_ask_reactivation_preflight \
  tests.test_owner_truth_interview_orchestration \
  tests.test_owner_truth_interview_session_orchestration \
  tests.test_owner_truth_interview_input_api.OwnerTruthInterviewInputAPITests.test_natural_input_do_not_ask_reactivation_preflight_is_default_off_and_write_free \
  tests.test_owner_truth_interview_input_api.OwnerTruthInterviewInputAPITests.test_do_not_ask_requires_explicit_confirmation_before_the_owner_can_restore
```

覆盖默认关闭时的旧冲突语义、明确/否定/歧义表达、跨 Owner 拒绝、重复提交零写入，以及必须经显式确认恢复的既有合同。

本项只形成 G0 本地安全合同；未部署、未开放公开 Echo UI，也不构成 M0-A 或发布 Gate 完成声明。
