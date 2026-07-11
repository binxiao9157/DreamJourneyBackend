# Knowledge Operation Receipt 最小化与维护

## 目标

`kb_operation_receipts` 永久保留 operation identity、kind、schema version 和 payload fingerprint，用于幂等与冲突检测；`result` 不再长期复制完整知识图谱、原始 mutation upserts 或实体正文。

新写入使用 `receiptEnvelopeVersion=1` 的 compact envelope。历史 full result 通过独立维护命令转换，**不删除 receipt 行，也不修改 payload hash**。

## 部署顺序

1. 备份数据库并记录 `kb_operation_receipts` 行数与表尺寸。
2. 部署包含 legacy/full 与 compact 双读的新后端。
3. 运行后端健康检查和 knowledge receipt maintenance smoke。
4. 只运行线上 dry-run，审阅报告，不加 `--apply`。
5. 确认无失败用户、无异常 operation kind、候选数和预计节省量合理。
6. 在低峰期使用小 batch 显式 apply。
7. 立即再次运行 dry-run/apply，确认 `candidate=0`、`updated=0`，证明幂等。
8. 验证 duplicate replay、privacy maintenance 和 change-feed compaction。

不得在 reader-first 部署完成前运行 apply。

## 本地组合验证

```bash
scripts/run-backend-knowledge-receipt-maintenance-smoke.sh
STORE_BACKEND=memory scripts/verify_backend.sh
```

## 线上 Dry-run

默认模式不更新 receipt result：

```bash
python scripts/maintain_knowledge_operation_receipts.py \
  --keep-days 30 \
  --batch-size 100 \
  --lock-timeout-ms 5000 \
  --statement-timeout-ms 30000
```

必须审阅：

- `status` 必须为 `ok`；`partial` 需要先调查。
- `failedUsers`、`failureReasons` 和 `failed` 应为 0。
- `candidate` 是计划转换的 legacy full receipt 数。
- `alreadyCompact`/`skipped` 是无需更新的 compact receipt 数。
- `estimatedBytes.before/after/saved` 是 canonical JSON 估算，不等同于 PostgreSQL 物理表尺寸。
- `byKind` 应只出现 `kb.sync`、`kb.mutation`、`kb.governance`、`archive.delete`。

报告不得包含 graph、mutation upserts 或知识正文。

## Apply

确认 dry-run 后再显式执行：

```bash
python scripts/maintain_knowledge_operation_receipts.py \
  --apply \
  --keep-days 30 \
  --batch-size 50 \
  --lock-timeout-ms 3000 \
  --statement-timeout-ms 30000
```

维护按用户使用独立事务和 `knowledge:{userId}` advisory transaction lock。单用户锁超时、语句超时或转换异常会回滚该用户并继续其他用户，报告为 `partial`。不要在存在失败时盲目扩大 batch 或超时。

## 数据库观察

- 观察数据库 CPU、锁等待、WAL 增长和磁盘剩余空间。
- JSONB 更新会产生旧 tuple；物理空间不会在事务提交后立即归还。
- 根据托管 PostgreSQL 运维策略观察 autovacuum，必要时安排受控 `VACUUM (ANALYZE)`；不要在高峰期执行 `VACUUM FULL`。
- 若 `status=partial`，保留未转换的 full result，先处理失败原因再重跑。命令本身可重复执行。

## 回滚边界

- 单用户 apply 失败会回滚该用户事务。
- 已成功转换的 compact result 不包含原 graph/mutation，数据库回滚需要依赖部署前备份。
- 应用层无需回滚：后端 reader 同时兼容 legacy full 和 compact envelope。
- 不允许删除 receipt identity 行或重算 `payload_hash`。

## 部署后验收

1. 新 mutation 首次写入成功。
2. 同 operation ID、同 payload 返回 verified duplicate。
3. 同 operation ID、不同 payload 返回 conflict。
4. Change 仍存在时 duplicate 可精确重建。
5. Change 已压缩时 duplicate 返回当前 snapshot、空 V2 mutation、`receiptCompacted=true` 和 `originalRevision`。
6. `maintain_knowledge_privacy_metadata.py` 在 compact V2 receipt 存在时不报告 invalid record。
7. Change-feed compaction 继续把 receipt identity 行视为已验证屏障。
