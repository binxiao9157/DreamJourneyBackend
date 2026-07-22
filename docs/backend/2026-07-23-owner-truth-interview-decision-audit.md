# Owner Truth 访谈决策最小审计

日期：2026-07-23

## 范围

本切片补齐 V4 产品定义中的 `interview_decisions` 最小审计记录。它把一次已有
Owner narrative 的确定性访谈动作绑定为 append-only 记录，用于后续审计“为什么采取
deepen/clarify/pause 等动作”。

记录只包含 Vault、Owner、Thread、Session、消息 ID、action、reason code、policy version、
authority epoch 和幂等哈希；不存储：

- 对话正文、内容 hash、话题名称、用户输入信号；
- 模型输出、Provider payload、Candidate、MemoryVersion；
- 家庭成员、Visitor 或跨账号数据。

这是 default-off 的私有 Owner Truth 能力。当前没有新增公开 API、OpenAPI 路由、iOS UI 或
Echo 行为，也不实现自然语言主题识别。

## 边界与一致性

迁移 `0041_owner_truth_interview_decision_audits` 创建
`owner_truth.interview_decisions`。数据库和服务层共同保证：

1. 审计只可绑定 active Vault 中当前 Owner 的 `narrative` 消息；
2. Thread、Session、消息和 authority epoch 必须一致；
3. `expectedSessionVersion` 过期时拒绝写入；
4. 同一 command 可安全重放，同一消息不能得到两条不同审计；
5. 表 append-only，禁止更新和删除。

决策函数保持只读；写入在独立的 `OwnerTruthInterviewDecisionAuditService` 中显式执行，
避免读取/推荐过程产生隐式副作用。

## 验证

本地：

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_interview_decision_audit \
  tests.test_owner_truth_interview_decision_audit_migration_contract \
  tests.test_owner_truth_interview_session_orchestration \
  tests.test_owner_truth_interview_orchestration

PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh
git diff --check
```

部署后隔离 Postgres smoke：

```bash
DATABASE_URL='<admin postgres dsn>' \
  PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-interview-decision-audit-postgres-smoke.sh
```

脚本会创建并删除临时数据库，覆盖 `0041` migration、Owner 绑定、幂等重放、过期
Session 围栏，以及审计表中不出现叙述正文或 `content_payload`；不会读取或写入线上
业务 Vault、档案或对话记录。

## 部署证据

后端提交 `f5b57ca feat(m0a): add interview decision audit` 已部署。服务器已执行：

```bash
python scripts/migrate_db.py --apply --build-id f5b57ca
python scripts/migrate_db.py --verify
python scripts/backend-owner-truth-interview-decision-audit-postgres-smoke.py
curl -fsS https://dreamjourney-api.liftora.cn/ready
```

结果：`appliedHead=0041`、`expectedHead=0041`、`status=ready`；隔离 smoke 输出
`valueFree=true deduplicated=true staleFenced=true`，公网 `/ready` 为 `ready`。

## 非目标

- 不把 transient 的 `topic_id`、用户布尔信号或 Provider 结果当作权威审计事实；
- 不创建 Source、Candidate、MemoryVersion 或任何公开推荐；
- 不改变 Owner、家庭、Visitor 的权限模型；
- 不宣称公开引导式访谈或 M0 已完成。
