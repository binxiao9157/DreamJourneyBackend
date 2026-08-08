# Owner Truth Stage 2: ClamAV Sidecar A1.1

日期：2026-08-08
状态：`CODE_COMPLETE_DEPLOYMENT_PROFILE_DEFAULT_OFF`

## 目标

将 Stage 2 私有媒体的内容安全扫描从 API/Worker 镜像内的 `clamscan` 可选依赖，收敛为可选的内部 ClamAV sidecar。默认不启动、默认不打开摄入；扫描器不可用、超时、签名库不可用或返回未知结果时，摄入必须在对象存储写入前 fail-closed。

## 实现合同

- Compose profile：`owner-truth-media-safety`。
- 服务名：`clamav`；仅使用 Docker 内部网络的 `3310`，没有 `ports` 映射。
- 扫描协议：`clamd` 的 NUL framed `INSTREAM`，按网络字节序分块发送并以零长度块结束。
- 运行时探测：不只是 TCP/PING；对固定、无用户数据的干净探针执行实际扫描，只有收到 `OK` 才认为签名库和扫描服务可用。
- 判定：`OK -> clean`、`FOUND -> blocked`、连接失败/超时/错误/截断回复 -> `contentSafetyScannerUnavailable`。
- 既有本地 `clamscan` 分支保留为未设置 `OWNER_TRUTH_MEDIA_CLAMAV_HOST` 时的兼容模式；生产建议显式配置 sidecar host。

ClamAV 官方协议说明确认：TCP 支持 `INSTREAM`，但该 TCP 通道没有加密或认证，因此绝不能暴露给不可信网络。[ClamD 协议](https://docs.clamav.net/manual/Usage/ClamdProtocol.html) 官方 Docker 安装说明见 [ClamAV Docker 文档](https://docs.clamav.net/manual/Installing/Docker.html)。

## 服务器配置与启用顺序

1. 先部署包含本改动的后端；不要立即修改 `.env` 的媒体开关。
2. 检查服务器剩余内存和磁盘。首次下载和后续签名库重载需要明显的资源余量；容量不足时不启动 sidecar。
3. 仅启动扫描器 profile：

   ```bash
   cd /opt/services/dreamjourney/DreamJourneyBackend
   sudo docker compose --profile owner-truth-media-safety up -d clamav
   sudo docker compose ps clamav
   ```

4. 在服务器私密 `.env` 中配置：

   ```dotenv
   OWNER_TRUTH_MEDIA_CONTENT_SAFETY_PROVIDER=clamav
   OWNER_TRUTH_MEDIA_CLAMAV_HOST=clamav
   OWNER_TRUTH_MEDIA_CLAMAV_PORT=3310
   OWNER_TRUTH_MEDIA_CLAMAV_TIMEOUT_SECONDS=30
   ```

   这三项不包含密钥。`clamav` 是 Compose 内部服务名，不是公网地址。

5. 重建并重启 API/所需私有 Worker。启动前不得打开 `OWNER_TRUTH_MEDIA_CAPTURE_ENABLED`。
6. 在 API 容器执行 sidecar smoke：

   ```bash
   sudo docker compose exec -T \
     -e RUN_BACKEND_OWNER_TRUTH_MEDIA_CLAMAV_SIDECAR_SMOKE=1 \
     api scripts/run-backend-owner-truth-media-clamav-sidecar-smoke.sh
   ```

   脚本只扫描固定干净探针和标准 EICAR 测试字符串；不会调用 COS、读取或写入任何用户媒体。
7. 查询 `/config/runtime`。在 COS、摄入和 sidecar 都完整配置前，`ownerTruthMediaStorage` 仍应保持不可用。只有所有独立 Gate 通过后，才按 closed-pilot 顺序打开摄入。

## 非真机验证

- `scripts/run-backend-owner-truth-media-processing-gate.sh`：协议分帧、正常、`FOUND`、离线/超时/未知回复、runtime fail-closed、默认关闭和 Compose profile 静态合同。
- `RUN_BACKEND_OWNER_TRUTH_MEDIA_CLAMAV_SIDECAR_SMOKE=1 scripts/run-backend-owner-truth-media-clamav-sidecar-smoke.sh`：真实 sidecar 运行时 clean/EICAR 验证。

## 当前边界

- 未启动 profile、未配置 host 或未通过 smoke 时，不允许启用真实媒体摄入。
- 本 Gate 不配置腾讯 COS、不上传任何真实文件，也不启用 OCR、ASR、视觉分析或公开入口。
- EICAR 被拦截只证明安全扫描链路；COS `PUT -> HEAD -> readback -> delete` 仍由 A1 独立 Gate 验收。
