# DreamJourneyBackend 服务器部署手册

更新时间：2026-06-19

> 本文只记录部署步骤、变量名和验证命令，不记录真实 token、Secret Key、Access Token、数据库密码或第三方密钥。

## 1. 部署目标

本次服务器部署目标是让线上后端包含以下能力：

- 后端 `main` 至少包含提交 `0752b07 feat: proxy voice clone provider through backend`，并包含后续 `/voice/synthesis` 复刻音色 TTS 代理提交
- `/config/runtime` 暴露 `voiceClone` 能力公告
- `/voice/profiles` 由后端代理火山声音复刻 V3 训练，不让 iOS 直连火山复刻 API
- `/voice/profiles/{user_id}/{voice_profile_id}/refresh` 查询声音复刻训练状态
- `/voice/synthesis` 由后端代理复刻音色 TTS 合成，不让 iOS 直连火山合成 API 或持有合成密钥
- `.env` 中配置火山实时语音、火山声音复刻、DeepSeek、高德、后端访问 token

## 2. 部署前确认

本地后端仓库路径：

```bash
/Users/yxj/Documents/Codex/Video/DreamJourneyBackend
```

服务器后端目录：

```bash
/opt/services/dreamjourney/DreamJourneyBackend
```

服务器目标分支：

```bash
main
```

如果服务器通过 Git 拉取代码，先确认本地后端提交已推送到远程：

```bash
git -C /Users/yxj/Documents/Codex/Video/DreamJourneyBackend log -1 --oneline
git -C /Users/yxj/Documents/Codex/Video/DreamJourneyBackend push origin main
```

服务器部署后，`git log -1 --oneline` 应显示 `0752b07` 或更新提交。

## 3. 登录服务器并备份

```bash
ssh miao-server
```

或使用实际服务器账号：

```bash
ssh ubuntu@<server-host>
```

检查当前状态：

```bash
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && git status --short --branch && git log --oneline -3'
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose ps
```

备份服务器 `.env`：

```bash
sudo cp /opt/services/dreamjourney/DreamJourneyBackend/.env \
  /opt/services/dreamjourney/DreamJourneyBackend/.env.backup.$(date +%Y%m%d-%H%M%S)
sudo chmod 600 /opt/services/dreamjourney/DreamJourneyBackend/.env.backup.*
```

## 4. 拉取代码

```bash
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && git fetch origin && git checkout main && git pull --ff-only origin main && git log -1 --oneline'
```

如果 `git pull --ff-only` 失败，不要强行 reset，先看差异：

```bash
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && git status --short && git diff --stat'
```

## 5. 更新服务器 .env

编辑服务器 `.env`：

```bash
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && nano .env'
```

至少确认以下变量存在：

```env
APP_ENV=production
PUBLIC_BASE_URL=https://dreamjourney-api.liftora.cn
STORE_BACKEND=postgres

DATABASE_URL=<postgres connection url>
REDIS_URL=<redis connection url>

BACKEND_API_TOKEN=<server api token>

DEEPSEEK_API_KEY=<deepseek api key>
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1/chat/completions

VOLCENGINE_API_KEY=<volcengine tts api key>
VOLCENGINE_VOICE_TYPE=zh_female_cancan_mars_bigtts

VOLCENGINE_APP_ID=<volcengine realtime app id>
VOLCENGINE_APP_KEY=<volcengine realtime app key>
VOLCENGINE_APP_TOKEN=<volcengine realtime access token>
VOLCENGINE_REALTIME_RESOURCE_ID=volc.speech.dialog
VOLCENGINE_REALTIME_ADDRESS=wss://openspeech.bytedance.com
VOLCENGINE_REALTIME_URI=/api/v3/realtime/dialogue

VOLCENGINE_VOICE_CLONE_API_KEY=<volcengine voice clone v3 api key>
VOLCENGINE_VOICE_CLONE_TRAIN_URL=https://openspeech.bytedance.com/api/v3/tts/voice_clone
VOLCENGINE_VOICE_CLONE_QUERY_URL=https://openspeech.bytedance.com/api/v3/tts/get_voice
VOLCENGINE_VOICE_CLONE_RESOURCE_ID=seed-icl-2.0
VOLCENGINE_VOICE_CLONE_TTS_URL=https://openspeech.bytedance.com/api/v3/tts/unidirectional
VOLCENGINE_VOICE_CLONE_TTS_RESOURCE_ID=seed-icl-2.0

AMAP_WEB_SERVICE_KEY=<amap web service key>
```

注意：`VOLCENGINE_VOICE_CLONE_API_KEY` 必须是火山声音复刻 V3 HTTP 接口用于 `X-Api-Key` 的 API Key。不要填普通 Secret Key、实时语音 App Token、SDK App Key 或 Access Token；填错时线上 `/voice/profiles` 会返回并持久化 `sampleStatus=failed`，`providerMessage` 类似 `Invalid X-Api-Key`。`VOLCENGINE_VOICE_CLONE_RESOURCE_ID` 用于训练/查询接口的 `X-Api-Resource-Id`，必须和该音色所属资源一致；填错或缺失时 provider 可能返回 `resource ID is mismatched with speaker related resource`。

权限要求：

```bash
sudo chown miao:miao /opt/services/dreamjourney/DreamJourneyBackend/.env
sudo chmod 600 /opt/services/dreamjourney/DreamJourneyBackend/.env
```

## 6. 重建并启动

代码和 `.env` 都更新后：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose up -d --build
sudo docker compose ps
```

如果只改 `.env`，也建议强制重建或重建 api 容器，确保环境变量重新加载：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose up -d --force-recreate api
```

## 7. 容器内配置检查

以下命令只输出布尔值，不输出真实密钥：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose exec api python - <<'PY'
from app.core.config import settings
print({
    "store_backend": settings.store_backend,
    "public_base_url": settings.public_base_url,
    "backend_api_token": bool(settings.backend_api_token),
    "deepseek_api_key": bool(settings.deepseek_api_key),
    "volcengine_api_key": bool(settings.volcengine_api_key),
    "volcengine_app_id": bool(settings.volcengine_app_id),
    "volcengine_app_key": bool(settings.volcengine_app_key),
    "volcengine_app_token": bool(settings.volcengine_app_token),
    "volcengine_voice_clone_api_key": bool(settings.volcengine_voice_clone_api_key),
    "amap_web_service_key": bool(settings.amap_web_service_key),
})
PY
```

预期关键项为 `True`：

- `backend_api_token`
- `volcengine_app_id`
- `volcengine_app_token`
- `volcengine_voice_clone_api_key`

## 8. 公网 smoke 验证

在本机或服务器执行：

```bash
export DJ_API="https://dreamjourney-api.liftora.cn"
export DJ_TOKEN="<BACKEND_API_TOKEN>"
```

健康检查：

```bash
curl -i "$DJ_API/health"
```

预期：

- HTTP `200`
- JSON 中 `status=ok`
- JSON 中 `store=postgres`

运行配置检查：

```bash
curl -s "$DJ_API/config/runtime" \
  -H "Authorization: Bearer $DJ_TOKEN" \
  | python3 -m json.tool
```

重点确认：

```json
{
  "capabilities": {
    "realtimeToken": true,
    "voiceClone": true
  },
  "voiceClone": {
    "enabled": true,
    "provider": "volcengineVoiceCloneV3",
    "realProviderReady": true,
    "trainEndpoint": "/voice/profiles",
    "queryEndpoint": "/voice/profiles/{user_id}/{voice_profile_id}/refresh",
    "synthesisEndpoint": "/voice/synthesis",
    "synthesisProviderReady": true,
    "defaultReleaseVisible": false
  }
}
```

实时语音 token 检查：

```bash
curl -s "$DJ_API/voice/realtime-token" \
  -H "Authorization: Bearer $DJ_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"userId":"deploy_smoke_user"}' \
  | python3 -m json.tool
```

预期：

- HTTP `200`
- 返回 `appId`
- 返回 `token` 或旧版兼容字段
- 不返回服务器 `.env` 原始文件内容

## 9. 声音复刻接口验证

安全检查：先只验证 runtime 能力，不要用假的 `audioBase64` 调线上 `/voice/profiles`。

原因：

- provider 已配置时，`/voice/profiles` 会真实请求火山声音复刻 V3
- 假音频可能造成 provider 失败、污染测试数据，甚至消耗额度

需要真实验证声音复刻训练时，使用一段授权后的真实短音频样本，并确认：

- 用户已授权
- 样本来源合规
- 样本质量可用于验收
- 本次训练可消耗火山额度
- 如果返回 `providerMessage=Invalid X-Api-Key`，优先检查服务器 `.env` 中 `VOLCENGINE_VOICE_CLONE_API_KEY` 是否为声音复刻 V3 HTTP API Key。

验证请求模板：

```bash
curl -s "$DJ_API/voice/profiles" \
  -H "Authorization: Bearer $DJ_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "userId": "voice_clone_acceptance_user",
    "voiceProfileId": "voice_profile_acceptance_001",
    "sampleStatus": "pending",
    "sampleCount": 1,
    "authorizationConfirmed": true,
    "authorizationVersion": "voice-clone-consent-v1",
    "authorizationText": "用户确认提交声音样本，仅用于声音复刻验收。",
    "personaScope": "personal",
    "digitalHumanId": "voice_clone_acceptance_user",
    "audioBase64": "<base64 encoded real audio sample>",
    "audioFormat": "wav",
    "language": 0,
    "privacyMetadata": {"scope": "generationAllowed"}
  }' \
  | python3 -m json.tool
```

响应中不得出现：

- `audioBase64`
- `rawSampleURL`
- `sampleLocalPath`

查询训练状态：

```bash
curl -s "$DJ_API/voice/profiles/voice_clone_acceptance_user/voice_profile_acceptance_001/refresh" \
  -H "Authorization: Bearer $DJ_TOKEN" \
  -X POST \
  | python3 -m json.tool
```

复刻音色 TTS 合成验证需要使用已训练成功的 `voiceProfileId`。该请求会真实调用火山合成接口并可能消耗额度，因此不要使用假 `voiceProfileId` 做线上验收。

```bash
tmp_voice_synthesis_response="$(mktemp)"
curl -s "$DJ_API/voice/synthesis" \
  -H "Authorization: Bearer $DJ_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "userId": "voice_clone_acceptance_user",
    "voiceProfileId": "<ready voiceProfileId>",
    "text": "你好，欢迎回家。",
    "format": "mp3",
    "sampleRate": 24000,
    "speechRate": -10,
    "loudnessRate": 10
  }' > "$tmp_voice_synthesis_response"
python3 - "$tmp_voice_synthesis_response" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
print({
    "status": payload.get("status"),
    "voiceProfileId": payload.get("voiceProfileId"),
    "providerMode": payload.get("providerMode"),
    "audioFormat": payload.get("audio", {}).get("format"),
    "byteCount": payload.get("audio", {}).get("byteCount"),
    "hasAudioData": bool(payload.get("audio", {}).get("data")),
})
PY
rm -f "$tmp_voice_synthesis_response"
```

## 10. 后端本地测试命令

服务器容器内可跑：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose exec api python -m unittest tests.test_core_services.VoiceCloneProfileAPITests
```

如果容器镜像内没有测试目录，则以本地开发机为准：

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
STORE_BACKEND=memory PYTHONPATH=. .venv/bin/python -m unittest discover tests
```

## 11. iOS 配置要求

iOS 真机或测试包需要配置：

```text
DreamJourneyBackendBaseURL=https://dreamjourney-api.liftora.cn
DreamJourneyBackendAPIToken=<BACKEND_API_TOKEN>
```

iOS 不再需要配置火山声音复刻训练/查询/合成 API Key。声音复刻训练、查询和复刻音色 TTS 合成统一走后端。

## 12. 回滚方案

如果部署后出现严重问题：

1. 回滚代码到上一提交

```bash
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && git log --oneline -5'
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && git checkout <previous-good-commit>'
```

2. 恢复 `.env` 备份

```bash
sudo cp /opt/services/dreamjourney/DreamJourneyBackend/.env.backup.<timestamp> \
  /opt/services/dreamjourney/DreamJourneyBackend/.env
sudo chown miao:miao /opt/services/dreamjourney/DreamJourneyBackend/.env
sudo chmod 600 /opt/services/dreamjourney/DreamJourneyBackend/.env
```

3. 重建容器

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose up -d --build
```

4. 重新跑 `/health` 和 `/config/runtime` smoke。

## 13. 完成判定

满足以下条件才算部署完成：

- 服务器 `git log -1 --oneline` 显示包含 `/voice/synthesis` 的更新提交
- `docker compose ps` 中 `api`、`postgres`、`redis` 正常运行
- `/health` 返回 `200`，且 `store=postgres`
- `/config/runtime` 返回 `voiceClone.provider=volcengineVoiceCloneV3`
- `/config/runtime` 返回 `voiceClone.realProviderReady=true`
- `/config/runtime` 返回 `voiceClone.synthesisEndpoint=/voice/synthesis` 和 `voiceClone.synthesisProviderReady=true`
- `/voice/realtime-token` 返回可用实时语音配置
- iOS 端声音复刻训练/查询/合成不再直连火山 API
