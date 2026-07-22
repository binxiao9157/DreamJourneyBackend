# Owner Truth 已到期“以后再聊”推荐校准

日期：2026-07-23

## 为什么需要这次校准

终版 V4 功能说明明确把“用户明确选择以后再聊且冷却期已结束”的线索列为“接着聊”的第一优先级；
此前 ThreadPreference QA 合同却要求用户在到期后手动恢复，导致该线索永远不能进入推荐池。

本切片只修正这条读取语义，不把 Session 自动恢复为 `open`，也不开放任何用户界面或公开 API。

## 当前规则

1. `cooldownUntil` 始终由服务端写入，客户端不能提交或缩短；
2. 到期前，`cooldown` 仍完全不能进入推荐；
3. 到期后，只有当前 Owner/Vault/authority epoch 下仍为 `active Thread + paused/cooldown Session`
   的记录，才可被服务端计划为一条 `continuity` 候选；
4. 该候选固定使用 `continueElapsedCooldown` 和 `elapsedCooldownContinuation`，只绑定当前
   Owner-confirmed 覆盖证据，不读取或返回访谈正文、历史提示词或 Provider 输出；
5. 计划读取保持零写入：不会自动恢复 Session、改写 ThreadPreference、创建 Candidate 或
   修改 Memory；
6. `doNotAsk` 没有时间自动失效，仍需 Owner 显式确认恢复；
7. 同时存在普通 active/open 线程时，已到期 cooldown 是更高优先级的连续性来源；多个到期
   cooldown 以最早的 `cooldownUntil` 决定一个稳定候选。

## 验证

本地覆盖：

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_thread_preferences \
  tests.test_owner_truth_conversation \
  tests.test_owner_truth_knowledge_recommendations \
  tests.test_owner_truth_knowledge_recommendation_read \
  tests.test_owner_truth_knowledge_recommendation_read_api \
  tests.test_owner_truth_thread_preference_api
```

部署后使用：

```bash
scripts/run-backend-owner-truth-knowledge-recommendation-plan-postgres-smoke.sh
```

脚本只创建并删除一次性 Postgres 数据库，验证未到期抑制、到期连续性候选、确定性重放、
`doNotAsk` 抑制和计划零写入，不读取或修改生产业务数据。

## 部署证据

后端提交 53cab30（fix(m0b): prioritize elapsed cooldown continuations）已部署到 API 容器。

    python scripts/migrate_db.py --apply --build-id 53cab30
    python scripts/migrate_db.py --verify
    python scripts/backend-owner-truth-knowledge-recommendation-plan-postgres-smoke.py
    curl -fsS https://dreamjourney-api.liftora.cn/ready

结果：迁移账本 appliedHead=0041、expectedHead=0041、status=ready；隔离 Postgres smoke 输出
elapsedCooldownContinuity=true、doNotAskSuppressed=true 和 readOnly=true。公网 /ready 返回 ready。
本次未读取或修改线上业务 Vault、档案或会话数据。

## 非目标

- 不自动恢复麦克风、Echo、访谈 Session 或公开 UI；
- 不做自然语言主题抽取、模型话术生成或 Topic 合并；
- 不把 ThreadPreference 扩展到家庭成员、Visitor 或跨账号访问；
- 不代表公开双推荐或完整访谈产品已上线。
