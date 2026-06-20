# DreamJourney 后端服务器更新操作清单

本文档用于服务器侧执行后端更新。当前目标是把后端更新到已推送的 `main` 最新提交，并启用 iOS 通过后端拉取实时语音运行配置的方案。

更新时间：2026-06-15

> 不要把真实 `.env`、`BACKEND_API_TOKEN`、火山/DeepSeek/高德 key 粘贴到群聊、工单或仓库文档里。本文所有命令只展示 key 名，不展示真实值。

声音复刻训练/查询/升级与复刻音色 TTS 合成的 key 建议拆开配置，详见：
`docs/backend/2026-06-20-volcengine-voice-clone-key-separation.md`。

## 1. 本次更新目标

| 项 | 目标 |
| --- | --- |
| 后端仓库 | `git@github.com:binxiao9157/DreamJourneyBackend.git` |
| 部署目录 | `/opt/services/dreamjourney/DreamJourneyBackend` |
| 目标分支 | `main` |
| 目标提交 | `f39cc11 feat: expose realtime voice config` 或更新的 `main` HEAD |
| 当前公网入口 | `https://www.mmdd10.tech/dreamjourney-api` |
| 正式规划入口 | `https://dreamjourney-api.liftora.cn`，DNS/HTTPS 放行后再切换 |

本次更新完成后：

- `/voice/realtime-token` 返回 iOS `SpeechEngineToB` 可直接使用的实时语音运行配置。
- 如果服务器启用了 `BACKEND_API_TOKEN`，iOS 必须配置同值 `DreamJourneyBackendAPIToken`。
- 新电脑/新真机不再需要把火山实时语音三件套重复配置到本地，只要能访问后端并带对 token。

## 2. 登录服务器

```bash
ssh ubuntu@124.221.2.31
```

如果本机配置了 alias：

```bash
ssh miao-server
```

后端仓库由 `miao` 用户拉取，后续 Git 操作用 `sudo -iu miao` 执行。

## 3. 更新前检查

查看当前后端代码版本：

```bash
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && git status --short --branch && git log --oneline -3'
```

查看容器状态：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose ps
```

备份当前 `.env`：

```bash
sudo cp /opt/services/dreamjourney/DreamJourneyBackend/.env \
  /opt/services/dreamjourney/DreamJourneyBackend/.env.backup.$(date +%Y%m%d-%H%M%S)
```

确认备份文件只在服务器本地，且不要提交：

```bash
sudo ls -l /opt/services/dreamjourney/DreamJourneyBackend/.env.backup.*
```

## 4. 拉取最新后端代码

```bash
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && git fetch origin && git pull --ff-only origin main && git log -1 --oneline'
```

预期至少包含本次提交：

```text
f39cc11 feat: expose realtime voice config
```

如果 `git pull --ff-only` 提示本地有修改，先不要强行覆盖，执行：

```bash
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && git status --short'
```

根据输出确认是否只是服务器本地误改文档或配置。不要对 `.env` 使用 Git 管理。

## 5. 检查服务器 `.env`

编辑服务器 `.env`：

```bash
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && nano .env'
```

至少确认以下 key 存在。真实值只保存在服务器，不写入仓库。

```dotenv
APP_ENV=production
PUBLIC_BASE_URL=https://www.mmdd10.tech/dreamjourney-api
STORE_BACKEND=postgres

DATABASE_URL=postgresql://dreamjourney:dreamjourney@postgres:5432/dreamjourney
REDIS_URL=redis://redis:6379/0

BACKEND_API_TOKEN=<服务器自定义强随机 token>

DEEPSEEK_API_KEY=<DeepSeek API Key>
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1/chat/completions

VOLCENGINE_API_KEY=<火山新版 API Key>
VOLCENGINE_VOICE_TYPE=zh_female_cancan_mars_bigtts

VOLCENGINE_APP_ID=<火山实时对话 App ID>
VOLCENGINE_APP_KEY=PlgvMymc7f3tQnJ6
VOLCENGINE_APP_TOKEN=<火山实时对话 Access Token>
VOLCENGINE_REALTIME_RESOURCE_ID=volc.speech.dialog
VOLCENGINE_REALTIME_ADDRESS=wss://openspeech.bytedance.com
VOLCENGINE_REALTIME_URI=/api/v3/realtime/dialogue
VOLCENGINE_VOICE_CLONE_API_KEY=<火山声音复刻 x-api-key>
VOLCENGINE_VOICE_CLONE_TRAIN_URL=https://openspeech.bytedance.com/api/v3/tts/voice_clone
VOLCENGINE_VOICE_CLONE_QUERY_URL=https://openspeech.bytedance.com/api/v3/tts/get_voice
VOLCENGINE_VOICE_CLONE_UPGRADE_URL=https://openspeech.bytedance.com/api/v3/tts/upgrade_voice
VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE=customSpeakerId
# 如果使用火山预付费/免费音色模式，改为 consoleSpeakerId，并填写控制台生成的真实 S_ 音色 ID。
# VOLCENGINE_VOICE_CLONE_SPEAKER_ID=S_xxxxxxxx
VOLCENGINE_VOICE_CLONE_TTS_API_KEY=<火山声音复刻 TTS x-api-key>
VOLCENGINE_VOICE_CLONE_TTS_URL=https://openspeech.bytedance.com/api/v1/tts
VOLCENGINE_VOICE_CLONE_TTS_CLUSTER=volcano_icl

AMAP_WEB_SERVICE_KEY=<高德 WebService Key>
```

声音复刻训练/查询只使用 `VOLCENGINE_VOICE_CLONE_API_KEY` 生成 `X-Api-Key`。`VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE=customSpeakerId` 时，请求体会传 `speaker_id=custom_speaker_id`，并把本地 `voiceProfileId` 写入 `custom_speaker_id`。`VOLCENGINE_VOICE_CLONE_SPEAKER_ID_MODE=consoleSpeakerId` 时，请求体会把服务器配置的 `VOLCENGINE_VOICE_CLONE_SPEAKER_ID` 作为 `speaker_id`，用于火山预付费/免费音色模式。复刻音色 TTS 使用独立的 `VOLCENGINE_VOICE_CLONE_TTS_API_KEY` 调官方 HTTP TTS `/api/v1/tts`，将训练得到的 `voiceProfileId` 作为 `audio.voice_type`。当前版本不要配置或依赖 `VOLCENGINE_VOICE_CLONE_RESOURCE_ID`、`VOLCENGINE_VOICE_CLONE_TTS_RESOURCE_ID`，后端不会向声音复刻训练/查询/TTS 请求发送 `X-Api-Resource-Id`。

如果还没有 `BACKEND_API_TOKEN`，可在服务器生成一个：

```bash
openssl rand -hex 32
```

把生成值写入服务器 `.env` 的 `BACKEND_API_TOKEN`。同一个值需要配置到 iOS 的 `DreamJourneyBackendAPIToken`，否则除 `/health` 外的接口会返回 `401`。

确保 `.env` 权限正确：

```bash
sudo chown miao:miao /opt/services/dreamjourney/DreamJourneyBackend/.env
sudo chmod 600 /opt/services/dreamjourney/DreamJourneyBackend/.env
```

## 6. 重建并启动容器

代码或 `.env` 更新后，执行重建：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose up -d --build
```

如果只改了 `.env`，也不要只用 `docker compose restart api`。`env_file` 需要重新创建容器才会读到新值：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose up -d --force-recreate api
```

查看状态：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose ps
```

预期 `api`、`postgres`、`redis` 都是 running 或 healthy。

## 7. 检查环境变量是否被容器读取

以下命令只输出是否已配置，不输出真实密钥：

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
    "volcengine_voice_type": settings.volcengine_voice_type,
    "volcengine_app_id": bool(settings.volcengine_app_id),
    "volcengine_app_key": bool(settings.volcengine_app_key),
    "volcengine_app_token": bool(settings.volcengine_app_token),
    "amap_web_service_key": bool(settings.amap_web_service_key),
})
PY
```

关键预期：

- `store_backend` 为 `postgres`。
- `public_base_url` 为 `https://www.mmdd10.tech/dreamjourney-api`。
- `backend_api_token` 为 `True`。
- `volcengine_app_id`、`volcengine_app_token` 为 `True`。
- `volcengine_app_key` 为 `True`，或由代码自动使用固定值 `PlgvMymc7f3tQnJ6`。

## 8. 公网接口验证

先把服务器 token 放入当前 SSH 会话的环境变量，避免命令行里重复粘贴：

```bash
export BACKEND_API_TOKEN='<服务器 .env 中 BACKEND_API_TOKEN 的真实值>'
export DJ_API='https://www.mmdd10.tech/dreamjourney-api'
```

健康检查不需要 token：

```bash
curl -i "$DJ_API/health"
```

预期包含：

```json
{"status":"ok","service":"DreamJourney Backend","environment":"production","store":"postgres"}
```

运行配置需要 token：

```bash
curl -s "$DJ_API/config/runtime" \
  -H "Authorization: Bearer ${BACKEND_API_TOKEN}" \
  | python3 -m json.tool
```

预期 `capabilities` 中至少包含：

```json
{
  "deepseekProxy": true,
  "ttsProxy": true,
  "realtimeToken": true,
  "amapDistrictProxy": true,
  "kbSync": true,
  "familyCircle": true
}
```

验证实时语音运行配置。注意这个接口会返回 SDK 启动所需凭证，验证时只打印字段是否存在，不直接打印完整响应：

```bash
python3 - <<'PY'
import json
import os
import urllib.request

base = os.environ["DJ_API"].rstrip("/")
token = os.environ["BACKEND_API_TOKEN"]
payload = json.dumps({"userId": "user_9157"}).encode("utf-8")
request = urllib.request.Request(
    f"{base}/voice/realtime-token",
    data=payload,
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    },
    method="POST",
)
with urllib.request.urlopen(request, timeout=20) as response:
    data = json.loads(response.read().decode("utf-8"))

print({
    "authMode": data.get("authMode"),
    "address": data.get("address"),
    "uri": data.get("uri"),
    "resourceID": data.get("resourceID"),
    "uid": data.get("uid"),
    "hasAppID": bool(data.get("appID")),
    "hasAppKey": bool(data.get("appKey")),
    "hasAppToken": bool(data.get("appToken")),
    "hasAPIKey": bool(data.get("apiKey")),
})
PY
```

旧式实时三件套配置正确时，预期：

```text
authMode=legacy
hasAppID=True
hasAppKey=True
hasAppToken=True
```

## 9. 业务接口 smoke test

登录接口：

```bash
curl -s -X POST "$DJ_API/auth/login" \
  -H "Authorization: Bearer ${BACKEND_API_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000000","nickname":"陈建国"}' \
  | python3 -m json.tool
```

KBLite 同步：

```bash
curl -s -X POST "$DJ_API/kb/sync" \
  -H "Authorization: Bearer ${BACKEND_API_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{
    "userId": "user_9157",
    "graph": {
      "people": [
        {"id":"p1","name":"陈建国","privacyMetadata":{"scope":"generationAllowed"}},
        {"id":"p2","name":"本机私密人物","privacyMetadata":{"scope":"localOnly"}}
      ],
      "places": [],
      "events": [],
      "facts": []
    }
  }' \
  | python3 -m json.tool
```

读取 KBLite 快照：

```bash
curl -s "$DJ_API/kb/snapshot/user_9157" \
  -H "Authorization: Bearer ${BACKEND_API_TOKEN}" \
  | python3 -m json.tool
```

预期看不到 `本机私密人物`。

高德 dry run：

```bash
curl -s -G "$DJ_API/maps/district" \
  -H "Authorization: Bearer ${BACKEND_API_TOKEN}" \
  --data-urlencode 'keyword=绍兴市' \
  --data-urlencode 'dryRun=true' \
  | python3 -m json.tool
```

TTS dry run：

```bash
curl -s -X POST "$DJ_API/tts?dryRun=true" \
  -H "Authorization: Bearer ${BACKEND_API_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"userId":"user_9157","text":"你好，我想听一段家族回忆。"}' \
  | python3 -m json.tool
```

## 10. 数据库确认

查看 Postgres 表：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose exec postgres psql -U dreamjourney -d dreamjourney -c '\dt'
```

至少应包含：

```text
users
kb_snapshots
memories
archive_items
family_members
care_snapshots
mailbox_letters
```

查看最近写入的测试用户：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose exec postgres psql -U dreamjourney -d dreamjourney \
  -c "select id, phone, nickname, updated_at from users order by updated_at desc limit 5;"
```

## 11. Nginx 检查

通常更新后端代码不需要改 Nginx。仍建议检查配置：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

查看 Nginx 日志：

```bash
sudo tail -n 100 /var/log/nginx/error.log
```

当前真机仍使用：

```text
https://www.mmdd10.tech/dreamjourney-api
```

`https://dreamjourney-api.liftora.cn` 只有在 DNS/备案/HTTPS 全部完成后再切换。

## 12. iOS 真机侧配套检查

服务器更新完成后，新电脑/新真机至少配置：

```text
DreamJourneyBackendBaseURL=https://www.mmdd10.tech/dreamjourney-api
DreamJourneyBackendAPIToken=<服务器 BACKEND_API_TOKEN 同值>
AMapAPIKey=<iOS 高德 SDK Key>
```

以下项可以不在新电脑本地配置，优先由后端承担：

```text
DeepSeekAPIKey
VolcEngineAPIKey
VolcEngineAppID
VolcEngineAppKey
VolcEngineAppToken
VolcEngineRealtimeResourceID
VolcEngineRealtimeAddress
VolcEngineRealtimeURI
AMapWebServiceKey
```

如果 App 仍提示“语音服务暂不可用”，优先检查：

1. iOS 的 `DreamJourneyBackendBaseURL` 是否是 `https://www.mmdd10.tech/dreamjourney-api`，不要写成 `localhost`。
2. iOS 的 `DreamJourneyBackendAPIToken` 是否与服务器 `BACKEND_API_TOKEN` 完全一致。
3. 服务器 `/voice/realtime-token` 是否返回 `authMode=legacy` 且 `hasAppToken=True`。
4. 后端容器是否在 `.env` 修改后重新创建，而不是只 restart。

## 13. 常见问题排查

### 13.1 `/config/runtime` 或其他接口返回 401

原因：服务器启用了 `BACKEND_API_TOKEN`，请求没带 token 或 token 不一致。

处理：

```bash
curl -i "$DJ_API/config/runtime" \
  -H "Authorization: Bearer ${BACKEND_API_TOKEN}"
```

iOS 侧同步配置：

```text
DreamJourneyBackendAPIToken=<服务器 BACKEND_API_TOKEN 同值>
```

### 13.2 `/voice/realtime-token` 返回 503

原因：服务器没有读到火山实时语音凭证。

检查：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose exec api python - <<'PY'
from app.core.config import settings
print({
    "appID": bool(settings.volcengine_app_id),
    "appKey": bool(settings.volcengine_app_key),
    "appToken": bool(settings.volcengine_app_token),
    "apiKey": bool(settings.volcengine_api_key),
})
PY
```

如果 `.env` 已经填写但这里仍是 `False`，执行：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose up -d --force-recreate api
```

### 13.3 TTS dry run 正常，但真实 TTS 失败

优先检查：

- `VOLCENGINE_API_KEY` 是否有效。
- `VOLCENGINE_VOICE_TYPE` 是否为可用 speaker id。
- 火山控制台额度、模型权限、音色权限是否开通。

### 13.4 地图行政区代理失败

优先检查：

- `AMAP_WEB_SERVICE_KEY` 是否在服务器 `.env` 中。
- 高德 WebService Key 是否启用了 Web 服务 API，不是 iOS SDK Key。
- 高德控制台是否限制了 IP 白名单。

## 14. 回滚方案

如果本次更新后 API 容器启动失败，可以临时回到上一个后端提交：

```bash
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && git checkout d60ef9d'
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose up -d --build
```

确认服务恢复：

```bash
curl -i https://www.mmdd10.tech/dreamjourney-api/health
```

排障完成后回到 `main`：

```bash
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && git switch main && git pull --ff-only origin main'
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose up -d --build
```

## 15. 更新完成判定

满足以下条件才算服务器更新完成：

- `git log -1 --oneline` 显示 `f39cc11` 或更新的 `main` HEAD。
- `sudo docker compose ps` 中 `api`、`postgres`、`redis` 正常运行。
- `/health` 返回 `status=ok`、`store=postgres`。
- `/config/runtime` 带 token 后返回能力开关，`realtimeToken=true`。
- `/config/runtime` 带 token 后返回 `voiceClone.provider=volcengineVoiceCloneV3` 且 `voiceClone.realProviderReady=true`。
- `/config/runtime` 带 token 后返回 `voiceClone.synthesisEndpoint=/voice/synthesis` 且 `voiceClone.synthesisProviderReady=true`。
- `/config/runtime` 带 token 后返回 `voiceClone.lipSyncTimeline.field=visemeTimeline`；当前 `supported=false` 代表 TTS provider 尚未承诺真实 phoneme/viseme 时间戳，iOS 应使用播放器音量 metering 降级。
- `/voice/realtime-token` 带 token 后返回 `authMode=legacy`，且 `hasAppToken=True`。
- `/voice/profiles` 在带授权与声音样本时由后端代理火山声音复刻 V3；返回结果不应包含 `audioBase64`、`rawSampleURL` 或本地样本路径。
- `/voice/synthesis` 使用已训练成功的 `voiceProfileId` 由后端代理 `/api/v1/tts` 复刻音色 TTS；响应可包含可选 `visemeTimeline`，不得包含火山 `X-Api-Key`、`x-api-key` 或上游请求头。
- iOS 真机配置 `DreamJourneyBackendBaseURL` 和 `DreamJourneyBackendAPIToken` 后，不再因新电脑缺本地火山实时语音三件套而提示“语音服务暂不可用”。
