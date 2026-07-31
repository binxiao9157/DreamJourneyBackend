# Owner Truth：ReviewReady 到确认收件箱的隔离 Postgres 验收

日期：2026-07-31

## 状态

`SCRIPT_READY / STATIC_VERIFIED / ISOLATED_POSTGRES_NOT_RUN / NOT_DEPLOYED`

## 目的

验证正式的候选提案状态读取与确认收件箱发现合同，而不是验证确认、修正或记忆激活。该验收只使用一次性隔离 PostgreSQL 数据库和合成数据，不连接部署环境、不读取生产数据。

## 覆盖范围

- Owner A 在同一 Vault 内有两条有效 `reviewReady` 批次；状态接口只返回指定批次的值最小化状态。
- 确认收件箱只返回 Owner A 当前 Vault 的有效批次；已删除 Source、authority epoch 失效、Owner B 和 Vault B 的批次均不可见。
- Owner B 请求 Owner A Vault 返回 `403`。
- 仅携带 QA Header 不能绕过正式 `ownerTruthCandidateReview` 发布策略。
- 状态与收件箱响应必须为 `Cache-Control: no-store`，且不返回 Candidate、Source、正文、回执、授权或 Provider 字段。
- 全过程不会新增 `DecisionReceipt`、`MemoryVersion`、记忆 Projection 或 `ProviderEffect`。

## 执行方式

仅在专用、可删除的 PostgreSQL 管理 DSN 已明确配置时运行：

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
DREAMJOURNEY_OWNER_TRUTH_REVIEW_READY_HANDOFF_SMOKE=1 \
OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL='<isolated-admin-dsn>' \
./scripts/run-backend-owner-truth-review-ready-confirmation-handoff-postgres-smoke.sh
```

脚本将创建、迁移并删除一次性数据库。没有该专用 DSN 时必须保持未执行；不要以服务器 `DATABASE_URL`、部署地址或生产数据库替代。

## 本地验证

- 静态 smoke 测试与关联候选审核 API 测试：19/19 通过。
- Python 编译、shell 语法和差异检查：通过。
- 完整隔离 Postgres smoke：未运行，原因是当前环境没有专用隔离管理员 DSN。

## 非目标

- 不执行候选确认、单条确认、修正或 Memory 激活。
- 不启动真实 extraction worker、Provider、数字人或语音。
- 不改变正式业务路由、公开 UI 或发布策略。
