# Owner Truth Legacy Shadow Parity Observer

## 目的

本切片继续 `WI-S1-01-09`，在已有只读旧数据 inventory 之上增加
`legacy/Projection` 的 shadow parity readiness observation。它只回答“当前
是否具备未来对比的前提”，绝不执行 backfill、authority cutover 或 legacy writer
retirement。

## 可调用边界

- 默认关闭，不出现在 OpenAPI。
- 仅在 `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`、有效用户会话以及
  `X-DreamJourney-QA-Owner-Truth: 1` 同时存在时可用。
- 路由：`POST /v2/vaults/{vaultId}/legacy-migration/shadow-parity`。
- 先以 Projection reader 验证 active V4 Vault、Owner 与 actor 一致；跨 Owner 或
  未知 Vault 在创建旧数据 inventory 前失败。
- 响应和路由均使用 `Cache-Control: no-store`。

## 输出合同

输出只包含 opaque run ID、SHA-256、计数、枚举和状态：

- `projectionRebuilding`：V4 Projection 尚未 ready。
- `legacyEvidenceIncomplete`：没有具备完整 Owner/Source/terminal Decision/revision
  证据的旧记录。
- `legacyRecordMappingRequired`：即使旧记录证据完整，仍缺少显式的
  `legacy record -> Source -> DecisionReceipt -> MemoryVersion` 映射。

所有路径都固定输出：

```text
cutoverAllowed=false
authorityEpochChanged=false
legacyWriterRetired=false
mappedRecordCount=0
```

因此该接口不是迁移命令，也不能被任何调用方当作切换许可。正文、旧 ID、图片描述、
KBLite 图谱和 Projection 内容不会进入报告、日志或持久化 summary。

## 验证

本地验证：

```bash
./scripts/verify_backend.sh
STORE_BACKEND=memory PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_legacy_shadow_parity \
  tests.test_owner_truth_legacy_shadow_parity_api -v
```

Postgres 验证会自动创建并删除独立临时数据库：

```bash
DATABASE_URL='<可创建临时数据库的 Postgres DSN>' \
  scripts/run-backend-owner-truth-legacy-shadow-parity-postgres-smoke.sh
```

它覆盖 Owner/Vault 先验、Projection ready、inventory 幂等、跨 Owner 拒绝、无正文
输出、无 V4 target 创建、authority epoch 不变和 legacy writer 不退休。

## 部署证据

2026-07-19（Asia/Shanghai）部署提交 `3d2f54b`。

- `/ready`：`database`、`schema`、`auth`、`incident` 均为 `ready`。
- `migrate_db.py --verify`：`expectedHead=0023`、`appliedHead=0023`、`status=ready`。
- 部署容器执行 `backend-owner-truth-legacy-shadow-parity-postgres-smoke.py` 通过：
  `schemaHead=0023 entries=2 status=legacyEvidenceIncomplete`。
- 部署容器执行 route-authentication smoke 通过：`routeCount=90`、无未分类路由，
  public/user/machine audience 边界均有效。

## 未完成且禁止自动推进的部分

1. 真实旧数据的逐条 lineage mapping、受控 backfill、差异处置和 Owner 审阅。
2. G2 的真实数据分布、备份/恢复、抽样批准与 cohort 证据。
3. `WI-S1-01-10` 的 Adult Self cohort、authorityEpoch CAS、forward-fix/rollback。
4. 任何公开 Context、Archive、KBLite 或 iOS UI 的 authority 切换。

这些均需独立 Work Item 和 Gate；本 Observer 不会隐式越过它们。
