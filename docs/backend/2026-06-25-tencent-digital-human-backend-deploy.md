# 腾讯数智人后端部署记录

日期：2026-06-25
更新：2026-06-27，已补齐竖屏 `asset_virtualman_key` 并完成 cloudRender 合同验收。

## 部署结论

后端已部署到服务器，当前线上代码已更新到：

```text
7e89f0d feat: add tencent digital human cloud session contract
```

部署分支：

```text
main
```

公网入口：

```text
https://dreamjourney-api.liftora.cn
```

容器状态正常：

- `api`：running
- `postgres`：healthy
- `redis`：running

## 本次部署内容

本次部署包含腾讯数智人 cloud-rendering session 合同：

- `/config/runtime.digitalHuman`
- `/digital-human/sessions`
- 腾讯数智人环境变量读取
- 缺少真实资产 ID 时的 mock/fallback 安全降级
- `silent` 生命周期禁止创建渲染 session

后端新增/更新的关键环境变量名：

```dotenv
TENCENT_DIGITAL_HUMAN_APP_KEY=
TENCENT_DIGITAL_HUMAN_ACCESS_TOKEN=
TENCENT_DIGITAL_HUMAN_ASSET_VIRTUALMAN_KEY=
TENCENT_DIGITAL_HUMAN_VIRTUALMAN_PROJECT_ID=
TENCENT_DIGITAL_HUMAN_APP_ID=
TENCENT_DIGITAL_HUMAN_SECRET_ID=
TENCENT_DIGITAL_HUMAN_SECRET_KEY=
```

注意：文档只记录 key 名，不记录真实值。

## 服务器配置状态

已确认服务器 `.env` 中当前配置状态：

| 配置项 | 状态 | 说明 |
| --- | --- | --- |
| `TENCENT_DIGITAL_HUMAN_APP_KEY` | 已配置 | 腾讯数智人会话/API appkey |
| `TENCENT_DIGITAL_HUMAN_ACCESS_TOKEN` | 已配置 | 腾讯数智人会话/API accesstoken |
| `TENCENT_DIGITAL_HUMAN_SECRET_ID` | 已配置 | 仅后续启用腾讯 ASR 时可能需要 |
| `TENCENT_DIGITAL_HUMAN_ASSET_VIRTUALMAN_KEY` | 已配置 | 真实云渲染竖屏形象资产 ID，适配 iOS Echo 竖屏场景 |
| `TENCENT_DIGITAL_HUMAN_VIRTUALMAN_PROJECT_ID` | 未配置 | 真实云渲染项目 ID |
| `TENCENT_DIGITAL_HUMAN_APP_ID` | 未配置 | 仅后续启用腾讯 ASR 时可能需要 |
| `TENCENT_DIGITAL_HUMAN_SECRET_KEY` | 未配置 | 仅后续启用腾讯 ASR 时可能需要 |

当前已经满足真实云渲染建流的后端合同条件：

```text
digitalHuman.providerMode = cloudRender
digitalHuman.assetMode = asset
```

横屏 `virtualmanKey` 已作为备用信息保存在本地私密配置中，暂未写入服务器 `.env`。当前 iOS Echo 以竖屏场景为主，服务器只启用竖屏 asset key。

## 验证结果

### 健康检查

公网健康检查已通过：

```text
GET /health -> 200
status = ok
store = postgres
```

### 运行配置

公网 `/config/runtime` 已可访问，关键返回：

```text
capabilities.digitalHumanSession = true
digitalHuman.provider = tencent
digitalHuman.providerMode = cloudRender
digitalHuman.realProviderReady = true
digitalHuman.sdkAdapterLinked = true
digitalHuman.assetMode = asset
voiceClone.voiceClone2TrialReady = true
voiceClone.synthesisProviderReady = true
```

### 数智人 session

本机容器内验证：

```text
POST /digital-human/sessions lifecycleMode=sunlight -> 200
provider = tencent
providerMode = cloudRender
credentialMode = backend-issued-tencent-cloud
hasProviderAssetId = true
hasProviderProjectId = false
fallbackMode = none
```

静默模式验证：

```text
POST /digital-human/sessions lifecycleMode=silent -> 409
silent mode rejected = true
```

### 容器日志

API 容器启动正常：

```text
Application startup complete.
Uvicorn running on http://0.0.0.0:8080
```

最近接口日志包含：

```text
GET /health -> 200
GET /config/runtime -> 200
POST /digital-human/sessions -> 200
POST /digital-human/sessions -> 409
```

## cloudRender 验收结果

已运行：

```bash
RUN_ID=20260627-tencent-dh-cloudrender-asset \
RUN_BACKEND_DIGITAL_HUMAN_SESSION_SMOKE=1 \
RUN_STANDARD_BUILD=0 \
RUN_SIMULATOR_SMOKE=0 \
RUN_ECHO_DELAYED_REPLY_NOTIFICATION_SMOKE=0 \
tmp/visual-qa/prd-stitch-ui/run-release-regression.sh
```

结果：

```text
completed = true
runtimeDigitalHuman.providerMode = cloudRender
runtimeDigitalHuman.realProviderReady = true
runtimeDigitalHuman.sdkAdapterLinked = true
runtimeDigitalHuman.assetMode = asset
session.providerMode = cloudRender
session.credentialMode = backend-issued-tencent-cloud
session.hasProviderAssetId = true
session.hasProviderProjectId = false
session.fallbackMode = none
silentModeRejected = true
```

报告路径：

```text
tmp/visual-qa/prd-stitch-ui/release-regression/20260627-tencent-dh-cloudrender-asset/report.md
tmp/visual-qa/prd-stitch-ui/release-regression/20260627-tencent-dh-cloudrender-asset/backend-digital-human-session-smoke/20260627-tencent-dh-cloudrender-asset/report.md
```

## 后续重复验收命令

后续更新腾讯数智人配置或后端代码后，在 iOS 工程目录运行：

```bash
RUN_BACKEND_DIGITAL_HUMAN_SESSION_SMOKE=1 \
RUN_STANDARD_BUILD=0 \
RUN_SIMULATOR_SMOKE=0 \
RUN_ECHO_DELAYED_REPLY_NOTIFICATION_SMOKE=0 \
tmp/visual-qa/prd-stitch-ui/run-release-regression.sh
```

预期通过后应满足：

```text
/config/runtime.digitalHuman.providerMode = cloudRender
/config/runtime.digitalHuman.realProviderReady = true
/config/runtime.digitalHuman.sdkAdapterLinked = true
/digital-human/sessions credential.mode = backend-issued-tencent-cloud
/digital-human/sessions credential.appkey 已下发
/digital-human/sessions credential.accesstoken 已下发
/digital-human/sessions providerAssetId 或 providerProjectId 已下发
```

报告路径格式：

```text
tmp/visual-qa/prd-stitch-ui/release-regression/<RUN_ID>/report.md
```

## 当前风险与限制

- 真实腾讯数智人后端 session 合同已切到 `cloudRender`，但还需要真机或模拟器实际打开 iOS 腾讯 SDK 面板，验证云渲染画面、会话打开、文本驱动和关闭行为。
- 当前服务器只配置竖屏 asset key；横屏 virtualmanKey 暂未启用。
- `SecretId` / `SecretKey` / `AppId` 不是云渲染建流必填项，仅在后续启用腾讯 ASR 时再处理。
- 不应把腾讯数智人长期密钥写入 iOS，本方案继续保持由后端签发 session 合同。
