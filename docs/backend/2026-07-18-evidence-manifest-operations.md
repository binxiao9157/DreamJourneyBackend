# DreamJourney Evidence Manifest 与 TTL 运维说明

日期：2026-07-18
适用 Work Item：`WI-S0-07-08`
Owner：Backend / Operations

## 目标与边界

- `/ops/evidence-manifests` 只保存验收证据的不可变、脱敏元数据：commit、build、环境、时间窗口、样本摘要哈希、排除项、schema、artifact 哈希、签发与过期时间、签发方、状态和 owner lease 哈希。
- 不保存或上传验收包正文、用户内容、音频、媒体、prompt、token、provider body、直接用户 ID 或完整日志。
- 旧报告没有 Manifest 时必须被验证逻辑标为 `evidenceManifestMissing`；不能借用新的 Manifest 伪装为已验收。
- 本地 iOS QA bundle 默认保存 7 天；后端持久化 Manifest 的 TTL 由 `EVIDENCE_ROLLOUT_RETENTION_DAYS` 控制。过期、hash 不匹配、缺少环境/窗口/排除项或非 `passed` 状态的证据不得关闭 Gate。
- 本文的 retention job 只删除已过期且不在 legal hold 中的 evidence event；它不会触碰业务档案、用户数据、备份或 provider 数据。

## 发布后安装 Timer

先完成普通后端部署并确认容器 healthy：

```bash
sudo -iu miao bash -lc 'cd /opt/services/dreamjourney/DreamJourneyBackend && git fetch origin && git pull --ff-only origin main'
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose up -d --build api
sudo docker compose ps
```

复制 systemd 单元并启用定时任务：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo install -m 644 deploy/systemd/dreamjourney-evidence-manifest-retention.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/dreamjourney-evidence-manifest-retention.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dreamjourney-evidence-manifest-retention.timer
sudo systemctl list-timers dreamjourney-evidence-manifest-retention.timer --all
```

预期 timer 每日 `03:45` 后在最多 15 分钟随机延迟内运行；`Persistent=true` 会在机器错过计划时间后补跑。不要把 `.env`、API token 或任何验收包正文写入 unit、journal 或本文件。

## 手工执行与检查

手工执行一次到期清理：

```bash
sudo systemctl start dreamjourney-evidence-manifest-retention.service
sudo systemctl status dreamjourney-evidence-manifest-retention.service --no-pager
sudo journalctl -u dreamjourney-evidence-manifest-retention.service -n 100 --no-pager
```

成功日志只允许包含 `schemaVersion`、job、状态、cutoff、`expiredCount`、`heldCount` 和 event ID 哈希。它不应包含 Manifest body、artifact 内容、用户标识或 credentials。

验证服务器部署时，使用一次性数据库 smoke，不写生产业务数据：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose exec -T \
  -e DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 \
  -e BACKEND_BASE_URL=https://dreamjourney-api.liftora.cn \
  api python scripts/backend-evidence-manifest-deployed-smoke.py
```

预期结果包括：`deployedReadiness=true`、`temporaryDatabase=true`、`productionBusinessDataMutated=false`、hash mismatch 被拒绝、legacy evidence 被拒绝、expired evidence 被拒绝，以及 retention 后 Manifest 数量为零。该 smoke 创建并清理临时 PostgreSQL 数据库。

## 故障和回退

- Timer 失败时，先阅读 value-free journal 输出；不要从 `evidence_events` 表手工删除行来伪造成功。
- 如果必须暂时停止自动清理：`sudo systemctl disable --now dreamjourney-evidence-manifest-retention.timer`。已过期证据仍不得被当作 Gate 证据使用。
- 若发现迁移或应用问题，回退 API 镜像/代码前先停用 timer；不要删除 `0010_evidence_manifest` 已写入的 append-only metadata。
- legal hold 的证据必须保留。解除 hold、调整长期保留天数或将证据提交到外部系统，需要 Operations、Privacy/Legal 的独立决定，不能由当前脚本自动完成。
