# 统一知识合同部署说明

## 变更范围

本次后端变更为 DreamJourney iOS 的统一知识主链路提供：

- `kb_snapshots.revision`
- `kb_changes` revision/change-feed 表
- `kb_change_feed_state.minimum_since_revision` 每用户保留水位
- `POST /kb/mutations`
- `GET /kb/changes/{user_id}`
- `/kb/sync` 和 `/kb/snapshot/{user_id}` 的兼容 revision 字段
- `/context/build.contextPacket.generationContext`

现有 `/kb/sync`、`/kb/snapshot` 和 Context V2 selected/filtered/ranking 字段保持兼容。

`/kb/sync` 的兼容行为：

- 首次旧客户端同步可创建 revision 1。
- 已存在 revision 后，未携带 `baseRevision` 的旧客户端请求返回 `200` 安全 no-op，并标记 `compatibilityNoOp=true`，不会覆盖较新的服务端图谱。
- 携带 `baseRevision`/`operationId` 的新请求继续使用 revision 冲突与幂等合同。

## 数据库迁移

应用启动时 `PostgresStore` 会幂等执行：

```sql
ALTER TABLE kb_snapshots
    ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS kb_changes (...);

CREATE TABLE IF NOT EXISTS kb_change_feed_state (...);
```

不需要手工删除或重建现有表。已有 snapshot 的 revision 从 0 开始，下一次 sync/mutation 会递增。

## 部署步骤

```bash
cd /path/to/DreamJourneyBackend
git pull --ff-only origin main
docker compose up -d --build backend
docker compose ps
curl -fsS https://dreamjourney-api.liftora.cn/health
```

实际服务名以服务器现有 compose 配置为准。不要在命令行或部署日志中输出 API token。

## 部署后验证

先运行基础验证：

```bash
scripts/verify_backend.sh
```

再使用服务器私密访问配置运行部署 smoke：

```bash
BACKEND_BASE_URL=https://dreamjourney-api.liftora.cn \
BACKEND_API_TOKEN='***' \
scripts/run-backend-knowledge-deployed-smoke.sh
```

验收点：

- 相同 operation ID 重试返回 duplicate，不增加 revision。
- 错误 baseRevision 返回 409。
- 无 baseRevision 的过期 `/kb/sync` 返回兼容 no-op，且最新 snapshot 不被覆盖。
- change feed 按 revision 升序返回。
- change feed 已支持 `limit`、`targetRevision`、`nextSinceRevision` 和 `hasMore`；首屏固定 target 后，后续页不会混入新写入。
- snapshot revision、change-feed 水位和当前页在同一用户 advisory lock 事务内读取。
- `sinceRevision` 早于 `minimumSinceRevision` 时返回 `410`，`detail.code=knowledgeChangeFeedCompacted`；客户端应重新拉取 `/kb/snapshot/{user_id}` 后从新 revision 继续。
- generationContext 有稳定版本、来源、hash 和长度上限。
- 草稿/未到期时间信件、无效家庭数据及失败空线索不进入 generationContext。

## 回滚兼容

- iOS 请求 mutation/change-feed 收到 404 或 405 时会回退旧 `/kb/sync`。
- iOS 收到旧 `/context/build`（无 generationContext）时会按当前 query 使用本地 KBLite。
- 因此可以先部署 iOS 或先部署后端，但完整能力只有两端都更新后启用。

## 并发边界

- knowledge mutation 使用请求独占 Postgres connection，事务锁、commit、rollback 不与其他请求共享，结束后始终关闭。
- `/kb/changes` 使用请求独占 Postgres connection，并在用户级 advisory lock 下原子读取 snapshot revision、水位和 change 页。
- mutation、change-page、account purge 与 compaction 统一使用 `knowledge:{user_id}` transaction advisory lock；同一用户的 snapshot/change/state 读写按该锁串行化。
- compactor 额外使用 session-level advisory lock 串行化维护实例，但不会要求在线请求获取全局锁。维护先取得用户列表快照，再按用户开启独立短事务；快照后新增的用户由下一次幂等执行补扫。
- `kb_changes` 当前仍保存完整快照 change；实体 tombstone 属于后续版本。稳定 target 分页、保留水位和保守 compaction 已实现。

## Change-feed compaction

先执行默认 dry-run：

```bash
STORE_BACKEND=postgres python3 scripts/maintain_knowledge_change_feed.py
```

确认报告后显式应用：

```bash
STORE_BACKEND=postgres python3 scripts/maintain_knowledge_change_feed.py \
  --keep-recent-revisions 1000 \
  --keep-days 30 \
  --lock-timeout-ms 5000 \
  --statement-timeout-ms 30000 \
  --apply
```

保留策略取两个窗口的并集：最近 1000 个 revision 或最近 30 天命中任一条件都会保留。脚本只删除连续旧前缀中已有 operation receipt 的 change；receipt 本身不会删除，无 receipt 的 legacy change 会成为压缩屏障。

每个用户的物理删除与 `minimum_since_revision` 推进位于同一个短事务，任一步失败只回滚该用户；已经提交的其他用户不受影响。用户锁或 statement 超时会跳过该用户，并在聚合报告的 `skippedUsers`/`skipReasons` 中记录，后续重复执行会从当前非零水位继续，因此可以安全补扫。session-level compactor lock 会阻止两个 maintenance 实例重叠，进程退出或 `finally` 都会释放该锁。
