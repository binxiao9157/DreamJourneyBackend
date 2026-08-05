# 数据权利外部效果回执（P0-S3）

## 目标与边界

本轮把“访问已撤销”和“外部系统已完成删除/停用”拆成两个可验证事实。它不调用真实
对象存储、火山语音、腾讯数智人或通知 Provider，也不把数据库 tombstone 当作外部删除
完成。

适用外部域固定为：

- `objectStorage`
- `providerVoice`
- `providerDigitalHuman`
- `notificationDelivery`
- `backupRetention`

每个域的公开证据投影只能是 `pending`、`partial`、`unsupported` 或 `completed`。
没有真实上游回执时，状态必须保持 `pending` 或 `unsupported`，不能伪造完成。

## 写入与读取合同

`rights_external_effect_receipts` 是追加式事实表。每条记录只持久化：请求 ID、Owner 哈希、
域、效果身份哈希、状态、Provider 回执存在标记、原因码、观测时间、可选证据哈希和保留
时间。表不接收 Provider ID、对象 key、媒体 URL、原始用户标识或凭据。

数据库约束：

1. 回执 Owner 哈希必须与数据权利请求一致。
2. 同一观测哈希幂等；不同内容不能覆盖原记录。
3. 记录禁止更新和删除。
4. 迁移 `0078` 只增加该表、索引和 Owner 触发器，不修改已执行迁移。

读取时，仓储返回值和证据投影均不序列化 Owner 哈希、效果身份哈希或证据哈希。Owner 和
效果身份只作为进程内私有绑定使用：前者继续拒绝跨账号证据，后者让同一效果的历史
`accepted` 与最新 `completed` 正确折叠为当前完成状态。历史回执缺口仍反映为
`receiptState=partial`，不会被掩盖。

## 账号注销语义

账号注销先写既有访问撤销事实，再记录各外部域的当前边界：

| 域 | 当前记录状态 | 原因 |
| --- | --- | --- |
| 对象存储 | `unsupported` | 尚无已批准的外部对象删除适配器 |
| 语音 Provider | `unsupported` | 尚无真实 Voice 退出/删除适配器 |
| 数智人 Provider | `unsupported` | 尚无真实 Digital Human 退出/删除适配器 |
| 通知投递 | `unsupported` | 尚无可验证的通知 Provider 删除回执 |
| 备份保留 | `pending` | 需要后续保留期处理和外部回执 |

这使客户端和运维能区分“已经停止访问”与“仍待外部处理”。SourceObject 删除、授权撤销和
后续 Voice/DH 停用可复用同一回执合同，但真实 Provider 动作仍须在对应功能阶段实现。

## 验证与部署证据

本地验证：

```bash
.venv/bin/python -m unittest \
  tests.test_data_rights_external_effect_receipts \
  tests.test_data_rights_external_effect_projection \
  tests.test_data_rights_evidence_projection \
  tests.test_account_deletion_rights \
  tests.test_data_rights_external_effect_receipt_migration_contract
./scripts/verify_backend.sh
```

全量后端验证通过：`1806` 项单测及现有发布门。

2026-08-05，服务器最终部署版本为 `7d5ce0b`：

1. `0078` 已应用，迁移校验 `status=ready`、`appliedHead=0078`。
2. 公网 `/ready` 返回 database、schema、auth、incident 均为 `ready`。
3. API 容器内执行 `backend-data-rights-external-effect-receipts-postgres-smoke.py` 通过。
   该 smoke 使用一次性 PostgreSQL 数据库，验证 Owner fence、并发重放幂等、追加不可变、
   最新状态折叠和脱敏投影；完成后自动删除临时数据库，不写生产业务表。

## 后续边界

P0-S3 完成的是统一、可审计的状态和证据基础，不等于真实 Provider 已删除数据。下一阶段
需要分别为对象存储、Voice、Digital Human、通知和备份接入经过产品、隐私和运维批准的真实
执行适配器，并把上游回执写入本合同后再宣称该域完成。
