# 火山声音复刻 Key 分离配置说明

日期：2026-06-20

本文用于说明 DreamJourney 后端接入火山/豆包声音复刻时，为什么需要把不同能力的 key 拆开配置，以及服务器 `.env` 应该如何配置。文档只写变量名和用途，不记录任何真实密钥。

## 1. 结论

声音复刻至少涉及两组不同能力：

| 能力 | 后端用途 | 推荐环境变量 | 上游接口 |
| --- | --- | --- | --- |
| 声音复刻训练 | 提交用户授权后的声音样本，生成 `voiceProfileId` / `speaker_id` | `VOLCENGINE_VOICE_CLONE_API_KEY` | `POST https://openspeech.bytedance.com/api/v3/tts/voice_clone` |
| 声音复刻查询 | 查询训练状态、样本状态、模型状态 | `VOLCENGINE_VOICE_CLONE_API_KEY` | `POST https://openspeech.bytedance.com/api/v3/tts/get_voice` |
| 声音复刻升级 | 仅用于旧版音色升级到新版模型，当前不是主链路 | `VOLCENGINE_VOICE_CLONE_API_KEY` | `POST https://openspeech.bytedance.com/api/v3/tts/upgrade_voice` |
| 复刻音色 TTS 合成 | 使用已训练好的 `voiceProfileId` 生成音频 | `VOLCENGINE_VOICE_CLONE_TTS_API_KEY` | `POST https://openspeech.bytedance.com/api/v1/tts` |

建议后端把训练/查询/升级 key 和 TTS 合成 key 明确拆开，不要让 `/voice/profiles` 和 `/voice/synthesis` 共用一个不确定来源的 key。这样可以避免训练接口可用、合成接口却报资源未授权时，误以为是同一个配置问题。

## 2. 为什么要拆开

火山官方声音复刻文档覆盖的是音色训练、查询和升级：

- [声音复刻-音色训练](https://www.volcengine.com/docs/6561/2534906?lang=zh)
- [声音复刻-音色查询](https://www.volcengine.com/docs/6561/2535742?lang=zh)
- [声音复刻-音色升级](https://www.volcengine.com/docs/6561/2535751?lang=zh)

复刻音色的 TTS 合成走的是 HTTP TTS `/api/v1/tts` 调用形态，请求体里把训练得到的 `voiceProfileId` / `speaker_id` 放到 `audio.voice_type`。它的授权、资源开通和训练/查询接口不一定完全相同。

线上曾出现过这类现象：

- `/config/runtime` 显示声音复刻训练 provider 已配置。
- `/voice/profiles` 的无音频合同 smoke 可以通过。
- `/voice/synthesis` 对假 `voiceProfileId` 能正确打到 `/api/v1/tts`，但上游返回资源未授权类错误。

这说明后端代码路径基本正确，但 TTS 合成侧 key 或资源授权未必和训练侧一致。拆开 key 后，问题边界会更清晰：

- 训练失败：优先查 `VOLCENGINE_VOICE_CLONE_API_KEY` 和训练/查询接口权限。
- 合成失败：优先查 `VOLCENGINE_VOICE_CLONE_TTS_API_KEY`、HTTP TTS 权限、`volcano_icl` cluster 权限，以及 clone voice id 是否属于同一账号/资源。

## 3. 推荐服务器 `.env`

```dotenv
# 普通 TTS / 默认语音，非声音复刻专用
VOLCENGINE_API_KEY=<volcengine normal tts api key>
VOLCENGINE_VOICE_TYPE=zh_female_cancan_mars_bigtts

# 实时对话，不用于声音复刻训练或复刻 TTS
VOLCENGINE_APP_ID=<volcengine realtime app id>
VOLCENGINE_APP_KEY=<volcengine realtime app key>
VOLCENGINE_APP_TOKEN=<volcengine realtime access token>
VOLCENGINE_REALTIME_RESOURCE_ID=volc.speech.dialog
VOLCENGINE_REALTIME_ADDRESS=wss://openspeech.bytedance.com
VOLCENGINE_REALTIME_URI=/api/v3/realtime/dialogue

# 声音复刻训练 / 查询 / 升级
VOLCENGINE_VOICE_CLONE_API_KEY=<volcengine voice clone x-api-key>
VOLCENGINE_VOICE_CLONE_TRAIN_URL=https://openspeech.bytedance.com/api/v3/tts/voice_clone
VOLCENGINE_VOICE_CLONE_QUERY_URL=https://openspeech.bytedance.com/api/v3/tts/get_voice
VOLCENGINE_VOICE_CLONE_UPGRADE_URL=https://openspeech.bytedance.com/api/v3/tts/upgrade_voice
VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE=customSpeakerId
# 预付费/免费音色模式才需要填写控制台生成的真实 S_ 音色 ID。
# VOLCENGINE_VOICE_CLONE_SPEAKER_ID=S_xxxxxxxx
# 声音复刻 2.0 赠送试用槽位建议使用槽位池模式。
# VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE=trialSpeakerIdPool
# VOLCENGINE_VOICE_CLONE_SPEAKER_IDS=S_xxxxxxxx,S_yyyyyyyy
VOLCENGINE_VOICE_CLONE_MODEL_TYPE=5

# 复刻音色 TTS 合成，建议独立配置，不与训练 key 混用
VOLCENGINE_VOICE_CLONE_TTS_API_KEY=<volcengine voice clone tts x-api-key>
VOLCENGINE_VOICE_CLONE_TTS_URL=https://openspeech.bytedance.com/api/v1/tts
VOLCENGINE_VOICE_CLONE_TTS_CLUSTER=volcano_icl
VOLCENGINE_VOICE_CLONE_TTS_RESOURCE_ID=seed-icl-2.0
```

后端代码需要支持 `VOLCENGINE_VOICE_CLONE_TTS_API_KEY`。补完后：

- `/voice/profiles`、`/voice/profiles/{user_id}/{voice_profile_id}/refresh` 只消费 `VOLCENGINE_VOICE_CLONE_API_KEY`。
- `/voice/synthesis` 只消费 `VOLCENGINE_VOICE_CLONE_TTS_API_KEY`。
- `/config/runtime` 应分别暴露训练 provider readiness 和 synthesis provider readiness，避免把“能训练”误显示成“能合成”。

## 4. 请求头和请求体边界

### 训练 / 查询 / 升级

声音复刻训练、查询、升级接口使用：

```text
Content-Type: application/json
X-Api-Key: <VOLCENGINE_VOICE_CLONE_API_KEY>
X-Api-Request-Id: <uuid>
```

后付费自定义音色模式，即 `VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE=customSpeakerId`，训练请求核心字段：

```json
{
  "speaker_id": "custom_speaker_id",
  "custom_speaker_id": "<voiceProfileId>",
  "audio": {
    "data": "<base64 audio>",
    "format": "wav"
  },
  "language": 0,
  "extra_params": {
    "voice_clone_denoise_model_id": "..."
  }
}
```

注意：按火山声音复刻 V3 文档，使用自定义音色时 `speaker_id` 必须传固定值 `custom_speaker_id`，真实自定义音色 ID 写在 `custom_speaker_id` 字段中；不要留空。

预付费/免费音色模式，即 `VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE=consoleSpeakerId`，训练请求核心字段：

```json
{
  "speaker_id": "<控制台生成的 S_ 音色 ID>",
  "audio": {
    "data": "<base64 audio>",
    "format": "wav"
  },
  "language": 0,
  "model_type": 5,
  "extra_params": {
    "voice_clone_denoise_model_id": "..."
  }
}
```

此模式必须在服务器 `.env` 配置 `VOLCENGINE_VOICE_CLONE_SPEAKER_ID`，不要使用 iOS 本地随机生成的 `S_` ID。

声音复刻 2.0 赠送的 10 个试用音色建议使用 `VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE=trialSpeakerIdPool`，并把控制台赠送的多个 `S_` ID 写入 `VOLCENGINE_VOICE_CLONE_SPEAKER_IDS`。后端会按本地 `voiceProfileId` 稳定选择一个槽位，训练成功后把真实 `S_` ID 返回给 iOS 保存。此调试模式不在 iOS 保存火山密钥。

查询请求核心字段：

```json
{
  "speaker_id": "<voiceProfileId>"
}
```

### 复刻音色 TTS 合成

复刻音色 TTS 合成使用：

```text
Content-Type: application/json
x-api-key: <VOLCENGINE_VOICE_CLONE_TTS_API_KEY>
Resource-Id: seed-icl-2.0
```

请求体核心字段：

```json
{
  "app": {
    "cluster": "volcano_icl"
  },
  "user": {
    "uid": "<user id>"
  },
  "audio": {
    "voice_type": "<voiceProfileId>",
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

## 5. 明确不要做的事

- 不要把 `VOLCENGINE_APP_TOKEN` 当成声音复刻 TTS 的 `x-api-key`。
- 不要把实时对话的 `VOLCENGINE_REALTIME_RESOURCE_ID` 用到声音复刻训练或合成。
- 不要给声音复刻训练、查询接口追加 `X-Api-Resource-Id`。
- 不要在当前链路里给声音复刻训练、查询或 `/api/v1/tts` 合成请求强行追加 `X-Api-Resource-Id`。
- 声音复刻 2.0 合成要按当前接入合同给 `/api/v1/tts` 追加 `Resource-Id=seed-icl-2.0`；不要写成 `X-Api-Resource-Id`。
- 不要把 key 写进 iOS 工程、Git 仓库、聊天记录或 issue。

## 6. 部署后验证

部署后先验证 runtime：

```bash
curl -sS "$BACKEND_BASE_URL/config/runtime" \
  -H "Authorization: Bearer $BACKEND_API_TOKEN" | jq '.voiceClone'
```

部署前可先跑本地声音复刻 2.0 合同 dry-run，不会调用火山接口，也不会打印 key：

```bash
PYTHONPATH=. scripts/run-voice-clone-2-contract-smoke.sh
```

输出应看到 `trainingSpeakerId` 为 `S_` 音色 ID、`trainingModelType=5`、`ttsResourceId=seed-icl-2.0`，且 `trainingHasCustomSpeakerId=false`、`ttsHasXApiResourceId=false`。

预期能区分：

- `realProviderReady`：声音复刻训练/查询 provider 是否可用。
- `synthesisProviderReady`：复刻音色 TTS 合成 provider 是否可用。
- `lipSyncTimeline`：复刻音色 TTS 可选口型时间轴合同。当前 `supported=false`，表示现有火山 HTTP TTS 链路不保证返回真实 phoneme/viseme 时间戳；iOS 应继续用播放器音量 metering 作为数字人口型降级。未来 provider 如果返回 `visemeTimeline`，后端会清洗并随 `/voice/synthesis` 原样回传给 iOS。

再验证无音频合同链路：

```bash
PYTHONPATH=. BACKEND_BASE_URL="$BACKEND_BASE_URL" BACKEND_API_TOKEN="$BACKEND_API_TOKEN" \
python3 scripts/backend-family-voice-contract-smoke.py
```

最后用真实授权音频样本验证训练，用已训练成功的 `voiceProfileId` 验证 `/voice/synthesis`。如果只是用随机 `voiceProfileId`，上游返回“speaker/resource 不匹配”是合理失败，不代表真实训练链路失败。

`/voice/synthesis` 使用 `outputMode=tencentAudioDrive` 时，后端会把 provider 音频转换成腾讯数智人 audio-drive 可消费的 PCM，并返回可观测字段：

```json
{
  "status": "synthesized",
  "voiceProfileId": "S_example",
  "providerMode": "volcengineVoiceCloneV1TTS",
  "outputMode": "tencentAudioDrive",
  "providerRequestId": "req-...",
  "providerLogId": "log-...",
  "audio": {
    "encoding": "base64",
    "format": "pcm16kMono",
    "sampleRate": 16000,
    "bitsPerSample": 16,
    "channelCount": 1,
    "byteCount": 32000,
    "durationSeconds": 1.0,
    "data": "<base64 pcm>"
  }
}
```

如果 provider 不可用、音色资源不匹配或音频无法转换为腾讯 audio-drive PCM，接口返回明确错误，不会静默降级成默认音色。

`/voice/synthesis` 响应中的 `visemeTimeline` 是可选字段，格式如下。当前 provider 不返回时该字段为 `null`。

```json
{
  "visemeTimeline": {
    "source": "providerVisemeTimeline",
    "duration": 1.2,
    "frames": [
      {"timeOffset": 0.0, "mouthShape": "neutral", "intensity": 0.1},
      {"timeOffset": 0.12, "mouthShape": "aa", "intensity": 0.85}
    ]
  }
}
```

如果训练或查询失败，后端会在 voice profile 合同中返回并持久化：

- `providerRequestId`：本次请求发给火山的 `X-Api-Request-Id`。
- `providerLogId`：火山响应头中的 `X-Tt-Logid`，可直接提供给火山支持排查上游日志。
- `providerMessage`：裁剪后的上游错误信息，不包含密钥或原始音频。

## 7. 常见错误判断

| 现象 | 优先判断 |
| --- | --- |
| `/voice/profiles` 返回 provider 未配置 | 检查 `VOLCENGINE_VOICE_CLONE_API_KEY` 是否配置 |
| 训练返回 `Invalid X-Api-Key` | 检查训练 key 是否来自声音复刻服务，而不是普通 TTS 或实时对话 |
| 训练返回 `[resource_id=volc.megatts.timbre] requested resource not granted` | 如果账号使用预付费/免费音色模式，配置 `VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE=consoleSpeakerId` 和控制台生成的 `VOLCENGINE_VOICE_CLONE_SPEAKER_ID`；如果使用后付费自定义模式，检查 `volc.megatts.timbre` 资源权限 |
| 查询返回 speaker/resource mismatch | 检查 `voiceProfileId` 是否由同一账号、同一声音复刻资源训练得到 |
| `/voice/synthesis` 返回 resource not granted | 检查 `VOLCENGINE_VOICE_CLONE_TTS_API_KEY`、HTTP TTS 权限、`volcano_icl` cluster 权限 |
| iOS 提示生产语音服务未完成配置 | 检查 `/config/runtime` 中 synthesis readiness，不要只看训练 readiness |

## 8. 后续代码要求

后端应保持以下合同：

- iOS 不直连火山声音复刻训练、查询、升级或复刻 TTS API。
- 所有火山 key 只存在服务器 `.env`。
- `/voice/profiles` 返回的 provider 错误可以用于 QA，但不得泄露请求头、key、原始音频路径或音频 base64。
- `/voice/synthesis` 返回的错误可以说明 provider 不可用或资源未授权，但不得回传上游密钥。
- release 文档、`.env.example` 和 `/config/runtime` 字段必须同步更新，避免部署时误以为一组 key 可以覆盖所有声音能力。
