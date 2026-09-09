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
- pgvector 镜像检查脚本存在且可执行；
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

### 4.1 pgvector 首次迁移门禁

当目标提交包含 `0113_owner_truth_memory_search_hybrid_pgvector`，但当前数据库镜像尚不含 pgvector 时，必须先完成本节，不能直接执行迁移。该变更沿用 PostgreSQL 16 数据卷，不升级 PostgreSQL 主版本。

1. 审核 `pgvector/pgvector:pg16` 的来源、架构和 PostgreSQL 主版本，在变更单中记录计划使用的不可变镜像摘要。
2. 拉取镜像后，将生产 `.env` 的 `POSTGRES_IMAGE` 设置为经过审核的 `pgvector/pgvector@sha256:...`。不得把摘要、`.env` 内容或数据库口令写入 Git、聊天或普通日志。
3. 切换生产数据库容器前，先在同一主机启动不挂载生产数据卷的一次性 pgvector 容器；在其中执行全部迁移、原子审核和 pgvector/Worker 烟测。所有烟测只写合成数据并自行删除数据库。

```bash
cd "$REPO"
sudo docker pull pgvector/pgvector:pg16
sudo bash scripts/verify-pgvector-image.sh live
# 使用 root-only wrapper 启动一次性数据库并通过 stdin 注入 DATABASE_URL：
bash scripts/run-backend-owner-truth-memory-changeset-group-postgres-smoke.sh
bash scripts/run-backend-owner-truth-memory-search-pgvector-postgres-smoke.sh
```

隔离数据库连接只能存在于受控进程环境，不能进入命令历史、交付报告或聊天。真实执行时必须通过 root-only 环境文件、stdin 或等价 secret 注入方式传入，禁止把 DSN 展开在命令行。

pgvector 烟测中的向量提供者是明确标注的确定性合成测试替身，只证明数据库、索引、Worker 重试和版本失效链路；它不构成真实 embedding 模型质量证据。真实模型必须另行运行 200 场景合成语料评测，并保留只含指标的结果。

### 4.2 B 迁移执行门禁

当目标提交包含 `0117_owner_truth_b_migration_execution` 时，在一次性
pgvector 数据库中额外运行：

```bash
bash scripts/run-backend-owner-truth-b-migration-execution-postgres-smoke.sh
```

该烟测必须证明：小批次检查点可恢复；中途事务失败不残留 Source 或
Outbox；重试复用确定性 Source；两个执行者不会重复生效；旧行改变后旧
dry-run 立即失效；最终只产生 `Source -> Candidate` 整理任务，正式记忆
写入数始终为零。烟测使用合成旧数据并删除本次可销毁数据库，不能接入
生产 DSN。

生产中的旧资料重放不是普通 schema 发布的一部分。只有经过单独审批、
确认 dry-run 报告后，才可临时设置
`OWNER_TRUTH_B_MIGRATION_EXECUTION_ENABLED=true`，并通过 root-only 环境
向 `scripts/execute-owner-truth-b-migration-batch.py` 注入 Owner、Vault、
report ID 和 `OWNER_TRUTH_B_MIGRATION_EXECUTION_ACK=YES`。每次最多处理
25 条（即单批最多 25 条）；执行结果只记录 hash、计数、状态和不透明 ID。完成或暂停后恢复
开关为 `false`。不得把该命令改成直接写 `MemoryVersion`，也不得用它执行
未经审批的生产语义迁移。

### 4.3 A 阶段 DFX 与真实模型门禁

切换生产容器前，必须在可销毁 pgvector 数据库运行服务端 baseline。该
入口使用生产 Postgres 仓储、事务、投影、HNSW 检索及 Worker，但 embedding
与候选整理均为确定性替身，因此只证明数据库和后端自身性能：

```bash
OWNER_TRUTH_DFX_POSTGRES_APPROVED=1 \
  bash scripts/run-backend-owner-truth-dfx-postgres-load.sh \
  --profile baseline --result /受控证据目录/owner-truth-dfx-postgres.json
```

结果至少包含 100 在线会话、20 检索 QPS、5 接收 QPS、3 整理并发，以及
可靠回执、内部混合检索、Live 预生成快照、原子审核、正式提交到可检索、
短会话到候选的 P50/P95/P99、失败/超时、连接池、数据库连接、积压变化、
CPU 和内存证据。任一目标不通过均不得改小预算后宣称通过。

`capacity` 档会创建 300 人乘每人 5,000 条的 150 万条合成事实，必须额外
设置 `OWNER_TRUTH_DFX_CAPACITY_ACK=YES`，并先核对磁盘、执行窗口与费用。
机器不足时允许分档执行并如实报告规模，但不能把较小规模外推为容量通过。

真实 embedding 质量另用 200 场景合成语料运行，且只有审批了模型、数据
出境边界与费用后才能设置
`OWNER_TRUTH_MEMORY_SEARCH_QUALITY_EVALUATION_APPROVED=1`。真实 DeepSeek
与火山用例也必须单列模型/供应商证据。DFX 的确定性替身结果、真实模型
结果与生产业务数据都不得互相冒充。

真实 DeepSeek 最小门禁只使用脚本内置合成事实，共发出三次请求，且报告
不保留提示词、模型回答或凭据：

```bash
OWNER_TRUTH_REAL_MODEL_VALIDATION_APPROVED=1 \
  python scripts/run-owner-truth-real-deepseek-validation.py \
  --result /var/lib/dreamjourney/evidence/owner-truth-real-deepseek.json
```

生产容器切换前必须完成迁移前备份并记录旧镜像摘要。`CREATE EXTENSION vector` 成功后，不得自动切回不含 pgvector 的普通 PostgreSQL 镜像。代码可通过关闭 embedding/hybrid 开关回退，数据库镜像必须继续提供已安装扩展；涉及数据恢复时遵循第 5.2 和第 6 节。

随后构建并执行前向迁移：

```bash
cd "$REPO"
export DEPLOY_BUILD_ID="$(sudo -iu miao git -C "$REPO" rev-parse --short HEAD)"
sudo docker compose up -d postgres redis
sudo bash scripts/verify-pgvector-image.sh live
sudo docker compose build api
sudo docker compose run --rm --no-deps api \
  python scripts/migrate_db.py --dry-run --build-id "$DEPLOY_BUILD_ID"
sudo docker compose run --rm --no-deps api \
  python scripts/migrate_db.py --apply --build-id "$DEPLOY_BUILD_ID"
sudo docker compose run --rm --no-deps api \
  python scripts/migrate_db.py --verify --build-id "$DEPLOY_BUILD_ID"
sudo docker compose run --rm --no-deps api \
  python scripts/rebuild-owner-truth-derived-projections.py --limit 100
sudo docker compose run --rm --no-deps api \
  python scripts/rebuild-owner-truth-derived-projections.py --apply --limit 100
sudo docker compose up -d --force-recreate api
sudo --preserve-env=DEPLOY_BUILD_ID \
  bash scripts/rebuild-enabled-workers-after-migration.sh
sudo systemctl start dreamjourney-db-backup.service
```

派生投影重建命令只从当前已授权正式记忆重建 memory/search projection，不修改
Source、Candidate、Memory 或 MemoryVersion，也不输出用户正文。执行 `--apply`
后必须再运行一次默认 dry-run；只有 `eligibleCount=0` 才能启动向量回填
Worker。若仍有 eligible target 或失败项，保持对应读取 fail-closed，不得直接把
checkpoint 状态改成 `ready`。

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
