# Owner Truth 家庭资料贡献：closed-pilot 正式合同

## 目的

将原有仅供 QA 使用的“家庭成员提交静态资料”能力，升级为一条默认关闭、服务端可控的 M0 closed-pilot 合同。它不是家庭空间、共享档案或数字人权限。

## 正式路径

- Owner 创建授权：`POST /v2/vaults/{vault_id}/family-contribution/grants`
- Owner 撤销授权：`POST /v2/vaults/{vault_id}/family-contribution/grants/{grant_id}/revoke`
- 受邀成员提交静态文字：`POST /v2/vaults/{vault_id}/family-contribution/grants/{grant_id}/sources`

旧的 `family-contribution-grants` 路径仍是 QA-only，不能与正式授权互换使用。

## 生效条件

1. Owner 是服务端 `RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS` 白名单成员。
2. 服务端 `RELEASE_POLICY_CLOSED_PILOT_FEATURES` 显式包含 `ownerTruthFamilyContribution`。
3. Owner 创建或撤销时携带当前、服务器签发的 release-policy capture。
4. 家庭关系必须已接受，且 relationship epoch 与授权时一致。
5. 贡献者仅可按当前授权版本提交静态文字；授权被撤销、关系变更或服务端 feature 关闭后立即失败。

正式授权将 `admissionMode=closedPilot` 与值最小化的 `authorizationEvidence` 一起持久化。证据中不保存 bearer token、原始 session ID 或原始决策 ID；外部响应与数据导出也不返回该证据。

## 权限边界

允许：

- 一名已接受家庭成员向 Owner 私人 Vault 写入一条有来源标记的静态文字 Source。

不允许：

- 读取 Vault、Source、Candidate、MemoryVersion、Context 或任何 Owner 私人内容。
- 触发 Candidate 自动提取、确认、纠正、发布或访客访问。
- 获取 Persona、Voice、数字人、音视频或任何 Memorial 权限。

写入资料固定带有：`origin=familyContributionGrant`、`perspectiveType=familyReport`、`epistemicStatus=reported`、`candidateExtraction=defaultOff` 和 `familyContributionAdmissionMode`。

## 验证

本地契约 Gate：

```bash
PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-family-contribution-formal-gate.sh
```

部署或具备可创建临时数据库权限的环境：

```bash
DATABASE_URL='postgresql://...' \
  scripts/run-backend-owner-truth-family-contribution-formal-postgres-smoke.sh
```

后者只创建并删除随机命名的临时数据库，验证迁移、默认关闭、正式授权、内容脱敏、跨账号拒绝、私有读取拒绝和撤销生效；不读取或写入线上用户业务数据。
