# A0 Provider Runtime Capability Contract

日期：2026-08-07  
状态：`IMPLEMENTED_PENDING_DEPLOYED_SMOKE`

## 目的

`GET /config/runtime` 现在基于同一份启动时 Provider 清单返回真实可用性，而不是让 iOS 根据环境变量、旧 mock 合同或本地开关自行猜测。该清单只验证配置是否完整；它不把“配置存在”写成“Provider 已通过外部验收”。

启动时配置不完整只关闭对应能力，API 仍可启动，其他能力不受影响。

## 公开字段

每个 `capabilitySnapshots` 项都包含以下脱敏字段：

- `enabled` / `providerReady`
- `provider` / `providerKind`
- `operation` / `dataClass`
- `region` / `retentionPolicyVersion`
- `fallbackMode` / `reason`
- `configurationStatus` / `evidenceStatus`

`providerInventory` 是相同决策的启动校验摘要，包含 `validatedAtStartup=true`，用于部署 smoke 对照。两者不包含密钥、bucket、endpoint、对象 key、音色 ID、手机号或用户内容。

## 配置矩阵

| Capability | 需要完整配置才会 ready | 未完成时的 fail-closed 原因 | 当前客户端行为 |
| --- | --- | --- | --- |
| `ownerTruthMediaStorage` | 启用采集；私有存储配置有效；内容安全扫描已选定 | `providerConfigurationIncomplete`、`contentSafetyProviderUnavailable`、`storageProviderUnsupported` | M0 私有媒体入口保持关闭 |
| `ownerTruthMediaProcessing` | 已有可用私有存储；异步 effect/worker 与处理 worker 均启用 | `storageProviderUnavailable`、`asyncEffectWorkerUnavailable`、`workerDisabled` | 不启动处理或 Candidate handoff |
| `identityChallenge` | 身份绑定 HMAC 和已选 OTP adapter 必需项完整 | `providerConfigurationIncomplete` 或 `runtimeDisabled` | 登录挑战不启动 |
| `voiceCloneShell` | 训练/查询 key 与复刻 TTS key 均完整 | `runtimeDisabled` 或 `synthesisProviderUnavailable` | 默认关闭，不伪造默认音色成功 |
| `digitalHumanLivePanel` | 即使项目凭据存在，也还需要受验证的 scoped-session broker | `scopedSessionCredentialContractNotVerified` | 回退文字 Echo，不放开移动端静态凭据 |

`archiveMediaUploadIntent` 仍是历史 mock 元数据同步合同，和 `ownerTruthMediaStorage` 分开。它不能作为 M0 真实媒体已接入的证据。

## iOS 消费

`OwnerTruthMediaRuntimeCapability` 只从 `/config/runtime` 解析：

1. `ownerTruthMedia.captureCapability` 和 `processingCapability` 必须与能力快照一致。
2. 只有 Provider ready、快照完整且服务端 `releaseVisible=true` 时，`canOpenCapture` 才为真。
3. 缺少字段、旧服务端或配置不完整一律保守关闭。

## 验证

本地：

```bash
.venv/bin/python -m unittest tests.test_runtime_capabilities -v
BACKEND_BASE_URL=https://dreamjourney-api.liftora.cn ./scripts/run-backend-runtime-capability-deployed-smoke.sh
```

iOS 类型 smoke：

```bash
./Scripts/QA/prd-stitch-ui/run-runtime-capability-snapshot-model-smoke.sh
./Scripts/QA/product-v4/run-owner-truth-media-runtime-capability-smoke.sh
```

部署后，`/ready` 必须为 `200`，并且 deployed smoke 必须证明 `providerInventory.validatedAtStartup=true`。
