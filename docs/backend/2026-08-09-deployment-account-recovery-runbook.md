# DreamJourney 后端部署账号、回滚与恢复 Runbook

日期：2026-08-09
状态：`AUTHORITATIVE`
适用环境：`dreamjourney-api.liftora.cn` 对应服务器

本文是当前后端日常部署、回滚、配置备份隔离和数据库恢复的唯一主入口。旧的 `server-deployment-guide.md` 与 `server-update-operations.md` 只保留历史背景；发生冲突时以本文为准。

## 1. 固定角色与安全边界

| 角色 | 固定账户 | 职责 |
| --- | --- | --- |
| 部署操作入口 | `ubuntu` | 唯一 SSH 登录与整轮部署发起者 |
| Git 仓库账户 | `miao` | 持有部署目录和 Git 拉取凭据；只通过 `sudo -iu miao` 使用 |
| 特权执行边界 | `sudo/root` | Docker、root-only `.env`、备份和 systemd；不持有 Git 私钥 |

部署目录固定为 `/opt/services/dreamjourney/DreamJourneyBackend`，分支固定为 `main`。禁止 root 直接拉 Git，禁止把个人电脑私钥复制到服务器，禁止临时把 `.env` 改为普通用户可读。

同一名部署操作者必须从 `ubuntu` 会话完成整轮操作；Git 通过固定 `miao` 服务账户执行，特权命令通过非交互 `sudo` 执行。不得在部署中途切换到未登记个人账户。

## 2. 配置备份隔离规则

1. 活跃配置只允许位于部署目录 `.env`，owner 为 `root:root`，mode 为 `0600`。
2. 历史 `.env.backup*` / `.env.bak*` 不得留在 Git 工作区，统一隔离到 `/var/lib/dreamjourney/private-config-backups/<批次>/`。
3. 隔离目录为 `root:root 0700`，文件为 `root:root 0600`。
4. Git 永久忽略 `.env.backup*` 和 `.env.bak*`，但忽略规则不等于允许继续散落。
5. 配置备份不自动删除。每月由 Operations 生成只含数量、日期和审批编号的清单；销毁必须有显式审批、双人复核和销毁回执。
6. 不把配置备份混入 PostgreSQL backup，也不将其复制到聊天、工单或 Git。

一次性隔离旧文件时使用：

```bash
export BATCH="legacy-$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -o root -g root -m 700 \
  "/var/lib/dreamjourney/private-config-backups/$BATCH"
sudo find /opt/services/dreamjourney/DreamJourneyBackend -maxdepth 1 -type f \
  \( -name '.env.backup*' -o -name '.env.bak*' \) \
  -exec mv -t "/var/lib/dreamjourney/private-config-backups/$BATCH" -- {} +
sudo chown root:root "/var/lib/dreamjourney/private-config-backups/$BATCH"/*
sudo chmod 600 "/var/lib/dreamjourney/private-config-backups/$BATCH"/*
```

这只是隔离，不是删除。空批次目录可保留；不得用通配符执行 `rm`。

## 3. 部署前 Gate

从 `ubuntu` 会话运行：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
bash scripts/deployment-preflight.sh
```

该检查只输出 value-free 摘要，必须验证：

- SSH 操作者是 `ubuntu`，没有 root 直接登录部署；
- 仓库 owner 与 Git 凭据账户都是 `miao`；
- `main` 工作区干净且能读取 `origin/main`；
- `.env` 和私密备份目录权限正确，旧备份已移出仓库；
- Compose 配置有效；
- 数据库备份和 retention timer 均已启用。

任一项失败均停止部署，不能通过 `git reset --hard`、放宽密钥权限或跳过备份来解除。

## 4. 固定提交部署

先记录部署前版本，并在仓库代码仍与当前数据库 schema 一致时生成迁移前备份：

```bash
export REPO=/opt/services/dreamjourney/DreamJourneyBackend
export PREVIOUS_COMMIT="$(sudo -iu miao git -C "$REPO" rev-parse HEAD)"
cd "$REPO"
sudo systemctl start dreamjourney-db-backup.service
```

确认备份服务成功且 manifest 的 `schemaHead` 等于当前数据库 head 后，才拉取目标提交；只接受 `main` 的 fast-forward：

```bash
sudo -iu miao git -C "$REPO" fetch origin main
sudo -iu miao git -C "$REPO" pull --ff-only origin main
export TARGET_COMMIT="$(sudo -iu miao git -C "$REPO" rev-parse HEAD)"
```

随后构建并执行前向迁移：

```bash
cd "$REPO"
export DEPLOY_BUILD_ID="$(sudo -iu miao git -C "$REPO" rev-parse --short HEAD)"
sudo docker compose up -d postgres redis
sudo docker compose build api
sudo docker compose run --rm --no-deps api \
  python scripts/migrate_db.py --dry-run --build-id "$DEPLOY_BUILD_ID"
sudo docker compose run --rm --no-deps api \
  python scripts/migrate_db.py --apply --build-id "$DEPLOY_BUILD_ID"
sudo docker compose run --rm --no-deps api \
  python scripts/migrate_db.py --verify --build-id "$DEPLOY_BUILD_ID"
sudo docker compose up -d --force-recreate api
sudo --preserve-env=DEPLOY_BUILD_ID \
  bash scripts/rebuild-enabled-workers-after-migration.sh
sudo systemctl start dreamjourney-db-backup.service
```

最后一次备份必须对应迁移后的新 schema head。这样迁移前、迁移后各有一个可验证恢复点，也避免新代码 head 在迁移执行前把旧数据库误判为未知 schema。

Worker 对齐脚本以代码内的长期 Worker 注册表为唯一清单，覆盖 4 个 Owner Truth Worker、消息投影 Worker 和发布外部清理 Worker。脚本只重建 `.env` 中明确启用的 Worker，并停止仍在运行但开关已关闭的旧容器。每个已启用 Worker 必须同时满足：镜像 migration head 等于仓库 head、数据库已应用同一 head、activation preflight 为 ready，且强制重建后连续两次保持 `running`、`RestartCount=0`。任一检查失败都必须停止放流，不能保留旧 Worker 镜像继续运行。

Worker activation 失败时会在标准错误输出一条脱敏 JSON 诊断，字段包括 `worker`、`failureStage`、`failureCode`、`retryable` 和 `correlationId`。排障时以 `correlationId` 关联同一次启动尝试，并依据 `failureStage` 区分配置加载、Store 创建、数据库连接、readiness probe、Store 关闭或 activation evaluation；不得把原始异常、连接串、Provider 凭据或任务 payload 写入日志。部署脚本检测到容器未运行、处于 restarting 或两次采样间发生重启时必须失败并触发部署告警，不能依赖 `restart: unless-stopped` 无限自愈。

放流 Gate：

```bash
curl -fsS https://dreamjourney-api.liftora.cn/ready
BACKEND_BASE_URL=https://dreamjourney-api.liftora.cn \
  scripts/run-backend-readiness-deployed-smoke.sh
```

按本次功能范围继续运行对应 deployed smoke。保存 `PREVIOUS_COMMIT`、`TARGET_COMMIT`、migration head、容器启动时间、readiness 和 smoke 摘要；不得保存 Token、DSN 或业务 payload。

## 5. 回滚

### 5.1 仅代码/镜像回滚

只在数据库 schema 与旧代码向后兼容时执行：

```bash
sudo -iu miao git -C "$REPO" merge-base --is-ancestor "$PREVIOUS_COMMIT" "$TARGET_COMMIT"
sudo -iu miao git -C "$REPO" checkout main
sudo -iu miao git -C "$REPO" reset --keep "$PREVIOUS_COMMIT"
cd "$REPO"
sudo docker compose build api
sudo docker compose up -d --force-recreate api
```

`reset --keep` 只允许在预检确认工作区干净且变更委员会明确选择已记录的 `PREVIOUS_COMMIT` 时使用。回滚完成后再次运行 `/ready` 和 deployed readiness smoke；随后通过正常 PR/fast-forward 恢复仓库分支，不做服务器上的长期分叉。

### 5.2 涉及数据库变化

- 不执行生产 down migration。
- 若新 schema 与旧代码不兼容，立即进入 `maintenance` 或 `readOnly`，采用 forward fix。
- 只有 forward fix 不可行且恢复审批完成时，才按数据库恢复文档在 `dj_recovery_*` 隔离数据库演练。
- 恢复脚本不得自动切流，`RECOVERY_EXPECTED_CUTOVER=NO_GO` 是当前默认值。

## 6. 数据库恢复演练

权威细节见 `docs/backend/2026-07-17-postgres-recovery-operations.md`。最低流程：

1. 选择 36 小时内、checksum/schema head 均有效的加密 backup。
2. 创建唯一 `dj_recovery_*` 数据库，不覆盖生产库或已有恢复库。
3. restore 后应用当前 forward migrations。
4. 运行 owner/authority、receipt replay、删除状态和 provider unknown 审计。
5. 生成脱敏 `recovery-record.json`；当前保持 `RECOVERY_EXPECTED_CUTOVER=NO_GO`。
6. 未经单独切流审批，不修改生产 DSN，不删除生产库。

备份与恢复 evidence 不得包含用户正文、手机号、Token、DSN、Provider 输入或明文密钥。

## 7. 故障停止条件

出现以下任一情况立即停止：

- Git 工作区不干净、目标提交不属于 `origin/main`；
- 最新有效备份缺失、过期或 schema head 不匹配；
- migration dry-run/apply/verify 不一致；
- 已启用 Worker 的镜像 migration head、数据库 head、activation preflight 或容器稳定性不一致；
- `/ready` 的 database/schema/auth/incident 任一不是 ready；
- Provider 配置校验泄露 secret，或 capability 从 fail-closed 意外变为 enabled；
- 回滚需要 down migration 或恢复记录仍为 `NO_GO`。

停止后保留当前可读服务或进入更严格的 `readOnly/maintenance`，不得伪造成功回执。
