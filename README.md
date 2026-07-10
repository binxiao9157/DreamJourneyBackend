# DreamJourneyBackend

阶段1真实测试用最小后端，目标是把三方密钥、知识库同步、亲友协作和地图/TTS 代理从 iOS 包里拆出来。

## 当前接口

- `GET /health`
- `POST /auth/login`
- `POST /auth/refresh`
- `POST /auth/logout`
- `GET /config/runtime`
- `POST /voice/realtime-token`
- `POST /voice/profiles`
- `GET /voice/profiles/{user_id}`
- `POST /voice/profiles/{user_id}/{voice_profile_id}/disable`
- `POST /voice/profiles/{user_id}/{voice_profile_id}/refresh`
- `DELETE /voice/profiles/{user_id}/{voice_profile_id}`
- `POST /voice/synthesis`
- `POST /tts`
- `GET /maps/district`
- `POST /kb/sync`
- `GET /kb/snapshot/{user_id}`
- `POST /kb/extract`
- `POST /memories`
- `GET /memories/{user_id}`
- `POST /archive/photos`
- `POST /archive/items`
- `POST /archive/media/upload-intent`
- `GET /archive/items/{user_id}`
- `DELETE /archive/items/{user_id}/{item_id}`
- `POST /archive/image-analysis`
- `POST /mailbox/letters`
- `GET /mailbox/letters/{user_id}`
- `POST /family/invite`
- `GET /family/members/{user_id}`
- `POST /family/members/{user_id}/{member_id}/accept`
- `POST /family/invitations/{invitation_code}/accept`
- `POST /family/members/{user_id}/{member_id}/revoke`
- `POST /care/snapshots`
- `GET /care/snapshots/latest/{user_id}`
- `GET /care/snapshots/{user_id}`

## 隐私规则

- `localOnly`：后端同步时过滤，不上传。
- `generationAllowed`：允许后端和 AI 处理。
- `familyCircle`：允许授权亲友同步。

`/kb/sync` 会过滤 KBLite 图谱里的 `localOnly` 实体，并清理事件、事实中的无效引用。

## 登录会话与 ownership shadow

- `/auth/login` 返回短期 opaque access token 和可轮换 refresh token；数据库只保存 SHA-256 hash，不保存原始 token。
- iOS 业务请求使用用户 access token；`BACKEND_API_TOKEN` 通过独立 header 保留部署兼容和系统级 smoke。
- `/auth/refresh` 每次成功同时轮换 access/refresh token，旧 refresh token 重放返回 `401`。
- `/auth/logout` 撤销当前会话，账号清理时同时删除该用户的 auth sessions。
- `AUTH_OWNERSHIP_MODE=shadow` 只记录 authenticated user 与 payload/path actor 的 `match/mismatch/unclaimed`，不会拦截现有请求。
- `AUTH_OWNERSHIP_MODE=enforce` 会对 mismatch 返回 `403`，只能在 shadow 证据审阅和跨账号规则补齐后启用。

## 本地启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8080
```

## 云服务器 Docker 启动

```bash
cp .env.example .env
# 编辑 .env，填入 DeepSeek、VolcEngine、AMap 等服务端密钥
docker compose up -d --build
curl http://127.0.0.1:3100/health
```

生产建议通过现有 `Nginx` 反向代理到 `127.0.0.1:3100`，启用 HTTPS，再把 iOS 的后端地址配置为公网域名。

## 真机配置建议

- `DreamJourneyBackendBaseURL`：指向 `https://dreamjourney-api.liftora.cn`
- `DreamJourneyBackendAPIToken`：如果服务器启用了 `BACKEND_API_TOKEN`，iOS 必须配置同值 token，才能拉取实时语音运行配置。
- `OpenAvatarChatBaseURL`：仅保留为旧 OpenAvatarChat 开源工程兼容配置，不作为本后端入口。
- `SafetyGuardBaseURL`：后续如果把 safety guard 挂到本后端，也指向同域名
- `AMapWebServiceKey`、`DeepSeekAPIKey`、`VolcEngineAPIKey`、`VolcEngineAppID`、`VolcEngineAppToken`：逐步从 iOS LocalConfig 迁移到后端 `.env`；实时语音会通过 `POST /voice/realtime-token` 下发运行配置。

当前默认使用 Postgres 持久化。API 容器启动时会自动创建以下 JSONB 表：

- `users`
- `kb_snapshots`
- `memories`
- `archive_items`
- `voice_profiles`
- `family_members`
- `auth_sessions`

如需本机临时无数据库调试，可设置：

```bash
STORE_BACKEND=memory uvicorn app.main:app --reload --port 8080
```

内存模式只用于开发调试，进程重启会丢数据；云服务器长期测试请保持 `STORE_BACKEND=postgres`。
