# DreamJourney PostgreSQL 备份、清单与调度

日期：2026-07-16
适用 Work Item：`WI-S0-04-04`
Owner：Backend / Operations

## 目标与边界

- 生成独立于 Compose volume 的 PostgreSQL custom-format backup。
- artifact 完成后加密，并通过 root-only 的短生命周期解密验证文件交给 `pg_restore --list` 验证可访问性；验证结束立即删除明文临时文件。
- manifest 记录 `backupId/createdAt/schemaHead/LSN/checksum/size/encryptionRef/retentionClass/status`，不记录 DSN、密码、用户正文或业务 payload。
- 写入 artifact 前会比较运行库 `schema_migrations` 的已应用 head 与当前代码迁移 head；两者不一致时写入 `schemaHeadMismatch` failure receipt，不生成可验证 backup。
- 失败写 machine-safe receipt，systemd `OnFailure` 再写 owner 明确的 alert receipt。
- retention 仅生成 `auditOnly` 计划，不自动删除；任何流程都不得删除最后一份有效 backup。
- backup 成功不等于 restore 成功。隔离 restore、receipt replay、RPO/RTO 实测属于 `WI-S0-04-05`。

## 首次服务器配置

创建受限目录和仅 root 可读的本机加密 key：

```bash
sudo install -d -m 700 /etc/dreamjourney
sudo install -d -m 700 /var/backups/dreamjourney/postgres
sudo sh -c 'umask 077; openssl rand -base64 48 > /etc/dreamjourney/backup.key'
```

创建 `/etc/dreamjourney/backup.env`，不要提交该文件：

```dotenv
BACKUP_ROOT=/var/backups/dreamjourney/postgres
BACKUP_DB_SERVICE=postgres
BACKUP_DB_USER=dreamjourney
BACKUP_DB_NAME=dreamjourney
BACKUP_ENCRYPTION_KEY_FILE=/etc/dreamjourney/backup.key
BACKUP_ENCRYPTION_REF=server-local-key:v1
BACKUP_RETENTION_CLASS=operationalBackup35d
BACKUP_RETENTION_DAYS=35
BACKUP_KEEP_MINIMUM=1
BACKUP_MIN_FREE_BYTES=104857600
BACKUP_ALERT_OWNER=backend-operations
```

```bash
sudo chmod 600 /etc/dreamjourney/backup.env /etc/dreamjourney/backup.key
```

`server-local-key:v1` 只表示当前加密基线，不能冒充异地主密钥。生产外部门仍需把 key 和 backup artifact 分离到受控 KMS/off-host storage，并完成 Privacy/Legal retention 审批。当前脚本支持 `BACKUP_DB_USER`，但受限 backup role 的创建、密码托管和轮换仍需 Data/SRE 窗口；在此之前使用现有数据库 owner 只能标内部验证。

## 手工备份与验证

通过读取受限 `EnvironmentFile` 的 service 执行手工备份，避免在命令行展开配置：

```bash
sudo systemctl start dreamjourney-db-backup.service
sudo systemctl status dreamjourney-db-backup.service --no-pager
sudo journalctl -u dreamjourney-db-backup.service -n 100 --no-pager
```

列出 manifest，不输出 artifact 内容：

```bash
sudo find /var/backups/dreamjourney/postgres -maxdepth 1 -name '*.manifest.json' -type f -print
```

验证指定 manifest：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
CURRENT_SCHEMA_HEAD="$(
  .venv/bin/python - <<'PY'
from app.db.migrator import default_migrations_dir, load_migrations
print(load_migrations(default_migrations_dir())[-1].version)
PY
)"
sudo .venv/bin/python scripts/db/verify_backup_manifest.py \
  /var/backups/dreamjourney/postgres/<backup-id>.manifest.json \
  --expected-schema-head "$CURRENT_SCHEMA_HEAD"
```

验证通过只输出 value-free 摘要。checksum、size、schema head、过期时间或 artifact 任一不匹配都会非零退出。

authority cutover 或 contract migration 前必须额外验证最新备份不超过 36 小时：

```bash
sudo .venv/bin/python scripts/db/verify_latest_backup.py \
  /var/backups/dreamjourney/postgres \
  --expected-schema-head "$CURRENT_SCHEMA_HEAD" \
  --max-age-hours 36
```

`backupMissing`、`backupStale`、checksum/size/schema mismatch 或没有任何 current verified backup 时必须暂停 cutover；该结果不要求当前只读服务立即停机。

## 安装调度

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo install -m 644 deploy/systemd/dreamjourney-db-backup.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/dreamjourney-db-backup.timer /etc/systemd/system/
sudo install -m 644 deploy/systemd/dreamjourney-db-backup-alert@.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/dreamjourney-db-backup-retention-audit.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/dreamjourney-db-backup-retention-audit.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dreamjourney-db-backup.timer
sudo systemctl enable --now dreamjourney-db-backup-retention-audit.timer
sudo systemctl list-timers 'dreamjourney-db-backup*' --all
```

备份 timer 每日运行且带随机延迟；retention timer 每周只生成审计报告。`Persistent=true` 使服务器错过计划时间后在恢复时补跑。

## 失败、告警与恢复动作

- 中断、磁盘不足、`pg_dump`、加密、archive 验证和 manifest 校验失败都会清理 `.partial` 并写入 `failures/*.failure.json`。
- systemd `OnFailure` 写入 `alerts/backup-alert-*.json` 并发送 journal 事件，owner 默认为 `backend-operations`。
- 当前本地 alert receipt 不是外部 paging。正式 G2 仍需接入现有监控/值班渠道并演练送达。
- 失败时先暂停 authority cutover/contract migration，不必因为单次 backup 失败立即停止当前只读服务。
- 可停用 timer 修复：`sudo systemctl disable --now dreamjourney-db-backup.timer`。不得通过删除最后有效 backup 或伪造 success 解除 Gate。

## Retention 审计

```bash
sudo systemctl start dreamjourney-db-backup-retention-audit.service
sudo cat /var/backups/dreamjourney/postgres/retention-latest.json
```

输出的 `eligibleBackupIds` 只是候选；`automaticDeletion=false`。实际删除要等待 off-host copy、最后有效 backup 保护、Privacy/Legal retention 和 Operations 审批，不能由当前 timer 自动执行。

服务器部署证据可通过一键 smoke 复核：

```bash
sudo scripts/db/run-backup-deployed-smoke.sh
```

该命令只返回 value-free 汇总，检查两份当前加密备份、schema head、freshness、权限、retention audit、alert receipt 和两个 timer，不输出 checksum、backupId 或数据库内容。
