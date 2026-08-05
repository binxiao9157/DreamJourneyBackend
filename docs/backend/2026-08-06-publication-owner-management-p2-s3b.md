# P2-S3b：发布管理读取合同

日期：2026-08-06
状态：`IMPLEMENTED_PENDING_DEPLOYED_SMOKE`
范围：M2 closed-beta QA-only；默认发布态关闭。

## 目的

为 iOS“我的”中的 QA-only 发布管理壳层提供最小读取合同。该合同只允许 Owner 查看：

- 自己已创建的发布条目的独立公开预览与生命周期；
- 自己发出的 ShareGrant 的状态、到期时间和剩余次数。

它不创建 Visitor 公开入口、链接、深链或第四个 Tab，也不把 Visitor 接入私人 Echo。

## 内部 QA 路由

| 路由 | 返回 | 开关与请求头 |
| --- | --- | --- |
| `GET /v2/internal/owner-authority/vaults/{vaultId}/publications` | `publication-owner-management-v1` | `PUBLICATION_AUTHORITY_QA_ENABLED=true` 且 `X-DreamJourney-QA-Publication: 1` |
| `GET /v2/internal/publication-access/vaults/{vaultId}/grants` | `publication-owner-grant-list-v1` | `PUBLICATION_VISITOR_ACCESS_QA_ENABLED=true` 且 `X-DreamJourney-QA-Visitor-Access: 1` |

未满足对应开关或请求头时，路由在认证前返回 `404`；两条路由均不进入 OpenAPI schema，并返回 `Cache-Control: no-store`。

## 最小字段与隔离边界

发布条目只返回：发布/版本/草稿标识、发布与 projection 状态、Owner 已确认的公开预览、二次确认/第三方审查/AI 披露状态。

授权只返回：授权标识、发布/版本标识、状态、到期时间和剩余使用次数。

以下字段不得出现在响应中：`memoryVersionId`、Source/Object 内容、KBLite、Voice/Digital Human 状态、访客身份、授权凭据和 Provider 错误正文。跨账号访问同一 Vault 必须返回 `403`，不能通过空列表掩盖授权错误。

## 验证

本地合同验证：

```bash
.venv/bin/python -m unittest \
  tests.test_publication_management_read_api \
  tests.test_route_ownership_registry \
  tests.test_publication_authority \
  tests.test_publication_authority_api \
  tests.test_publication_visitor_access \
  tests.test_publication_visitor_access_api \
  tests.test_publication_authority_migration_contract
PYTHON_BIN=.venv/bin/python bash scripts/run-backend-publication-visitor-access-gate.sh
PYTHON_BIN=.venv/bin/python bash scripts/run-backend-publication-release-guard-viewstate-g0-gate.sh
```

部署后，在 API 容器内运行 disposable PostgreSQL smoke：

```bash
PYTHON_BIN=.venv/bin/python bash scripts/run-backend-publication-visitor-access-postgres-smoke.sh
```

该 smoke 创建并清理独立数据库，验证 Owner 读取、跨账号拒绝、授权使用次数 CAS、撤回和 Projection 阻断；不会写入生产业务数据。

## 后续

iOS 必须以单独的 `DJEnablePublicationManagementM2QA` 开关消费上述合同。该入口只能在 Debug/UI-QA 下显示，失败或过期时仅显示 M2 文字状态，不能跳转到私人 Echo、数字人或声音复刻链路。
