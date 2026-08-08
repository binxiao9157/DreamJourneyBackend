# R4 OTP Provider 状态与恢复合同

## 目标与边界

本轮在既有手机号挑战/校验接口上做加法扩展，不选择具体短信厂商，
不发送真实短信，也不把 Provider 字段、凭据或回执暴露给 iOS。

保留兼容接口：

- `POST /v2/auth/challenges`
- `POST /v2/auth/challenges/{challengeId}/verify`
- `GET /config/runtime`

新增只读状态接口：

- `GET /v2/auth/challenges/{challengeId}`
- `GET /v2/auth/challenges/{challengeId}?recover=true`

`challengeId` 是高熵 bearer handle。不存在、Provider 模式漂移或 HMAC
版本漂移均返回中性 `identity_challenge_state_unavailable`。

## Provider-neutral gateway

发送端仍接收：

```json
{
  "challengeId": "ach_...",
  "identityType": "phone",
  "target": "8613800138000",
  "purpose": "login",
  "code": "123456"
}
```

最小成功响应仍为 `{ "accepted": true }`。可选响应字段为：

```json
{
  "accepted": true,
  "deliveryState": "accepted",
  "receiptId": "provider-opaque-id",
  "retryAfterSeconds": 30
}
```

配置 `IDENTITY_CHALLENGE_HTTP_JSON_STATUS_URL` 后，服务端可用
`{"challengeId":"ach_..."}` 查询状态。状态端只允许返回：

- `accepted`
- `delivered`
- `undeliverable`
- `unknown`

Provider 原始 `receiptId` 只在请求内存中存在，数据库只保存带版本的
HMAC 哈希。手机号和验证码继续只以 HMAC 哈希落库。

## 公开状态合同

原 `contractVersion=1` 保持不变，新增 `stateContractVersion=1`：

- `challengeState`: `active / verified / expired / locked / unavailable`
- `deliveryState`: `accepted / delivered / undeliverable / unknown`
- `attempt / maxAttempts / remainingAttempts`
- `retryAfterSeconds`
- `recoveryState`: `available / pending / notRequired / terminal / unsupported`
- `recoveryAttempt`
- `statusEndpoint`

状态组合 fail closed：`delivered` 必须对应 `notRequired`，
`undeliverable` 必须对应 `terminal`。`undeliverable` challenge 不可再验证。
验证码成功后仍只能消费一次；错误、过期、锁定、缺失和重放继续使用同一
中性失败合同。

## 生产策略

- production 禁止 synthetic adapter；
- send endpoint、API key 或 HMAC 配置不完整时，整个 OTP 能力关闭；
- status endpoint 可选，缺失时发送/校验仍可启用，但 recovery 为
  `unsupported`；
- Provider 429 归一化为 `identity_challenge_rate_limited` 与
  `Retry-After`；
- recovery 失败不破坏已受理 challenge，而是进入 `pending`；
- recovery 查询按 Provider retry interval 节流，查询使用 challengeId
  作为幂等键；
- 未配置真实 Provider 时 `/config/runtime` 继续 fail closed。

## 数据库

迁移 `0085_identity_challenge_delivery_state` 只给 `auth_challenges` 增加：

- delivery/recovery 状态；
- Provider 回执哈希；
- Provider retry interval；
- recovery attempt 与检查/送达时间。

迁移不新增手机号、验证码、原始回执或 Provider payload 字段。

## 非真机 Gate

```bash
PYTHON_BIN=.venv/bin/python \
  bash scripts/run-backend-identity-challenge-provider-gate.sh
```

组合 Gate 覆盖 synthetic、生产 fail-closed、Provider 受理、送达恢复、
恢复失败重试、Provider 限流、错误次数、一次性消费、重放拒绝、回执脱敏
以及 `0085` 迁移合同。

Postgres disposable smoke：

```bash
IDENTITY_CHALLENGE_PROVIDER_POSTGRES_SMOKE=1 \
IDENTITY_CHALLENGE_PROVIDER_SMOKE_DATABASE_URL="$DATABASE_URL" \
  bash scripts/run-backend-identity-challenge-provider-postgres-smoke.sh
```

真实短信 Provider、签名、模板、地域、测试号码和送达回执仍为
`WAITING_EXTERNAL_GATE`，不阻断后续非真机任务。
