# DreamJourney 长期 Worker 启动与部署稳定性加固

日期：2026-08-27

状态：`DEPLOYED`

后端分支：`main`

最终代码提交：`2af0ade`

部署环境：`dreamjourney-api.liftora.cn`

## 1. 本轮目标

本轮处理两个稳定性问题：

1. 迁移后的 Worker 镜像对齐脚本只覆盖 4 个 Owner Truth Worker，遗漏消息投影和发布外部清理 Worker。数据库迁移后，遗漏的 Worker 可能继续运行旧镜像。
2. Worker 启动检查把数据库、配置、Store 和 readiness probe 异常统一折叠为 `ownerTruthWorkerReadinessProbeFailed`，无法快速判断失败阶段；配合 `restart: unless-stopped` 时容易形成缺乏有效诊断的重启循环。

目标不是重构异步任务架构，而是建立一个统一、可审计、默认失败关闭的 Worker 启动与部署边界。

## 2. 实现结果

### 2.1 统一长期 Worker 注册表

新增：

- `app/async_effects/worker_deployment_registry.py`

注册表成为长期业务 Worker 的唯一部署清单，统一维护：

| Worker | Compose service | 独立开关 |
| --- | --- | --- |
| `ownerTruthCandidateExtraction` | `owner-truth-candidate-extraction-worker` | `owner_truth_candidate_extraction_worker_enabled` |
| `ownerTruthMemoryProjection` | `owner-truth-memory-projection-worker` | `owner_truth_memory_projection_worker_enabled` |
| `ownerTruthMediaProcessing` | `owner-truth-media-processing-worker` | `owner_truth_media_processing_worker_enabled` |
| `ownerTruthMediaDeletion` | `owner-truth-media-deletion-worker` | `owner_truth_media_deletion_worker_enabled` |
| `businessMessageProjection` | `business-message-projection-worker` | `business_message_projection_worker_enabled` |
| `publicationExternalCleanupMaterializer` | `publication-external-cleanup-materializer-worker` | `publication_external_cleanup_materializer_enabled` |

一次性 `shadow-once` Worker、调度器、API、数据库、Redis 和 ClamAV 不属于这份长期业务 Worker 注册表。

注册表输出只包含代码标识和布尔启用状态，不输出环境变量值、Provider 配置或凭据。

### 2.2 统一 fail-closed 启动检查

新增：

- `app/async_effects/worker_activation.py`

六个 Worker 在启动实际循环前统一执行 activation preflight。检查范围包括：

1. Settings 是否能够安全加载。
2. Store 是否能够创建和打开。
3. 异步任务 schema readiness 是否满足。
4. 全局异步任务开关是否允许运行。
5. 当前 Worker 的独立 kill switch 是否开启。
6. Owner Truth 媒体 Worker 所依赖的 Provider 是否 ready。

失败时返回结构化、脱敏的合同：

```json
{
  "worker": "businessMessageProjection",
  "ready": false,
  "reason": "workerReadinessProbeFailed",
  "failureStage": "openStore",
  "failureCode": "storeOpenFailed",
  "retryable": true,
  "correlationId": "<bounded-id>"
}
```

诊断只允许包含：

- `worker`
- `failureStage`
- `failureCode`
- `retryable`
- `correlationId`

禁止记录原始异常文本、数据库连接串、Provider 凭据、对象 key、用户标识和任务 payload。

Owner Truth 的专用决策类型和规则已经并入统一模块。旧模块入口已删除，仓库只保留 `app.async_effects.worker_activation` 一套正式启动入口。

### 2.3 迁移后统一重建和稳定性检查

新增：

- `scripts/rebuild-enabled-workers-after-migration.sh`

新部署脚本按以下顺序处理注册表中的全部 Worker：

1. 校验仓库 migration head。
2. 校验 API 镜像 migration head。
3. 从当前 API 镜像读取 Worker 注册表和启用状态。
4. 对每个已启用 Worker 构建镜像。
5. 校验 Worker 镜像 migration head。
6. 校验数据库 applied head 与镜像一致。
7. 运行 Worker activation preflight。
8. 强制重建容器。
9. 连续两次采样容器状态，要求均为 `running`、非 `restarting`、`RestartCount=0`。
10. 对已关闭但仍有旧容器运行的 Worker 执行停止操作。

任一步失败都会中止部署，不允许旧 Worker 镜像继续带流量运行。

### 2.4 修复部署循环只处理第一项的问题

首次部署 `d722763` 时，注册表正确返回了多个 Worker，但脚本只重建了第一项。根因是：

- `while read` 循环从标准输入读取注册表；
- 循环内的 `docker compose` 命令继承并消费了同一个标准输入；
- 后续 Worker 行因此丢失。

修复提交 `6bd5078` 将注册表输入绑定到独立文件描述符 `3`，Docker 命令继续使用标准输入，两者不再互相影响。同时增加静态合同测试，防止后续改回共享标准输入。

## 3. 主要文件

| 文件 | 作用 |
| --- | --- |
| `app/async_effects/worker_deployment_registry.py` | 六类长期 Worker 的唯一部署清单 |
| `app/async_effects/worker_activation.py` | 统一启动决策和结构化诊断 |
| `docker-compose.yml` | 六个 Worker 接入统一 activation preflight |
| `scripts/rebuild-enabled-workers-after-migration.sh` | 迁移后镜像、schema、activation 和容器稳定性对齐 |
| `scripts/deployment-preflight.sh` | 部署前检查切换到新脚本 |
| `tests/test_worker_activation.py` | 注册表、失败分类和脱敏测试 |
| `tests/test_deployment_operations_contract.py` | 部署脚本合同和循环输入隔离测试 |
| `tests/test_owner_truth_worker_process.py` | 六个 Compose Worker 启动命令合同 |

## 4. 提交记录

| 提交 | 内容 |
| --- | --- |
| `d722763 ops: harden long-running worker activation` | 注册表、统一 activation、Compose 接入、迁移后重建脚本、测试和 Runbook |
| `6bd5078 fix: process every registered worker during deploy` | 修复部署循环只处理第一个 Worker，并补防回归测试 |
| `2af0ade refactor: remove legacy worker activation entrypoints` | 删除旧模块和旧脚本，所有运行时、Gate、smoke 与文档统一使用正式入口 |

以上代码提交均已推送至 `origin/main`。

## 5. 本地验证

已通过：

- Worker 与部署相关扩展回归：131 项。
- 最终定向回归：39 项。
- 文件描述符修复回归：19 项。
- Python `compileall`。
- `pip check`。
- Shell `bash -n`。
- 新旧 Worker 对齐脚本 `--contract-only`。
- 部署预检 `--contract-only`。
- `git diff --check`。

全量测试运行 2270 项时，仓库仍存在不属于本轮修改的基线问题：

- 49 个顺序相关的 PostgreSQL pool closed 错误；
- 1 个仍把 migration head 写死为 `0101`、而当前 head 已为 `0105` 的旧断言。

这些问题未被计入本轮通过项，也没有在本轮顺手修改。

## 6. 服务器部署记录

部署前服务器版本：`9923333`。

第一次更新版本：`d722763`。

循环修复后的最终版本：`6bd5078`。

已执行并通过：

1. 权威 `deployment-preflight.sh`。
2. 迁移前 PostgreSQL 验证备份。
3. `main` fast-forward 更新。
4. API 镜像重建。
5. migration dry-run、apply、verify。
6. API 强制重建。
7. 全部已启用 Worker 镜像重建和 activation preflight。
8. Worker 容器双采样稳定性检查。
9. 迁移后 PostgreSQL 验证备份。
10. 线上 `/live`、`/ready`、`/health` smoke。

数据库状态：

```text
expectedHead=0105
appliedHead=0105
pendingVersions=[]
status=ready
```

最终运行状态：

| 服务 | 状态 |
| --- | --- |
| API | `running / not restarting / RestartCount=0` |
| Candidate Extraction Worker | `running / not restarting / RestartCount=0` |
| Memory Projection Worker | `running / not restarting / RestartCount=0` |
| Media Deletion Worker | `running / not restarting / RestartCount=0` |
| Business Message Projection Worker | `running / not restarting / RestartCount=0` |
| Media Processing Worker | 配置关闭，无遗留容器 |
| Publication Cleanup Worker | 配置关闭，无遗留容器 |

线上 readiness 结果：database、schema、auth、incident 均为 ready。

## 7. 当前边界与后续项

本轮已经解决：

- 部署清单漏 Worker；
- Worker 镜像与迁移 head 可能不一致；
- 启动异常过度折叠；
- 部署后立即重启未被阻断；
- 关闭开关后旧容器可能继续运行；
- 循环只处理第一项 Worker。

仍需单独处理：

1. 当前重启检测属于部署 Gate。容器运行数小时后才发生重启时，仍需要接入服务器现有监控或值班渠道进行持续 paging。
2. 全量测试中的 PostgreSQL pool 隔离问题和旧 migration head 断言需要独立修复，不能混入 Worker 部署提交。
3. 本轮没有 iOS 改动，也不需要重新发布 iOS 客户端。

## 8. 后续部署要求

今后数据库迁移或 Worker 代码更新后，统一执行：

```bash
sudo --preserve-env=DEPLOY_BUILD_ID \
  bash scripts/rebuild-enabled-workers-after-migration.sh
```

不得再复制维护独立 Worker 列表，也不得跳过 activation、migration head 或双采样稳定性检查。
