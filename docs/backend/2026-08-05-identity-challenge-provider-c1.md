# C1 手机号 OTP Provider 适配闭环

> 2026-08-09 起，Provider 受理、送达回执和恢复状态的增量合同以
> `2026-08-09-identity-challenge-provider-r4.md` 为准；本文保留首次
> accepted-only 接入与历史部署证据。

## 范围

本次只完成服务器侧 OTP Provider 抽象和非真机验证，不启用真实短信供应商，也不改变 iOS 的身份挑战合同。

保留的公开接口：

- `POST /v2/auth/challenges`
- `POST /v2/auth/challenges/{challengeId}/verify`
- `GET /config/runtime` 中的 `auth.identityChallenge`

生产环境默认仍是 `providerMode=unavailable`，直到管理员明确配置并启用一个可用的 SMS gateway。

## 适配器边界

`HttpJsonIdentityChallengeAdapter` 是服务端的通用 HTTPS JSON 投递适配器：

1. 后端生成六位 OTP；
2. 后端只持久化 target/code 的 HMAC 哈希；
3. 后端把 `challengeId`、标准化手机号、purpose 和 OTP 投递给 SMS gateway；
4. gateway 仅在响应 JSON 为 `{ "accepted": true }` 时被视为接受；
5. 发送接受后，后端保存挑战记录，并在验证时用服务端哈希比较 OTP；
6. iOS 永远不保存 SMS provider endpoint、API key 或 OTP。

投递请求字段：

```json
{
  "challengeId": "ach_...",
  "identityType": "phone",
  "target": "8613800138000",
  "purpose": "login",
  "code": "123456"
}
```

鉴权使用 `Authorization: Bearer <IDENTITY_CHALLENGE_HTTP_JSON_API_KEY>`。实际供应商字段不兼容时，应新增命名 adapter；不要把供应商分支写进 iOS 或接口路由。

## 环境变量

```dotenv
IDENTITY_BINDING_HMAC_KEY=<至少 32 字节的独立随机值>
IDENTITY_BINDING_HMAC_KEY_VERSION=v1
IDENTITY_CHALLENGE_ADAPTER=httpJson
IDENTITY_CHALLENGE_HTTP_JSON_URL=https://sms-gateway.example.com/v1/challenges
IDENTITY_CHALLENGE_HTTP_JSON_API_KEY=<仅服务器保存>
IDENTITY_CHALLENGE_HTTP_JSON_TIMEOUT_SECONDS=10
```

安全规则：

- `synthetic` / `test` 只允许 development、local、test、testing；production 强制忽略；
- HTTP JSON endpoint 必须是无 query/fragment/内嵌凭据的 HTTPS URL；
- endpoint、key 或 timeout 无效时，工厂返回 unavailable adapter，客户端不能启动 OTP 流程；
- provider 接受失败时 API 返回中性 `503 identity_challenge_delivery_failed`，不返回手机号、验证码或上游报错；
- 发送失败不保存 challenge，因此不会产生一个可验证但未投递的 OTP。

## 非真机验证

```bash
PYTHON_BIN=.venv/bin/python bash scripts/run-backend-identity-challenge-provider-gate.sh
```

该 Gate 不访问外部网络，覆盖：

- synthetic 本地测试通道；
- HTTP JSON adapter 的发送接受、服务器 OTP 校验、单次消费和重放拒绝；
- 发送失败不持久化；
- 原始手机号和 OTP 不进入公开响应或持久化记录；
- production synthetic / 不完整 HTTP JSON 配置 fail closed；
- 已部署但未配置 provider 的现有 fail-closed smoke：

```bash
BACKEND_BASE_URL=https://<host> \
  bash scripts/run-backend-identity-challenge-deployed-smoke.sh
```

连接到具备创建临时数据库权限的 Postgres 后，可再执行：

```bash
IDENTITY_CHALLENGE_PROVIDER_POSTGRES_SMOKE=1 \
IDENTITY_CHALLENGE_PROVIDER_SMOKE_DATABASE_URL="$DATABASE_URL" \
  bash scripts/run-backend-identity-challenge-provider-postgres-smoke.sh
```

该 smoke 创建并删除独立数据库，跑真实 `PostgresStore` 的发送接受、错误次数、一次性消费、重放拒绝和发送失败不落库；其 gateway 仍是进程内 fake，不发送真实短信。

## 后续真实接入

真实 SMS gateway 选型、模板签名、地区/号码策略、供应商限流回执和真实手机号验收属于 C1 的外部交付部分。配置真实 provider 前，服务器继续使用：

```dotenv
IDENTITY_CHALLENGE_ADAPTER=disabled
```

因此本次部署可安全上线代码，而不会意外发送短信或开放生产手机号登录。

## 本次部署证据

部署版本：`main@e2d18e4`。

服务器完成 API 镜像重建和重启后：

- `/ready` 返回 `status=ready`，database、schema、auth、incident 均为 ready；
- `BACKEND_BASE_URL=https://dreamjourney-api.liftora.cn bash scripts/run-backend-identity-challenge-deployed-smoke.sh` 通过，确认生产仍为 fail-closed；
- API 容器内执行 `scripts/backend-identity-challenge-provider-smoke.py` 通过；
- API 容器内执行 `IDENTITY_CHALLENGE_PROVIDER_POSTGRES_SMOKE=1 scripts/run-backend-identity-challenge-provider-postgres-smoke.sh` 通过。

该部署没有设置 `IDENTITY_CHALLENGE_ADAPTER=httpJson`，没有接入真实 gateway，也没有投递真实短信。
