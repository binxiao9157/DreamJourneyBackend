# 声音复刻 2.0 试用音色槽位切换与调试说明

> 已废弃的调试分配方式：本文记录的“按 `voiceProfileId` 稳定选择槽位”仅用于历史排障。2026-07-10 起，生产合同使用 Postgres `voice_clone_slots` 独占分配；容量耗尽必须失败，槽位删除后退休，真实 `S_` ID 不再返回 iOS。

日期：2026-06-22
后端分支：`main`
当前提交：`805689e fix: support voice clone 2 trial slots`

## 1. 背景

当前声音复刻需要切到豆包/火山声音复刻 2.0 的试用音色槽位模式。2.0 赠送的试用能力不是后付费 `custom_speaker_id` 模式，而是控制台预生成的 `S_...` 音色槽位。

因此后端需要做到：

- 训练请求使用真实 `S_...` 音色 ID。
- 训练请求带 `model_type=5`。
- TTS 合成请求使用训练成功后的真实 `S_...` 作为 `audio.voice_type`。
- TTS 合成请求带 `Resource-Id=seed-icl-2.0`。
- iOS 不直连火山 API，不持有火山密钥。

## 2. 本次后端已完成

本次提交已完成以下改动：

- 新增 `trialSpeakerIdPool` 模式。
- 新增 `VOLCENGINE_VOICE_CLONE_SPEAKER_IDS`，支持配置多个控制台赠送的 `S_...` 音色槽位。
- 新增 `VOLCENGINE_VOICE_CLONE_MODEL_TYPE`，默认值为 `5`。
- 新增 `VOLCENGINE_VOICE_CLONE_TTS_RESOURCE_ID`，默认值为 `seed-icl-2.0`。
- 训练请求会从槽位池中按本地 `voiceProfileId` 稳定选择一个 `S_...`。
- 训练请求不再在试用槽位模式下传 `custom_speaker_id`。
- TTS 合成请求会发送 `Resource-Id=seed-icl-2.0`，不会发送 `X-Api-Resource-Id`。
- `/config/runtime` 增加 2.0 readiness 字段：
  - `speakerIdPoolConfigured`
  - `speakerIdPoolCount`
  - `modelType`
  - `ttsResourceId`
  - `voiceClone2TrialReady`
- 新增 dry-run 合同 smoke：
  - `scripts/voice_clone_2_contract_smoke.py`
  - `scripts/run-voice-clone-2-contract-smoke.sh`
- `scripts/verify_backend.sh` 已接入声音复刻 2.0 合同 smoke。

## 3. 服务器环境变量

服务器 `.env` 需要配置：

```bash
VOLCENGINE_VOICE_CLONE_API_KEY=<声音复刻训练/查询 x-api-key>
VOLCENGINE_VOICE_CLONE_TRAIN_URL=https://openspeech.bytedance.com/api/v3/tts/voice_clone
VOLCENGINE_VOICE_CLONE_QUERY_URL=https://openspeech.bytedance.com/api/v3/tts/get_voice
VOLCENGINE_VOICE_CLONE_UPGRADE_URL=https://openspeech.bytedance.com/api/v3/tts/upgrade_voice

VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE=trialSpeakerIdPool
VOLCENGINE_VOICE_CLONE_SPEAKER_IDS=<控制台赠送的 S_ 音色 ID 列表，逗号分隔>
VOLCENGINE_VOICE_CLONE_MODEL_TYPE=5

VOLCENGINE_VOICE_CLONE_TTS_API_KEY=<复刻音色 TTS 合成 x-api-key>
VOLCENGINE_VOICE_CLONE_TTS_URL=https://openspeech.bytedance.com/api/v1/tts
VOLCENGINE_VOICE_CLONE_TTS_CLUSTER=volcano_icl
VOLCENGINE_VOICE_CLONE_TTS_RESOURCE_ID=seed-icl-2.0
```

注意：

- 真实 `S_...` 音色 ID 不要写入 Git 仓库。
- 如果只配置一个 `S_...`，可以先做单用户调试。
- 如果配置多个 `S_...`，后端会按 `voiceProfileId` 稳定分配槽位。
- 训练/查询使用 `VOLCENGINE_VOICE_CLONE_API_KEY`。
- 合成使用 `VOLCENGINE_VOICE_CLONE_TTS_API_KEY`。
- 两个 key 是否可以相同取决于火山控制台实际权限；后端支持分开配置。

## 4. 请求合同

### 4.1 训练请求

试用槽位模式下，训练请求核心字段应为：

```json
{
  "speaker_id": "S_xxxxxxxx",
  "audio": {
    "data": "<base64 audio>",
    "format": "wav"
  },
  "language": 0,
  "model_type": 5,
  "extra_params": {
    "voice_clone_denoise_model_id": ""
  }
}
```

不应出现：

```json
{
  "speaker_id": "custom_speaker_id",
  "custom_speaker_id": "..."
}
```

### 4.2 合成请求

复刻音色 TTS 合成请求头应包含：

```text
x-api-key: <VOLCENGINE_VOICE_CLONE_TTS_API_KEY>
Content-Type: application/json
Resource-Id: seed-icl-2.0
```

请求体核心字段应为：

```json
{
  "app": {
    "cluster": "volcano_icl"
  },
  "user": {
    "uid": "<user id>"
  },
  "audio": {
    "voice_type": "S_xxxxxxxx",
    "encoding": "mp3",
    "speed_ratio": 1.0
  },
  "request": {
    "reqid": "<uuid>",
    "text": "要合成的文本",
    "operation": "query"
  }
}
```

不要发送：

```text
X-Api-Resource-Id: ...
```

## 5. 本地 dry-run 验证

本地 dry-run 不会调用火山接口，不会消耗训练次数，也不会打印 key。

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-voice-clone-2-contract-smoke.sh
```

也可以临时传入真实槽位列表验证合同形态：

```bash
VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE=trialSpeakerIdPool \
VOLCENGINE_VOICE_CLONE_SPEAKER_IDS='<S_音色ID列表，逗号分隔>' \
VOLCENGINE_VOICE_CLONE_MODEL_TYPE=5 \
VOLCENGINE_VOICE_CLONE_TTS_RESOURCE_ID=seed-icl-2.0 \
scripts/run-voice-clone-2-contract-smoke.sh
```

期望输出满足：

```json
{
  "voiceClone2TrialReady": true,
  "speakerIdMode": "trialSpeakerIdPool",
  "speakerIdPoolCount": 2,
  "trainingSpeakerId": "S_...",
  "trainingModelType": 5,
  "trainingHasCustomSpeakerId": false,
  "ttsResourceId": "seed-icl-2.0",
  "ttsHasXApiResourceId": false,
  "ttsVoiceType": "S_..."
}
```

## 6. 后端完整验证

提交前已运行：

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/verify_backend.sh
```

验证结果：

```text
Backend unittest: Ran 111 tests, OK
Backend py_compile: passed
Voice clone 2.0 contract smoke: passed
Backend FastAPI smoke: passed
Backend diff --check: passed
```

## 7. 部署后验证步骤

部署后先检查 runtime：

```bash
curl -sS "$BACKEND_BASE_URL/config/runtime" \
  -H "Authorization: Bearer $BACKEND_API_TOKEN" | jq '.voiceClone'
```

重点检查：

```json
{
  "speakerIdMode": "trialSpeakerIdPool",
  "speakerIdPoolConfigured": true,
  "speakerIdPoolCount": 2,
  "modelType": 5,
  "ttsResourceId": "seed-icl-2.0",
  "voiceClone2TrialReady": true
}
```

然后再做真实链路：

1. iOS 提交授权后的声音样本。
2. 后端调用火山训练接口。
3. 后端返回真实 `S_...` 音色 ID、`providerRequestId`、`providerLogId`。
4. 查询训练状态，等待状态变为成功或激活。
5. 使用该 `S_...` 调 `/voice/synthesis`。
6. 确认合成返回音频，并可在 iOS 播放。

## 8. 常见问题定位

### 训练失败：提示没有传入音色 ID

优先检查：

- `VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE` 是否为 `trialSpeakerIdPool`。
- `VOLCENGINE_VOICE_CLONE_SPEAKER_IDS` 是否配置真实 `S_...`。
- dry-run 输出里 `trainingSpeakerId` 是否以 `S_` 开头。
- dry-run 输出里 `trainingHasCustomSpeakerId` 是否为 `false`。

### 训练失败：资源未授权

优先检查：

- `VOLCENGINE_VOICE_CLONE_API_KEY` 是否属于声音复刻训练/查询服务。
- 控制台是否已开通声音复刻 2.0。
- `S_...` 槽位是否来自同一账号/同一服务。
- 当前槽位是否还有训练次数。

### 合成失败：resource mismatch 或 resource not granted

优先检查：

- `VOLCENGINE_VOICE_CLONE_TTS_API_KEY` 是否具备复刻音色 TTS 合成权限。
- 合成请求是否带 `Resource-Id=seed-icl-2.0`。
- 合成请求是否没有错误地发送 `X-Api-Resource-Id`。
- `audio.voice_type` 是否是真实训练成功的 `S_...`。

### UI 显示训练成功但无法合成

优先检查：

- 训练状态是否确实为成功或激活。
- iOS 保存的 `voiceProfileId` 是否是真实 `S_...`，不是本地 `voice_profile_...`。
- `/config/runtime.voiceClone.synthesisProviderReady` 是否为 `true`。

## 9. 当前边界

当前已经完成的是后端合同切换和 dry-run 调试，不等于真实火山训练已经验收通过。

还需要部署后继续完成：

- 使用真实授权音频样本训练。
- 查询 provider 状态和 `providerLogId`。
- 使用训练成功的 `S_...` 做 TTS 合成。
- iOS 真机验证训练、查询、合成、播放完整链路。
- 根据火山返回结果继续补充错误码映射。
