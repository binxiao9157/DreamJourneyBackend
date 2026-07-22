# C02：身份提升预检 Shadow

日期：2026-07-23
范围：`WI-MIG-01-03` 的 G0 身份、账户与 AuthZ 迁移预检。仅建立 value-minimized、fail-closed 的判定合同；不执行身份提升或业务数据迁移。

## 本轮实现

新增 `app/services/identity_promotion_preflight_shadow.py`，用于在任何 Owner 数据迁移前，对一条已经脱敏的 legacy alias claim 与服务端解析的账户上下文做纯内存预检。

输入边界：

- legacy alias 只能以 SHA-256 hash 进入；不接受手机号、token 或 Provider 原始值。
- 只有 `explicit_owner_claim` 可以带 claim subject 与 claim evidence hash。
- 上下文必须包含服务端 subject、Vault、session subject/Vault、resource/payload owner/Vault、AccountLease generation、route/resource decision 和两类 evidence hash。
- 原始标识仅在函数内参与派生 hash；公开摘要只输出 `aliasHash`、`scopeHash`、`evidenceHash`、状态和原因码。

fail-closed 规则：

| 情形 | 预检结果 | 不会发生的操作 |
| --- | --- | --- |
| legacy alias 为 `shared` 或已隔离 | `quarantined` | 自动 claim、登录、授权、Visitor 枚举 |
| legacy alias 为 `unknown` 或 `claim_pending` | `claim_pending` | 自动合并到任意 subject |
| session stale/revoked/missing | `denied` | 续发 session 或迁移 Owner 数据 |
| cross-Vault、payload/resource owner 不一致、AccountLease generation 不一致 | `denied` | 路由放行、资源放行或数据迁移 |
| principal 不是 server-derived，或 route/resource decision 为 deny | `denied` | 使用 payload/system/anonymous principal 提升身份 |
| 所有 G0 条件成立 | `shadow_eligible` | 仍不会 claim alias、写 subject/session、改路由、切流 |

`shadow_eligible` 的含义仅是“未来独立命令可再次验证的候选”。它不是生产 GO，仍强制要求独立的 G1、G2、G4 证据与事务性身份提升命令。

## 验证

```bash
PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-migration-identity-promotion-gate.sh
```

G0 测试覆盖：

- disabled 路径不读取或 hash 输入；
- 已验证 Owner 只能得到 shadow candidate，所有写入/切流/Visitor 枚举布尔值均为 `false`；
- shared、unknown、pending、quarantined legacy alias；
- cross-Vault、payload owner、AccountLease generation mismatch；
- stale/revoked/missing refresh session；
- payload principal、route/resource deny、claim subject mismatch；
- 输入 alias 不是 hash 时拒绝；
- 输出摘要不泄漏 subject、Vault 或 lease generation。

该 gate 已接入 `scripts/verify_backend.sh`，并额外检查模块不依赖 API routes、store、effects、Provider、HTTP client 或数据库 driver。

## 未关闭的门

本轮不包含以下内容，因此 `WI-MIG-01-03` 仍是 `NO_GO`：

- G1：iOS AccountLease / 本地 A-B store 隔离和旧版本行为证据；
- G2：部署环境中的真实 route/resource enforce、revoke/re-auth、cross-Vault corpus；
- G4：真实身份 Provider、恢复流程、产品与 Privacy/Legal 决策；
- 任何 legacy alias 实际 claim、subject/vault 写入、Owner 数据 backfill、C03 切流。

后续真实实现必须重新执行等价检查并在同一事务中持久化证据；不得把本模块的输出当作授权、登录或迁移命令。
