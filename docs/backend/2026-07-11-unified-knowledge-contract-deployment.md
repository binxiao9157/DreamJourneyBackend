# 统一知识合同部署说明

## 变更范围

本次后端变更为 DreamJourney iOS 的统一知识主链路提供：

- `kb_snapshots.revision`
- `kb_changes` revision/change-feed 表
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
- generationContext 有稳定版本、来源、hash 和长度上限。
- 草稿/未到期时间信件、无效家庭数据及失败空线索不进入 generationContext。

## 回滚兼容

- iOS 请求 mutation/change-feed 收到 404 或 405 时会回退旧 `/kb/sync`。
- iOS 收到旧 `/context/build`（无 generationContext）时会按当前 query 使用本地 KBLite。
- 因此可以先部署 iOS 或先部署后端，但完整能力只有两端都更新后启用。

## 并发边界

- knowledge mutation 使用请求独占 Postgres connection，事务锁、commit、rollback 不与其他请求共享，结束后始终关闭。
- `kb_changes` 当前仍是完整快照 change feed；实体 tombstone、分页、保留期限和 compaction 属于后续版本。
