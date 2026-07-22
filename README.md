# DreamJourneyBackend

阶段1真实测试用最小后端，目标是把三方密钥、知识库同步、亲友协作和地图/TTS 代理从 iOS 包里拆出来。

## 当前接口

- `GET /health`
- `GET /live`
- `GET /ready`
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

## 登录会话、路由认证与 ownership

- `/auth/login` 返回短期 opaque access token 和可轮换 refresh token；数据库只保存 SHA-256 hash，不保存原始 token。
- iOS 业务请求只使用用户 access token；不得把 `BACKEND_API_TOKEN` 打进客户端包。
- `BACKEND_API_TOKEN` 仅代表服务端 machine principal，只能调用声明了对应 machine scope 的系统任务和运维路由，不能调用普通用户业务路由。
- `AUTH_ROUTE_MODE=auto` 在 production 自动解析为 `enforce`、在非生产环境解析为 `shadow`；非法值会阻止启动，生产环境缺少 machine credential 也会阻止启动。
- `CLIENT_COMPATIBILITY_MODE=observe|enforce` 独立于 feature mapper，对 route registry 中的 USER mutation 按 `RELEASE_POLICY_MIN_CLIENT_BUILD` 观测或返回稳定 `426 upgrade_required`；GET/HEAD 与 `/auth/logout` 不受写围栏影响。
- `/auth/refresh` 每次成功同时轮换 access/refresh token，旧 refresh token 重放返回 `401`。
- `/auth/logout` 撤销当前会话，账号清理时同时删除该用户的 auth sessions。
- `AUTH_OWNERSHIP_MODE=shadow` 只记录 authenticated user 与 payload/path actor 的 `match/mismatch/unclaimed`，不会拦截现有请求。
- `AUTH_OWNERSHIP_MODE=enforce` 会对 mismatch 返回 `403`，只能在 shadow 证据审阅和跨账号规则补齐后启用。
- `RELEASE_POLICY_COMMAND_MODE=observe` 会为受控 command 重新计算服务端发布策略并输出诊断响应头，但暂不拦截旧客户端；这是默认迁移模式。
- `RELEASE_POLICY_COMMAND_MODE=enforce` 会在受控 command 缺少有效 captured decision、账号代际不匹配或服务端策略拒绝时返回 `403 release_policy_denied`。只能在 observe mismatch 与旧客户端覆盖完成后按 cohort 切换。
- `DELEGATED_ACCESS_CONTRACT_API_ENABLED=false` 默认关闭 Family/Care/TimeLetter 的 Grant/Relationship 管理接口；安全合同和数据库迁移可以先部署，但在 G4 产品政策通过前不得在常驻服务进程中开启。部署 smoke 仅在独立进程内临时开启该合同。
- `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=false` 默认关闭 V4 Candidate Inbox/Owner 决策、Projection、KBLite compatibility 与 Context shadow QA 接口。即使临时开启，也必须携带认证后的用户会话和 `X-DreamJourney-QA-Owner-Truth: 1`；这些接口仅用于 QA，不代表 Candidate、Projection 或 Context 已对公开产品开放。
- `OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED=false` 是独立的 M0-B Owner 知识维度确认回执开关。即使 Candidate QA 已开启，它仍默认关闭；开启后只允许 Owner 对当前 `MemoryVersion` 的 hash 追加明确的维度/Facet 确认，不写回记忆原文、不创建 Candidate/新版本，也不接入公开 Echo。
- `OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED=false` 是独立的 M0-B 推荐读取开关。它要求前两个 Owner Truth QA 开关和认证 Owner 会话同时成立，只允许用当前确认回执绑定的同维度 `confirmedMemory` 引用执行值无关的选择策略；不生成问题文本、不落库、不接入公开 Echo。
- `OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED=false` 是独立的 M0-B 显式续聊线索开关。它额外要求服务端规划 QA 开关；只允许 Owner 将当前 `active + open` 会话、当前确认的 `MemoryVersion` 和仍缺失的 facet 绑定为 append-only cue。它不保存对话/问题正文，Session/authority/MemoryVersion/facet 失效后自动不再参与计划，也不接入公开 Echo。
- `ASYNC_EFFECT_V1_ENABLED=false`、`ASYNC_EFFECT_WORKER_ENABLED=false` 和 `OWNER_TRUTH_MEMORY_PROJECTION_WORKER_ENABLED=false` 默认关闭 V4 异步副作用执行。`0013_async_effects_kernel` 只部署可重建的协调 schema；API 不得把本地 timer、notification 或 schema 存在解释为服务端完成。现有 `async-effect-worker` 与 `async-effect-scheduler` Compose profile 仍只允许值无关的 shadow observation。Owner Truth projection 有独立的一次性 typed worker，只有三个开关同时显式开启才会 claim `ownerTruth.memoryProjection.rebuild`，并且不改变 `/context/build`、KBLite legacy writer 或公开 Echo。
- ReleasePolicy rollout shadow 事件写入严格白名单的 append-only evidence sink；`EVIDENCE_ROLLOUT_RETENTION_DAYS` 只控制临时 rollout 观察保留期，legal hold 不受普通 TTL 或账号 purge 删除。

部署环境可设置 `BACKEND_BASE_URL`、`COMPAT_EXPECTED_MODE`、`COMPAT_MIN_CLIENT_BUILD`、`COMPAT_USER_ACCESS_TOKEN`、`COMPAT_USER_ID` 和 `BACKEND_API_TOKEN` 后运行 `scripts/backend-client-compatibility-deployed-smoke.py`。脚本只执行读取和必然在字段校验前失败的 mutation probe，不切换或持久化生产策略；observe 与 enforce 应分别在对应配置的实例上执行。

## Runtime capability 五轴合同

`GET /config/runtime` 的 `capabilitySnapshots` 为扩展能力提供独立五轴：

- `implemented`：代码/合同已实现；
- `enabled`：当前环境配置允许尝试；
- `providerReady`：真实 Provider effect 前置条件已满足；
- `releaseVisible`：服务端发布策略允许当前 cohort 看到；
- `externalVerified`：G3/G4 外部证据有效且未过期。

这五个字段不能互相推导。Provider 已配置不代表功能可以公开，mock/text-only Provider 不能标记为 ready，缺失或过期外部证据不能由代码自行签署。旧 runtime bool 继续保留给旧客户端，iOS 新客户端在五轴合同缺失时按 unknown/deny 处理。

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
curl http://127.0.0.1:3100/live
curl http://127.0.0.1:3100/ready
```

生产建议通过现有 `Nginx` 反向代理到 `127.0.0.1:3100`，启用 HTTPS，再把 iOS 的后端地址配置为公网域名。

## 真机配置建议

- `DreamJourneyBackendBaseURL`：指向 `https://dreamjourney-api.liftora.cn`
- iOS 不配置 `DreamJourneyBackendAPIToken`；登录后的业务接口使用用户 access token，公开 runtime/login/challenge 接口使用显式 public 合同。
- `OpenAvatarChatBaseURL`：仅保留为旧 OpenAvatarChat 开源工程兼容配置，不作为本后端入口。
- `SafetyGuardBaseURL`：后续如果把 safety guard 挂到本后端，也指向同域名
- `AMapWebServiceKey`、`DeepSeekAPIKey`、`VolcEngineAPIKey`、`VolcEngineAppID`、`VolcEngineAppToken`：逐步从 iOS LocalConfig 迁移到后端 `.env`；实时语音会通过 `POST /voice/realtime-token` 下发运行配置。

当前默认使用 Postgres 持久化。API 启动不会创建或修改业务表，所有 schema 变化必须先通过下方 versioned migrator 执行并验证。

如需本机临时无数据库调试，可设置：

```bash
STORE_BACKEND=memory uvicorn app.main:app --reload --port 8080
```

内存模式只用于开发调试，进程重启会丢数据；云服务器长期测试请保持 `STORE_BACKEND=postgres`。

## Database migrations

API startup does not create or alter database objects. Run the versioned migrator before starting a new API build:

```bash
python scripts/migrate_db.py --dry-run --build-id "$DEPLOY_BUILD_ID"
python scripts/migrate_db.py --apply --build-id "$DEPLOY_BUILD_ID"
python scripts/migrate_db.py --verify --build-id "$DEPLOY_BUILD_ID"
```

For the first rollout to an existing DreamJourney database, inspect the dry-run result and then use the explicit baseline receipt path. It verifies all known relations, columns, and triggers and does not replay baseline DDL:

```bash
python scripts/migrate_db.py --apply --adopt-existing-baseline --build-id "$DEPLOY_BUILD_ID"
```

Do not use baseline adoption for a partially matching schema. Do not run automatic down migrations in production; stop the rollout and use a forward fix or an isolated restore.

## Liveness and readiness

- `GET /live` 只证明 API 进程存活，不访问数据库，也不能作为业务流量放行依据。
- `GET /ready` 每次重新检查连接池 checkout、可回滚的数据库读写 probe、migration head/checksum 和生产必需认证配置；任一 required component 为 unknown/down 时返回 `503`。
- DeepSeek、火山语音和腾讯数智人等扩展 Provider 不进入基础 readiness，Provider 故障只降对应 capability，不阻断 Owner 文字核心。
- `GET /health` 仅为旧客户端保留并标记 deprecated；Docker、负载均衡和部署脚本必须使用 `/ready`。
- readiness 响应只包含 component/status/reason/evidenceTimestamp，不返回 DSN、SQL、migration checksum 或凭据。

真实 Postgres 与部署环境 smoke：

```bash
scripts/run-backend-readiness-postgres-smoke.sh
BACKEND_BASE_URL=https://dreamjourney-api.liftora.cn \
  scripts/run-backend-readiness-deployed-smoke.sh
```

## PostgreSQL backups

Compose volume 不是备份。`scripts/db/backup_postgres.sh` 生成加密 custom-format artifact，并在成功后写 value-free manifest；`scripts/db/verify_backup_manifest.py` 校验 schema head、checksum、size 和有效期。systemd service/timer、失败 alert receipt、audit-only retention 和服务器安装步骤见 `docs/backend/2026-07-16-postgres-backup-operations.md`。

本地不访问真实数据库的合同 smoke：

```bash
scripts/db/run-backup-postgres-smoke.sh
```

该 smoke 覆盖连续备份、加密读取、中断、磁盘不足、损坏 checksum、告警回执和“永不自动删除最后有效备份”。隔离 restore 与 RPO/RTO 不在本项宣称范围内。
