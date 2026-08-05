# Stage 2 私有媒体摄入与处理

## 已实现范围

本轮将 Owner Truth 的媒体壳层推进为一个默认关闭、可审计的私有处理闭环：

1. 客户端先创建上传意图，再用一次性上传令牌把字节发送到后端；上传者自己的状态回执可保留文件名，但对象 URL、对象 key、处理正文和 Provider 凭据均不返回客户端。
2. 后端以文件魔数和内容安全扫描确认字节后，才把 `SourceObject` 进入处理队列。
3. `text/plain`、PDF、DOCX 在后端私有 Worker 本地解析，生成私有 `import` Source，再进入现有 Candidate 审核链路；不会自动写入确认记忆、Persona 或公开检索。
4. 图片 OCR 与音频 ASR 走同一套可配置 HTTPS Provider 合同。上传时必须显式指定 `allowExternalProcessing=true`，否则 Worker 不会向外部服务发送字节。
5. 视频当前只做私有存储，处理状态固定为 `notApplicable`，不伪造缩略图、转写或理解结果。
6. 临时 Provider 失败最多自动重试三次；终止失败后保留已验证的私有文件，用户可发起一次新的 `processingGeneration`，不需要重新上传，也不会改写文件版本。
7. 每次处理任务的终止失败都会在同一数据库事务内写入 value-free dead-letter：先写 SourceObject 失败状态与 typed consumer receipt，再使 job 终止，最后记录 dead-letter。重试耗尽的记录标记为 `maxAttemptsExceeded`，需经已有 restore-fenced 人工授权后才能申请 replay；不可恢复的处理错误标记为 `manualInterventionRequired`，不会被静默重放。
8. 私有媒体删除同样采用撤权优先：对象存储不可用或存储 Provider 不匹配时，访问仍保持撤销，当前删除 job 终止并写入 `manualInterventionRequired` dead-letter。用户后续的删除重试会创建新的 `deletionGeneration`，不会重放旧 job 或恢复访问。

这仍是 closed-pilot 能力：`ownerMediaCaptureV1`、摄入服务、处理 Worker 和内容安全扫描均须独立打开；默认公开发布态不可见。

## 状态与数据边界

```text
uploadPending
  -> verified / queued
  -> processing
  -> processed / succeeded     (本地文档或已授权 OCR/ASR 成功)
  -> verified / retryableFailed (自动重试中)
  -> verified / failed          (重试用尽或不可恢复错误，可创建下一处理代次)
```

- `storageVersion` 表示私有字节版本；当前实现中同一文件不会被处理重试改写。
- `processingGeneration` 表示一次独立处理请求；人工“重新处理”只增加该字段并生成新异步任务。
- `media_source_object_processing_results` 只存 processor 身份、状态、哈希、代次、尝试次数和可选 `derivedSourceId`。不存原文、OCR/ASR 文本、对象 key 或公开 URL。
- 成功提取的文本仅作为同一 Vault/Owner 下的 `import` Source 存储，仍需要 Candidate 审核。

## 私有对象存储

支持两种后端存储适配器：

| Provider | 用途 | 约束 |
| --- | --- | --- |
| `filesystem` | 服务器挂载卷或 Docker volume | 仅适用于具备备份、加密和删除策略的私有卷 |
| `s3` / `cos` | S3 兼容私有 Bucket；腾讯 COS 可使用 S3 endpoint | 禁止公共 Bucket、公共 ACL、预签名 URL；凭据仅在服务器 `.env` |

S3/COS 相关配置已在 `.env.example` 列出，均默认空值。可选指定 `AES256` 或 `aws:kms` 服务端加密；应用不会为任何 SourceObject 生成公共链接。

## 外部 OCR / ASR 合同

图片和音频使用 `httpJson` 时，Provider 接口必须满足：

```text
POST <HTTPS endpoint>
Authorization: Bearer <server-only key>
Content-Type: <源文件 MIME>
X-DreamJourney-Media-Kind: image | audio
X-DreamJourney-Processor-Contract: owner-truth-media-text-v1

body: 原始文件字节
response: { "text": "..." } 或 { "transcript": "..." }
```

不会发送：Vault ID、Owner ID、文件名、对象 key、对象 URL、客户端 token 或其他业务上下文。URL 必须是 HTTPS，不能携带 query、fragment 或嵌入账号密码。Provider 密钥只接受服务器环境变量。

只有同时满足下列条件才会发起外部调用：

1. 媒体种类为图片或音频；
2. 上传意图已显式声明 `allowExternalProcessing=true`，该授权随 SourceObject 持久化；
3. 对应 Provider 设置为 `httpJson`，且 URL、密钥、超时和字节上限有效；
4. 当前处理 Worker 已显式启用。

无授权、错误配置、网络超时、429 或 5xx 都不会生成伪造文本。无授权和不可恢复 4xx 会留下明确失败码；暂时不可用会重试，超过三次后以可重新处理的终止失败收敛。

## 部署前配置顺序

1. 先迁移到当前 Stage 2 schema head `0075`，并确认 `0073_owner_truth_media_source_objects`、`0074_owner_truth_media_processing` 与 `0075` 均成功执行。
2. 配置私有存储和生产可用的内容安全扫描；不要使用 `testClean`。
3. 仅在 Provider 的区域、保留期、删除能力、费用和 DPA 已确认后，再配置 `httpJson` OCR/ASR endpoint 与密钥。
4. 先保持以下开关关闭，执行本地 Gate：

```dotenv
OWNER_TRUTH_MEDIA_CAPTURE_ENABLED=false
OWNER_TRUTH_MEDIA_PROCESSING_WORKER_ENABLED=false
OWNER_TRUTH_MEDIA_IMAGE_OCR_PROVIDER=disabled
OWNER_TRUTH_MEDIA_AUDIO_ASR_PROVIDER=disabled
```

5. closed-pilot 验收时，按环境逐项打开摄入、内容安全、存储、异步 Worker 和一个 Provider；不要同时全量开放。
6. 处理 Worker 使用 Compose profile `owner-truth-media-worker`。filesystem 模式必须让 API 与 Worker 共享同一私有 volume；S3/COS 模式无需共享卷。

## 验证

本地执行：

```bash
scripts/run-backend-owner-truth-media-processing-gate.sh
```

该 Gate 覆盖：私有上传、跨 Owner 隔离、S3/COS 无公共 ACL、文本/PDF/DOCX 解析、OCR/ASR 授权合同、三次重试、人工新代次重试、迁移合同、路由归属和发布策略。

其中的 disposable PostgreSQL smoke 还会验证禁用的图片 OCR 在第三次失败后只产生一条
`maxAttemptsExceeded` dead-letter，且 job、typed consumer receipt、SourceObject 状态和
dead-letter 一并成为终态。它还会让私有对象删除 Provider 显式失败，验证访问撤销、
`partial` 删除状态、失败 job、typed consumer receipt 和一条
`manualInterventionRequired` dead-letter 在同一事务中落库；删除重试只生成新代次。该 smoke
不会调用真实 OCR/ASR Provider 或真实对象存储。

部署容器内执行：

```bash
DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 \
BACKEND_BASE_URL=https://dreamjourney-api.liftora.cn \
scripts/run-backend-owner-truth-media-processing-deployed-smoke.sh
```

### Worker lease 续租验证

媒体处理和物理删除 Worker 都会在对象存储读写、OCR/ASR 或其他外部调用期间，
以独立数据库事务更新已认领 job 的 lease。续租失败时不会写入处理成功或删除完成
结果，后续由既有 lease-expiry/recovery 流程处理。此行为不打开 closed-pilot 功能。

部署容器中可运行以下一次性 PostgreSQL smoke：它会故意让删除调用超过一秒租约，
验证第二个 worker 仍不能认领同一 job，随后第一个 worker 才能完成：

```bash
DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 \
BACKEND_BASE_URL=https://dreamjourney-api.liftora.cn \
scripts/run-backend-owner-truth-media-deletion-lease-heartbeat-deployed-smoke.sh
```

### Phase B 正式 closed-pilot 链路 Gate

该 Gate 在一次性 PostgreSQL 数据库中使用真实 FastAPI 路由、认证会话、服务端
release-policy 快照和正式 Candidate 路径。它不携带
`X-DreamJourney-QA-Owner-Truth`，也不会写入生产业务数据：

```bash
DREAMJOURNEY_OWNER_TRUTH_MEDIA_FORMAL_SMOKE=1 \
OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL='<admin database url>' \
scripts/run-backend-owner-truth-media-closed-pilot-formal-postgres-smoke.sh
```

它验证完整闭环：`SourceObject 上传 -> 私有处理 -> Candidate -> Owner 确认 ->
MemoryVersion -> Projection -> /context/build`，并验证非 Owner 不可读取候选。
该命令只验证正式后端合同，不会替代真实 closed-pilot 的身份、内容安全扫描、私有
对象存储和 Worker 部署前置条件，也不会打开公开发布态。

## 部署证据

- 2026-08-03：服务器后端版本 `98898cd`。PostgreSQL 已从 `0072` 迁移到
  `0074`，`0073`、`0074` 均成功执行；迁移校验结果为 `status=ready`、
  `pendingVersions=[]`。
- 2026-08-05：服务器源码已同步至 `abb317e`，并在新构建的临时 API 容器中运行
  Phase B 正式 closed-pilot Gate。结果确认：默认关闭、Owner 绑定上传、跨 Owner
  拒绝、私有对象落盘、处理完成、Candidate 确认、MemoryVersion 创建、Projection
  就绪和 `/context/build` 全部通过；执行过程未携带 QA header，临时数据库已自动删除。
- 2026-08-05：服务器已部署 `74ddcab`。API 容器内的 lease-heartbeat PostgreSQL
  smoke 通过，`leaseHeartbeatProtected=true`：删除调用超过初始 lease 后，竞争
  worker 没有接管 job，原 worker 完成物理删除；默认 worker profile 仍未启动。
- 2026-08-05：服务器已部署 `e71fda3` 与 smoke 修正 `59ebb7b`。API 容器内的
  Stage 2 PostgreSQL smoke 确认 `mediaProcessingDeadLetterAdmitted=true`：禁用图片
  OCR 在第三次失败后写入一条 open `maxAttemptsExceeded` dead-letter，SourceObject、
  typed consumer receipt 和 async job 均为终态。随后 lease-heartbeat smoke 再次通过；
  两个一次性数据库均已删除，默认 Worker profile 仍未启动。
- 2026-08-05：服务器已部署 `572d438`。API 容器内的 Stage 2 PostgreSQL smoke 确认
  `mediaDeletionDeadLetterAdmitted=true`：对象删除 Provider 不可用时，访问保持
  `accessRevoked`，SourceObject 变为可重试 `partial`，当前 async job 与 typed consumer
  receipt 均为失败终态，并写入一条 open `manualInterventionRequired` dead-letter。随后
  物理删除 lease-heartbeat smoke 通过，证明慢删除期间竞争 worker 不会接管 lease；默认
  Worker profile 仍未启动。
- 公网 `/ready` 验证通过：database、schema、auth、incident 均为 `ready`。
- 部署态 smoke 在服务器容器内创建一次性 PostgreSQL 数据库，完成后自动删除，没有写入生产业务表。
- E2E 已证明：公开默认关闭、Owner 绑定上传、命令幂等、跨 Owner 隐藏、私有文件落盘、媒体处理成功、派生 `import` Source、生成一条 `pending` Candidate、处理回执持久化及响应脱敏。
- E2E 首轮发现并修复了 PostgreSQL Candidate 层未接纳合法 `import` Source 的差异；回归测试已覆盖该来源类型。
- 生产环境仍未启动 `owner-truth-media-worker` profile，也未打开媒体摄入、图片 OCR 或音频 ASR；真实 Provider 验收不在本次证据范围内。
