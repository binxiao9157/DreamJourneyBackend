# Voice/DH 被拒绝样本意图 G0 合同

对应 V4 `WI-V0-01-03` 的后续 G0 子切片。该切片只补齐可审计的
`SampleIntent` 拒绝链，不代表已创建受管样本、已训练音色、已生成试听音频，
或已开放声音复刻功能。

## 已实现

- 新增 `0043_voice_dh_blocked_sample_intent_receipts` 迁移。
  - `sample_intents` 只能绑定当前激活 Vault 中已经 `blocked` 的本人训练
    profile；Owner、actor、subject、authority epoch、purpose、provider 必须完全一致。
  - 样本只允许 `wav`、`mp3`、`m4a` 的哈希描述，且时长必须大于零。
  - 样本意图自身固定为 `status=blocked`，表和回执仍保持 append-only。
  - `authority_receipts` 现在可精确绑定 `sampleIntent`，但仍拒绝
    `generatedAudioIntent`、`dhSessionAdmission` 等未来资源类型。
- `VoiceDHBlockedSampleIntentCommand.from_synthetic_preflight(...)` 只接受已有的
  synthetic G0 preflight：当前 Vault/Owner/actor/subject/epoch、训练用途、火山
  Provider、profile id/version 和确定性 profile version ID 都必须相符。
- 预检请求哈希仅作为 sample intent 的 `payload_hash` 保存；数据库不保存音频、
  URL、对象存储位置、质量内容、Provider speaker id、token 或训练 payload。
- `PostgresVoiceDHAuthorityRepository` 用
  `(vault_id, command_id_hash)` 和匹配回执完成数据库幂等；相同命令 deduplicate，
  不同内容复用命令会冲突。
- 全部 result 固定声明：`providerEffectAllowed=false`、
  `providerEffectPerformed=false`、`sampleObjectCreated=false`、
  `trainingCommandCreated=false`、`releaseVisible=false`。

## 明确未做

- 没有公开 API、iOS UI、ReleasePolicy 提升、对象存储、录音、真实媒体扫描、
  火山训练、试听合成、Provider credential 或网络调用。
- 这里的在世成年人、随机声明、活体和质量只来自 synthetic 前置条件；它们不是
  真实身份、活体或音频质量证据。
- 不把被拒绝的 SampleIntent 视为 `SampleObject`，也不把它视为可用音色。

## 验证

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
PYTHON_BIN=.venv/bin/python scripts/run-backend-voice-dh-blocked-sample-intent-g0-gate.sh
RUN_VOICE_DH_BLOCKED_SAMPLE_INTENT_POSTGRES_SMOKE=1 \
  PYTHON_BIN=.venv/bin/python scripts/run-backend-voice-dh-blocked-sample-intent-g0-gate.sh
PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh
```

本地静态 gate 和完整 `verify_backend.sh` 已通过。第二条命令需要一个能创建
disposable Postgres database 的 `DATABASE_URL`；部署后应在 API 容器环境中运行，
并确认输出 `sampleStatus=blocked`、`receiptCount=2` 且所有 Provider/SampleObject/
training effect 均为 false。

## 剩余 Gate

`G2` 仍需要受管 SourceObject、真实扫描、留存和数据安全证据；`G3` 仍需要真实
Provider 训练、删除、成本、重放/unknown reconciliation 证据；`G4` 仍需要真机、
隐私、法律和发布批准。本切片不降低这些门槛。
