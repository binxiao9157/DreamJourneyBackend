# APNs 生产 Provider 部署说明

## 1. 能力边界

后端已实现 Apple token 认证的 APNs HTTP/2 Provider，并复用加密 PostgreSQL
设备令牌仓库与持久化 Outbox。代码默认关闭；没有完整配置时不会注册设备、
发送通知或向客户端声明生产可用。

APNs 返回 `200` 只表示 Apple 接受请求，不代表通知已经到达设备。真机到达、
前后台展示、点击路由和权限状态仍属于后续真机验收。

## 2. 外部输入

在 Apple Developer 后台准备与目标 App、环境匹配的 APNs token key：

- Team ID
- Key ID
- `.p8` 私钥文件
- Bundle ID/topic：当前本机验收包为 `com.yxj.dreamjourney.app`
- 环境：Debug/Development 使用 `sandbox`，发布包使用 `production`

私钥不能提交到 Git，也不能粘贴进 `.env`。放在服务器仓库之外，例如：

```text
/opt/services/dreamjourney/secrets/AuthKey_XXXXXXXXXX.p8
```

文件 owner 设为 root，权限设为 `600`。

## 3. 服务器配置

先保持 `APNS_EXTERNAL_VERIFIED=false`，配置：

```dotenv
APNS_DELIVERY_PROVIDER=appleToken
APNS_TOKEN_VAULT_PROVIDER=postgresEncrypted
APNS_TOPIC=com.yxj.dreamjourney.app
APNS_ENVIRONMENT=sandbox
APNS_MAX_ATTEMPTS=3
APNS_TOKEN_ENCRYPTION_KEY=<Fernet key>
APNS_TOKEN_ENCRYPTION_KEY_VERSION=v1
APNS_TEAM_ID=<10-character Team ID>
APNS_KEY_ID=<10-character Key ID>
APNS_PRIVATE_KEY_HOST_PATH=/opt/services/dreamjourney/secrets/AuthKey_XXXXXXXXXX.p8
APNS_PRIVATE_KEY_PATH=/run/secrets/apns-auth-key.p8
APNS_REQUEST_TIMEOUT_SECONDS=15
APNS_EXTERNAL_VERIFIED=false
```

`APNS_TOKEN_ENCRYPTION_KEY` 用于加密数据库内的设备 token，不能与 APNs `.p8`
混用。更换该 key 前必须先设计 token 重加密流程，不能直接覆盖。

## 4. 部署与 Gate

1. 重建并启动 API 容器，确认 `.p8` 只读挂载成功。
2. 执行数据库迁移，确认 `0088_apns_postgres_outbox` 已应用。
3. 运行 `scripts/run-backend-apns-foundation-gate.sh`。
4. 运行 `scripts/run-backend-apns-postgres-outbox-gate.sh`；该 Gate 不调用 Apple。
5. 使用受控测试账户和匹配环境的真实设备 token 发送一次测试通知。
6. 只有 Apple 返回 accepted、后端回执已持久化且真机到达验收通过后，才设置
   `APNS_EXTERNAL_VERIFIED=true`。
7. 安装并启用 `dreamjourney-apns-outbox-worker.service`。生产 Provider 使用
   长驻 Worker 复用 HTTP/2 连接和 Provider token；旧的 oneshot timer 只保留给
   fake/运维 smoke，不与长驻 Worker 同时启用。

## 5. 失败与回滚

- `BadDeviceToken`、`DeviceTokenNotForTopic`、`Unregistered`：终态失败，不重试。
- `429`、`500`、`503`、Provider token 过期：进入可重试状态，受最大次数约束。
- 网络状态未知：保留 unknown 回执，不伪造成功。
- 回滚时先关闭 timer，再设置 `APNS_DELIVERY_PROVIDER=disabled` 并重启 API；
  已保存的 token 与 Outbox 不删除，等待审计后处理。

## 6. 仍需真机证明

- App entitlement、topic 和 sandbox/production 环境一致。
- token 注册、刷新、账号退出后的撤销。
- 前台、后台、锁屏通知到达。
- 点击通知打开正确消息或时间信件。
- 多次投递幂等、无效 token 收敛和弱网恢复。
