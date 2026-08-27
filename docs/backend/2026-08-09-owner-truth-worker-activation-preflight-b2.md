# Owner Truth B2 Worker 启动预检

日期：2026-08-09

状态：`CODE_DEPLOYED / WORKERS_DISABLED / COS_PENDING`

## 目标

Owner Truth 的 Candidate、Memory、媒体处理和媒体删除 Worker 只能在依赖完整时进入长循环。仅启动 Docker Compose profile 不再足以让进程领取任务。

统一预检模块：

```text
app.async_effects.worker_activation
```

预检只输出 Worker、是否就绪、原因和阻断依赖，不输出凭据、bucket、object key、Owner、Vault 或业务载荷。

## 启动条件

所有 Worker 共同要求：

1. `STORE_BACKEND=postgres`。
2. `ASYNC_EFFECT_V1_ENABLED=true`。
3. PostgreSQL readiness probe 同时证明读写与迁移 head 正确。
4. `ASYNC_EFFECT_WORKER_ENABLED=true`。
5. 对应 Worker 独立开关为 `true`。

额外要求：

- Memory Projection 要求 Candidate Extraction 已启用。
- Media Processing 与 Media Deletion 要求私有媒体存储 capability 通过，包括采集开关、COS/存储配置和内容安全扫描器。
- Media Deletion 使用独立的 `OWNER_TRUTH_MEDIA_DELETION_WORKER_ENABLED` kill switch。

## Compose 边界

以下四个服务在进入 `--loop` 前必须先通过预检：

- `owner-truth-candidate-extraction-worker`
- `owner-truth-memory-projection-worker`
- `owner-truth-media-processing-worker`
- `owner-truth-media-deletion-worker`

任一依赖缺失时预检返回非零状态，容器不会领取任务。所有 profile 仍不属于默认 API 启动集合。

## 验证

本地 Gate：

```bash
scripts/run-backend-owner-truth-worker-process-gate.sh
scripts/run-backend-owner-truth-media-processing-gate.sh
scripts/run-backend-owner-truth-media-provider-matrix-gate.sh
scripts/verify_backend.sh
git diff --check
```

部署态默认关闭 smoke：

```bash
sudo docker compose exec -T \
  -e RUN_BACKEND_OWNER_TRUTH_WORKER_ACTIVATION_SMOKE=1 \
  -e OWNER_TRUTH_WORKER_EXPECTED_STATE=blocked \
  api bash scripts/run-backend-owner-truth-worker-activation-deployed-smoke.sh
```

真实 COS E2E 和各 Worker 开关完成后，将 `OWNER_TRUTH_WORKER_EXPECTED_STATE` 改为 `ready`，并按 B2 顺序逐项指定 `OWNER_TRUTH_WORKER_ACTIVATION_TARGETS` 验证。不得一次性打开全部 Worker。

## 当前证据

- 运行态配置已改为使用 live store readiness，不再把异步 schema 永久硬编码为未就绪。
- Worker preflight、Compose 启动边界和独立删除开关共 15 项专项测试通过。
- Stage 2 媒体处理 Gate 186 项、Provider matrix 44 项通过。
- 后端全量 1986 项测试及 FastAPI smoke 通过。
- 后端 `main@113d2ea` 已部署；migration dry-run/apply/verify 均保持 `0085`，公网 readiness 的 database、schema、auth、incident 全部为 ready。
- 部署态 activation smoke 已覆盖 Candidate Extraction、Memory Projection、Media Processing 和 Media Deletion；四类 Worker 均以 `asyncEffectV1Disabled` 明确拒绝启动，符合 B1 未完成时的 fail-closed 预期。
- 公网 `/config/runtime` 继续报告 `asyncEffect=false`、`ownerTruthMediaCapture=false`、`ownerTruthMediaProcessing=false`；媒体存储为 `runtimeDisabled`，媒体处理为 `workerDisabled`。
- API 重建后数据库备份 timer 与账号终态清理 timer 均保持 enabled/active。
- 真实 COS 尚未配置，因此生产媒体采集和全部 Owner Truth Worker 必须继续关闭。
