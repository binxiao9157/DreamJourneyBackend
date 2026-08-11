# 测试账号白名单管理

## 1. 目的与边界

该能力用于内部开发和真机 QA：测试人员可以使用平台生成的 11 位合成号码和
一次性披露的固定验证码，走现有 `/v2/auth/challenges` 登录流程。首次验证自动
创建账号，后续验证继续登录同一个账号。

它不是短信认证的通用降级方案，也不能用于真实手机号。功能默认关闭，普通号码
在短信 Provider 不可用时仍然 fail closed。

## 2. 安全约束

- 只接受 `100xxxxxxxx` 保留合成号段，不能创建真实中国移动手机号。
- 环境配置还必须提供 10 至 13 位的规范化号码前缀，将可用范围限制在小号段内。
- 数据库只保存目标号码 HMAC、验证码 HMAC 和脱敏提示，不保存原号码或明文验证码。
- 完整登录号码和验证码只在创建或轮换响应中披露一次，所有相关响应均为 `no-store`。
- 测试账号创建后永久有效，只有管理员主动停用时才失效。
- 禁用已绑定账号时，管理接口同时撤销该账号的全部登录会话。
- 管理接口只接受管理员安全会话或机器身份，并且都只授予 `testAccount:manage`
  scope；普通用户会话不能访问。
- 不提供物理删除接口，保留最小化的账号状态和审计证据；不再使用时应禁用。
- 管理员密码只以 PBKDF2-SHA256 哈希写入环境配置，浏览器使用短时、
  `HttpOnly`、`Secure`、`SameSite=Strict` Cookie，不把密码或会话写入浏览器存储。

## 3. 发布配置

先随版本执行 migration `0089_test_account_allowlist` 和
`0090_permanent_test_accounts`，再配置：

```dotenv
TEST_ACCOUNT_ALLOWLIST_ENABLED=true
TEST_ACCOUNT_ALLOWED_PHONE_PREFIXES=8610000000
TEST_ACCOUNT_ADMIN_ENABLED=true
TEST_ACCOUNT_ADMIN_USERNAME=dreamjourney_admin
TEST_ACCOUNT_ADMIN_PASSWORD_HASH=pbkdf2_sha256:...
TEST_ACCOUNT_ADMIN_SESSION_HMAC_KEY=<至少32字节的独立随机密钥>
TEST_ACCOUNT_ADMIN_SESSION_TTL_SECONDS=7200
TEST_ACCOUNT_ADMIN_COOKIE_NAME=dj_test_account_admin
TEST_ACCOUNT_ADMIN_COOKIE_PATH=/dreamjourney-api/ops/test-accounts
TEST_ACCOUNT_ADMIN_COOKIE_SECURE=true
```

`8610000000` 对应 `10000000000` 至 `10000000999` 的 1000 个合成号码。
`IDENTITY_BINDING_HMAC_KEY` 必须已配置且不少于 32 字节；更换该密钥或版本会令既有
测试账号凭据失效，需要重新创建。

管理员密码哈希可在部署机器上生成；命令会提示输入密码，不会把明文写入仓库：

```bash
python -c 'import getpass; from app.services.test_account_admin_auth import encode_test_account_admin_password; print(encode_test_account_admin_password(getpass.getpass("Admin password: ")))'
```

`TEST_ACCOUNT_ADMIN_SESSION_HMAC_KEY` 必须与其他密钥分开随机生成。生产环境通过
`/dreamjourney-api` 反向代理时，Cookie path 必须包含这个前缀；直接运行 API 时使用
`/ops/test-accounts`。

关闭开关即可立即停用整个测试登录通道，无需回滚数据库迁移：

```dotenv
TEST_ACCOUNT_ALLOWLIST_ENABLED=false
```

## 4. 管理页面

浏览器访问：

```text
$BASE_URL/ops/test-accounts/admin
```

管理员登录后可以新增永久测试账号、查看账号状态、重置固定验证码、停用和重新启用
账号。新增或重置时，完整登录号码与验证码只在弹窗中显示一次；关闭后无法从列表找回，
只能再次重置验证码。

登录连续失败 5 次后，同一来源会被暂时限制 15 分钟。管理员会话默认 2 小时失效；
退出登录会立即清除浏览器 Cookie。

## 5. 管理接口

以下示例中的 `$BASE_URL` 是后端 API 根地址，`$BACKEND_API_TOKEN` 是机器令牌。
不要把机器令牌写入客户端或提交到代码仓库。

### 5.1 创建账号

```bash
curl -X POST "$BASE_URL/ops/test-accounts" \
  -H "X-DreamJourney-Api-Token: $BACKEND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"target":"10000000001","label":"iPhone QA"}'
```

响应中的 `loginTarget` 和 `verificationCode` 只显示一次，必须由测试负责人通过安全
渠道交给测试人员。`GET` 列表无法再次读取验证码。

### 5.2 查看、轮换和状态管理

```bash
curl "$BASE_URL/ops/test-accounts" \
  -H "X-DreamJourney-Api-Token: $BACKEND_API_TOKEN"

curl -X POST "$BASE_URL/ops/test-accounts/$ACCOUNT_ID/rotate-code" \
  -H "X-DreamJourney-Api-Token: $BACKEND_API_TOKEN"

curl -X POST "$BASE_URL/ops/test-accounts/$ACCOUNT_ID/disable" \
  -H "X-DreamJourney-Api-Token: $BACKEND_API_TOKEN"

curl -X POST "$BASE_URL/ops/test-accounts/$ACCOUNT_ID/enable" \
  -H "X-DreamJourney-Api-Token: $BACKEND_API_TOKEN"
```

轮换验证码后旧验证码立即失效。历史 `/renew` 接口只保留兼容性，会把旧版限时账号
转为永久并启用；新页面不再展示续期操作。

## 6. iPhone 使用方式

1. 在现有登录页输入管理接口返回的 `loginTarget`，例如 `10000000001`。
2. App 继续调用现有 challenge API；测试账号不会触发真实短信发送。
3. 在验证码弹窗输入一次性披露的 6 位 `verificationCode`。
4. 首次验证完成注册和登录；再次使用相同号码与验证码会进入同一账号。

iOS 无需持有白名单、机器令牌或特殊后门参数。测试通道的判定、验证码校验和账号
绑定全部发生在后端。

## 7. 验证与上线顺序

代码阶段已覆盖：

- 身份、路由、会话、运行时配置及迁移回归测试；
- 临时 PostgreSQL 中执行全部迁移至 `0090`；
- 首次登录建号、重复登录复用 Subject、禁用后撤销会话；
- 原号码和明文验证码不落库；
- 管理员密码哈希、签名会话、防篡改、过期、限流和 Cookie 安全属性；
- 管理员会话不能访问其他运维接口，用户会话不能访问白名单管理接口；
- 非白名单号码在短信 Provider 不可用时继续拒绝登录。

上线顺序必须是：代码评审、备份、执行迁移、配置小号段、创建测试账号、最后再做
真机验证。当前代码开发和隔离测试完成，不代表已经部署或已经生成可用账号。
