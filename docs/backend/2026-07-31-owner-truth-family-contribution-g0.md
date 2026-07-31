# Owner Truth 家庭静态贡献 G0

日期：2026-07-31
范围：V4 Phase 5C 的预 Memorial、Owner 私库家庭材料补充合同。

## 目标

在 Owner 已有的私有 Vault 中，允许 Owner 对一名已接受的家庭成员授予唯一的
`submitTextSource` 权限。家庭成员只能提交一条静态文字材料，不能读取 Vault，也不获得
Candidate、MemoryVersion、Voice、数字人、发布或 Memorial 权限。

## 合同与边界

- 默认关闭：`OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED=false`。
- 三条路由均不出现在 OpenAPI；正常产品请求返回 `404`。QA 请求还必须带认证用户会话和
  `X-DreamJourney-QA-Owner-Truth: 1`。
- 授权记录绑定 Owner、Vault、接受中的 family relationship、贡献者主体、relationship epoch 和
  固定 scope；创建与撤销均使用稳定命令哈希和版本 CAS。
- 提交时在同一 relationship lock 内重新验证 Vault Owner、relationship 接受状态、成员主体和
  epoch。暂停、撤销、换人或 epoch 变化都会 fail closed。
- 贡献 Source 被服务器标记为 `familyContributionGrant/familyReport/reported`，候选提取明确为
  `notRequested`，不会创建 async effect。
- 撤销只阻止后续提交；已经写入的 Source 保持不可变。贡献者退出、撤回、数据权利和 Memorial
  继承规则不在本 G0 的范围内，不能据此宣称已经完成。

## 与 Memorial shadow 的区别

既有 `owner_truth_memorial_family_contribution_shadow.py` 仍是未来 Memorial 设计的纯 shadow，
没有路由、持久化或写入能力。本次通道不带 represented persona，不可用于已故人格、声音、数字人或
公开内容，二者不能互相作为放行依据。

## 本地验证

```bash
PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-family-contribution-g0-gate.sh
git diff --check
```

该 gate 覆盖默认隐藏、Owner 授权、家庭成员提交、无正文回执、撤销、跨账号拒绝、关系状态/epoch
失效、无候选提取 effect、迁移 additive/default-off 合同和路由认证登记。

## 未关闭的 Gate

仅 G0 已验证。本次没有部署、没有线上 Postgres 迁移/烟测、没有公开 iOS 入口，也没有 G2/G3/G4
证据。部署前必须先在可丢弃的 Postgres 环境执行 migration 和跨账号负向 smoke；在产品明确贡献者
撤回与数据权利规则前，不得将该 QA-only 通道转为公开功能。
