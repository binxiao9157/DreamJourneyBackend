# Owner Truth ClamAV 生产部署证据

日期：2026-08-09  
状态：`CLAMAV_DEPLOYED_VERIFIED / COS_PENDING`

## 1. 本轮范围

本轮只关闭 Stage 2 内容安全扫描器的真实部署 Gate：

1. 使用官方 ClamAV 镜像，并固定版本与上游 manifest digest。
2. 仅在 Docker 内部网络开放 `3310`，不映射公网端口。
3. 让 API 通过 `clamd INSTREAM` 扫描真实 clean/EICAR 探针。
4. 保持媒体采集、处理 Worker、删除 Worker和 COS 存储关闭。

本轮没有上传、读取或删除用户媒体，也没有启用 OCR、ASR 或视觉分析。

## 2. 镜像交付

生产服务器不能直接访问 Docker Hub。为避免使用来源不明的镜像代理，本轮在受信开发机执行多架构拉取并固定 `linux/amd64`：

- image：`clamav/clamav:1.5.3-debian13-slim`
- upstream manifest digest：`sha256:741e6c447241220e0792a901befcaec1d55a755c5097fc9cd88d7fd8be251a5c`
- offline archive SHA-256：`e236a9d0aa000e4b1bd8a0eb708e4ab2a8e34bc91fbc223923f2286d9117fb8b`

服务器在校验 archive SHA-256 后导入镜像，并删除临时 archive。长期签名库保存在 Compose 私有 volume `clamav_data`。

## 3. 运行配置

服务器私密 `.env` 只增加非密钥配置：

```dotenv
OWNER_TRUTH_MEDIA_CAPTURE_ENABLED=false
OWNER_TRUTH_MEDIA_STORAGE_PROVIDER=disabled
OWNER_TRUTH_MEDIA_CONTENT_SAFETY_PROVIDER=clamav
OWNER_TRUTH_MEDIA_CLAMAV_HOST=clamav
OWNER_TRUTH_MEDIA_CLAMAV_PORT=3310
OWNER_TRUTH_MEDIA_CLAMAV_TIMEOUT_SECONDS=30
OWNER_TRUTH_MEDIA_PROCESSING_WORKER_ENABLED=false
OWNER_TRUTH_MEDIA_DELETION_WORKER_ENABLED=false
```

因此扫描器可以被 API 验证，但私有媒体入口仍不会打开。

## 4. 验证结果

以下检查通过：

- Compose `clamav` 服务状态：`healthy`。
- clean 探针：`clean`。
- EICAR 探针：`blocked/contentSafetyScanBlocked`。
- API `/ready`：`ready`，数据库、迁移、认证与 incident 组件均正常。
- API、Postgres、Redis 在 sidecar 启动后继续健康。
- ClamAV 稳态内存约 `1.0 GiB`；服务器 available memory 约 `1.6 GiB`，swap 仍有余量。

验证入口：

```bash
sudo docker compose exec -T \
  -e RUN_BACKEND_OWNER_TRUTH_MEDIA_CLAMAV_SIDECAR_SMOKE=1 \
  api scripts/run-backend-owner-truth-media-clamav-sidecar-smoke.sh
```

## 5. 未关闭 Gate

真实 COS 仍缺少专用私有 bucket、region、HTTPS endpoint、SSE 策略和最小权限凭据，因此以下 E2E 尚未执行：

`PUT -> HEAD -> readback -> DELETE -> HEAD 404`

在该 E2E 通过前，不得把 `OWNER_TRUTH_MEDIA_STORAGE_PROVIDER` 或 `OWNER_TRUTH_MEDIA_CAPTURE_ENABLED` 打开，也不得给公开用户下发媒体摄入能力。
