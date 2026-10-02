# DreamJourney Live 长对话 run04 生产发布记录

- 日期：2026-09-20（Asia/Shanghai）
- 发布授权：用户明确确认“发布本轮后端修复”
- 发布状态：`DEPLOYED / INFRASTRUCTURE_READY / DEVICE_RETEST_PENDING`
- 证据边界：本记录证明代码、迁移、长期 Worker、备份和匿名基础探活完成；不等同于真实 Provider、真机长场或候选语义闭环通过。

## 1. 发布基线与范围

- 本地后端 HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`
- 本地工作区含未提交的 run04 累积修改；未执行 `git add`、`commit` 或 `push`。
- 生产发布目录：`/opt/services/dreamjourney/releases/live-long-memory-run04-20260920-2230`
- 当前发布指针：`/opt/services/dreamjourney/current` 指向上述 run04 目录。
- 上一发布指针：`/opt/services/dreamjourney/releases/live-candidate-run03-20260919-1745`
- 本轮发布内容：数据库迁移 0122、Backend API、所有当前启用的长期 Worker，以及长 Live/长记忆相关运行配置。
- 未访问或修改生产业务正文、候选、正式记忆、审核历史、Dead Letter 或历史失败任务。

## 2. 源码与迁移指纹

上传归档 SHA-256：

`c642004cc14ea4a010d312ff37e05d263fbe5ad93afa15409c483a4984758768`

关键文件 SHA-256：

- `app/services/owner_truth_live_memory_support.py`：`5bf7d1...`
- `app/services/deepseek.py`：`316f9e...`
- `app/async_effects/owner_truth_candidate_extraction_worker.py`：`975fa8...`
- `db/migrations/0122_owner_truth_live_long_memory_pipeline.sql`：`54109e...`

完整源码指纹以 run04 本地交付报告为准；本次发布前已与该报告核对关键文件。

## 3. 数据库迁移与备份

- 迁移前数据库 head：`0121`
- 迁移 dry-run：仅发现 `0122` 待应用。
- 迁移 apply：成功应用 `0122`。
- 迁移 verify：`expectedHead=0122`、`appliedHead=0122`、`pendingVersions=[]`、`status=ready`。
- 迁移前备份：成功，schema head 为 `0121`。
- 迁移后加密备份：成功，systemd `Result=success`、`ExecMainStatus=0`。
- 迁移后备份校验：`status=verified`、`schemaHead=0122`、`freshnessGate=passed`。

备份校验仅输出 value-free 摘要，未在本记录保存 backup ID、checksum、DSN、凭据或数据库内容。

## 4. 运行配置

容器内只读核对结果：

- `owner_truth_live_memory_organization_enabled=true`
- `owner_truth_live_long_memory_pipeline_enabled=true`
- `realtime_voice_long_live_profile_enabled=true`
- `candidate_worker_enabled=true`
- `store_backend=postgres`

没有放宽账号、FeatureGate、authority、CAS、hash、revision、Binding 或未知写保护。

## 5. 镜像与长期 Worker 对齐

API 当前镜像：

- `sha256:2a35518b2222b3247f0a8a50ed76b1bd9aecc8ea680a1a4dc055a4e7d1886b12`

启用 Worker 当前镜像：

- narrative generation：`sha256:4baa3f42a50a838c761ea599b1b0a550d6458c7486880761a1a57de571a0d246`
- candidate extraction：`sha256:9a8b7db6d5bea1bb8c6878c9f8563285d3dc1baa3af987bf944cdc657822bbba`
- memory projection：`sha256:0076672925d3a167ca317918ea24c44ae368520b90cb6f1808093d7603b71288`
- memory search embedding：`sha256:d5c0e2e7c7a8980f190158e11e3d271338818a20d9e7dc49505b8bde08933a13`
- media deletion：`sha256:291be0ddf9692b1ee69ac200fed9ff9eae1c6baf99faec0c5d7c43bc211e57ef`
- business message projection：`sha256:c1ead41ea6d65c29a317f85db548a7cf34b78d56cdf33b30be7ed0053203e5a3`

官方迁移后 Worker 对齐脚本结果：

- `migrationHead=0122`
- `workerCount=6`
- `status=passed`
- 每个启用 Worker 均完成镜像 migration head、数据库 migration head、activation preflight 和两次稳定采样校验。

最终额外双采样：API 为 `running/healthy/restart=0`；六个 Worker 均为 `running/restart=0`。近 15 分钟针对 `Traceback`、`CRITICAL`、迁移清单及 schema mismatch 的脱敏扫描均为 0。

## 6. 匿名基础探活

公网基址：`https://www.mmdd10.tech/dreamjourney-api`

`run-backend-readiness-deployed-smoke.sh` 通过：

- `/live` 合同通过；
- `/ready` 中 database/schema/auth/incident 均 ready；
- `/health` 兼容合同通过；
- 响应未泄漏 DSN、token、secret、checksum 或 SQL；
- readiness 绕过业务 UoW，且返回 `Cache-Control: no-store`。

## 7. 发布过程偏差与处置

### 7.1 macOS AppleDouble 文件

首次上传归档包含 `._*` 元数据，导致新 API 镜像内迁移扫描拒绝非法文件名。仅在尚未放流的新发布目录删除 1545 个 `._*` 文件并重新构建；未修改旧服务、数据库或业务数据。重建后镜像 migration head 为 0122。

### 7.2 Worker 对齐脚本项目名

首次运行 Worker 对齐脚本时未继承生产 Compose 项目名，安全门禁以 `apiImageMigrationHeadMismatch` 终止。该次只创建了一个未使用的空 network 和两个空 volume；没有连接生产数据库，也没有替换生产容器。

随后使用 `COMPOSE_PROJECT_NAME=dreamjourneybackend` 重新执行并通过。误建的 network 与两个空 volume 已精确删除，未执行 `docker system prune`，未删除任何生产 image、container、volume 或业务数据。

### 7.3 旧 Worker 与 0122 不一致

迁移后检查发现三个旧镜像 Worker 因迁移清单仍为 0121 进入 restart loop。按生产发布手册执行“所有启用长期 Worker 迁移后对齐”，六个启用 Worker 全部更新到 0122 并通过零重启稳定性检查。没有重放历史任务或修改队列内容。

## 8. 回退准备

保留的回退镜像：

- API：`dreamjourney-rollback-api:pre-run04-20260920-2230`
  - `sha256:32b53f07a600c87f874d3d9099144c9290766e879197f8357b1b2182b0b4c6c8`
- Candidate Worker：`dreamjourney-rollback-candidate:pre-run04-20260920-2230`
  - `sha256:6b7ce23004eb7a883d56c6675515ed11c15e13d84e15797d7804bcd2b84c392d`
- 上一发布目录仍保留。

迁移 0122 已应用，回退代码前必须先核对旧镜像对 0122 的兼容性；不得直接将长期 Worker 回退到 migration head 0121。优先局部关闭新增功能开关并保持 0122 schema；若必须镜像回退，应使用迁移后备份、当前发布手册和 Worker 全量对齐门禁，不删除新增表或生产数据。

## 9. 磁盘与清理

- 清理前：40GB 总量，约 5.1GB 可用，87% 使用。
- 安全清理后：约 11GB 可用。
- 发布及镜像对齐完成后：约 11GB 可用，73% 使用。
- 详细删除项与保留项见同目录《2026-09-20-DreamJourney-生产服务器磁盘安全清理记录.md》。

## 10. 当前结论与未完成项

### PASS

- 0122 迁移 apply/verify。
- 迁移前后加密备份。
- API 与六个启用长期 Worker 的 0122 对齐。
- API/Worker 双采样稳定性。
- 公网匿名基础探活。
- 回退镜像与上一发布目录保留。

### FAIL

- 修复版物理约 20 分钟长场完成 33 个用户回合与 15 项预定义事实，但停止后显示`当前无法继续整理，原对话已保留`。
- 等待并刷新后候选总数仍为 46，本场应保留事实候选为 0；Candidate Worker 没有观察到本场组织阶段事件。
- 详见同目录《2026-09-20-DreamJourney-Live长场停止后未进入候选-真机失败记录.md》。

### NOT_RUN

- 修复版真机短场大于 8 条事实与合法补充/改写验证。
- 候选可见、用户审核、正式记忆读取、冷启动幂等的本轮完整闭环。
- 真实 Provider 在本次发布后的专项证据。

下一步只能由新的合成真机数据验证本轮能力；不得补跑、清理或修改历史失败任务。
