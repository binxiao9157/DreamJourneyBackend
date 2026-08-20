# Owner Truth Stage 2: Tencent COS 私有媒体 A1

日期：2026-08-08
状态：`CODE_COMPLETE_PROVIDER_CONFIGURATION_PENDING`

## 1. 首发边界

首发真实对象存储固定为 **腾讯 COS**，通过 S3 兼容 API 由后端访问。

- App 不直接访问 COS，也不会收到 bucket、object key、永久 URL 或 COS 凭据。
- App 先从后端申请一次性上传 intent，再把字节上传到已认证的后端内容路由。
- 后端校验 owner、vault、intent、用途、MIME、文件大小、SHA-256 和安全扫描后写入 COS。
- 写入后后端必须调用 COS `HEAD Object`，复核 `Content-Length`、`Content-Type`、服务端保存的 SHA-256 metadata 以及显式 SSE；任何一项不匹配都不会把 SourceObject 标记为 `verified`。
- 读取仍通过已认证的后端内容路由；删除沿用“先撤权、后物理删除、再写 receipt”链路。COS 删除只有在 Provider 返回 2xx 回执且后续 `HEAD Object` 明确返回对象不存在时才算完成；未知回执、对象仍存在或 HEAD 不可用均保持 `partial/retryable`。

这保持了 Stage 2 的所有对象键和供应商细节在服务端，不会让移动端绕过 owner/vault 授权。

## 2. COS 控制台与最小权限

创建一个专用于 Owner Truth 私有媒体的 bucket，不与公开静态资源、日志或临时文件共用。

必须满足：

1. bucket 为私有读写，关闭公共访问、静态网站托管和任何匿名策略。
2. 首发使用 `SSE-COS (AES256)`；如合规要求 KMS，则使用 `SSE-KMS (cos/kms)` 和专用 CMK。
3. 首发不启用会保留旧版本字节的对象版本控制；否则账号删除/Source 删除不能证明物理清理完成。
4. 创建独立子账号或 CAM role，只允许指定 prefix 下的 `PutObject`、`HeadObject/GetObject`、`DeleteObject`；禁止 `ListBucket`、bucket policy/ACL 修改、跨 bucket 访问和公共 ACL。
5. 先用单独 closed-pilot 测试租户验证，不能直接对公开账户开启 `ownerMediaCaptureV1`。

腾讯 COS 的 S3 兼容 `PUT Object` 支持 `AES256`（SSE-COS）和 `cos/kms`（SSE-KMS）；bucket 名称必须带 APPID 后缀，region 和 endpoint 必须与 bucket 一致。[腾讯 COS 对象加密文档](https://cloud.tencent.com/document/product/436/63744) [腾讯 COS PUT Object 文档](https://cloud.tencent.com/document/product/436/7749)

## 3. 服务器 `.env` 最小配置

以下值只写入服务器私密 `.env`，不要提交、聊天粘贴或写入 iOS 工程：

```dotenv
OWNER_TRUTH_MEDIA_CAPTURE_ENABLED=true
OWNER_TRUTH_MEDIA_STORAGE_PROVIDER=cos
OWNER_TRUTH_MEDIA_STORAGE_EXTERNAL_VERIFIED=false
OWNER_TRUTH_MEDIA_STORAGE_EVIDENCE_TIMESTAMP=
OWNER_TRUTH_MEDIA_S3_BUCKET=<private-bucket-name-appid>
OWNER_TRUTH_MEDIA_S3_PREFIX=dreamjourney/private-media
OWNER_TRUTH_MEDIA_S3_REGION=<bucket-region>
OWNER_TRUTH_MEDIA_S3_ENDPOINT_URL=https://cos.<bucket-region>.myqcloud.com
OWNER_TRUTH_MEDIA_S3_ACCESS_KEY_ID=<least-privilege-secret-id>
OWNER_TRUTH_MEDIA_S3_SECRET_ACCESS_KEY=<least-privilege-secret-key>
OWNER_TRUTH_MEDIA_S3_SERVER_SIDE_ENCRYPTION=AES256
OWNER_TRUTH_MEDIA_S3_KMS_KEY_ID=

# Capture 还要求内部 ClamAV sidecar 能完成无用户数据的 INSTREAM 探测
# （包含可读的签名库）；否则 runtime capability 会继续 fail-closed。
OWNER_TRUTH_MEDIA_CONTENT_SAFETY_PROVIDER=clamav
OWNER_TRUTH_MEDIA_CLAMAV_HOST=clamav
OWNER_TRUTH_MEDIA_CLAMAV_PORT=3310
OWNER_TRUTH_MEDIA_CLAMAV_TIMEOUT_SECONDS=30

# 在真实对象存储通过 smoke 前保持其余处理链路关闭。
OWNER_TRUTH_MEDIA_PROCESSING_WORKER_ENABLED=false
OWNER_TRUTH_MEDIA_PROCESSING_EXTERNAL_VERIFIED=false
OWNER_TRUTH_MEDIA_PROCESSING_EVIDENCE_TIMESTAMP=
OWNER_TRUTH_MEDIA_DELETION_WORKER_ENABLED=false
```

使用 KMS 时把 `OWNER_TRUTH_MEDIA_S3_SERVER_SIDE_ENCRYPTION` 改为 `cos/kms`，并配置可访问的 `OWNER_TRUTH_MEDIA_S3_KMS_KEY_ID`。缺少 bucket、region、HTTPS endpoint、最小权限凭据或显式 SSE 时，runtime capability 将 fail-closed；endpoint 主机中的 region 与配置 region 不一致时也会在联网前拒绝，不会假装媒体功能可用。

`*_EXTERNAL_VERIFIED` 不是 Provider 开关。初次部署和内部 closed pilot 必须保持 `false`；只有真实 COS/扫描/删除 E2E 通过并保存验收证据后，才把存储项设为 `true`，同时写入对应 UTC ISO-8601 时间。处理 Worker 需要自己独立的验收开关和时间，不能继承存储结果。证据缺失、无效或超过 30 天时，普通用户媒体入口自动关闭。`filesystem` 即使误配为 `true` 也只允许独立 internal entitlement，永远不会成为 public Provider。

## 4. 部署与验证顺序

1. 在无凭据环境先运行组合合同 Gate：

   ```bash
   bash scripts/run-backend-owner-truth-media-provider-matrix-gate.sh
   ```

   该 Gate 使用 fake COS/ClamAV，覆盖缺配置、缺 SSE、错误 region、扫描器离线/超时、EICAR、删除未知回执和删除后对象仍存在；不会连接 Provider 或读取用户媒体。
   同时运行准入隔离 Gate：

   ```bash
   bash scripts/run-backend-pc-c1-media-admission-gate.sh
   ```

   它证明普通 Owner 需要外部证据、filesystem 仅限独立 internal entitlement，且真实 OTP 不会自动开放媒体。
2. 将后端升级到包含 A1 的版本，构建并重启 API 容器。
3. 在 API 容器内执行：

   ```bash
   RUN_BACKEND_OWNER_TRUTH_MEDIA_COS_PROVIDER_SMOKE=1 \
     python scripts/backend-owner-truth-media-cos-provider-smoke.py
   ```

   脚本只写入一段随机 probe、执行 HEAD/readback 后立即删除；输出不会包含 bucket、key 或凭据。
4. 查询 `/config/runtime`，先确认 `ownerTruthMediaStorage.provider=cos`、`providerReady=true`、`externalVerified=false`，且响应中没有 bucket、endpoint、SecretId 或 SecretKey。
5. 仅给测试租户打开 server-side closed-pilot policy，跑 Postgres media capture smoke，覆盖上传、授权读取、重复提交、过期、跨 owner、撤权和删除。
6. 保存 smoke 证据后设置存储 `EXTERNAL_VERIFIED=true` 和对应时间，重启并确认普通 Owner Release Policy 才从 `externalVerificationRequired` 切换为允许。
7. 通过后才考虑打开处理 worker；处理能力需要独立验收和独立证据时间，OCR/ASR/视觉分析仍各自需要 Provider Gate。

## 5. 当前部署结论

当前服务器尚未配置上述 `OWNER_TRUTH_MEDIA_S3_*` COS 变量，因此 A1 代码可以安全部署但会保持 `ownerTruthMediaStorage` 不可用。这是预期 fail-closed 状态，不代表真实 COS 已验证。
