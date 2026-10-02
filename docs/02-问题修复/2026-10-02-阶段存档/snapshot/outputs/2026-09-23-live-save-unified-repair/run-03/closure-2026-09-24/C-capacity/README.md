# C：300 事实默认 Worker 落库精确验收

执行日期：2026-09-24。结论：**C_LOCAL_PASS**。本项没有修改产品源码、受保护测试或旧 runner；只新增本目录探针与证据。

## 实际完成

在全新隔离 PostgreSQL 数据库中，生成 150 用户轮 + 150 助手轮，每个用户轮含两个不同事实，共 300 个精确事实。创建该场 Source/admission 和业务 intent 后，使用未注入 extractor 的 `OwnerTruthCandidateExtractionWorkerRuntime` 默认装配，经真实 `DeepSeekLiveMemoryOrganizationProxy`、真实 loopback HTTP transport，将结果写入 PostgreSQL。

本项是既有 C 容量落库分支的定向补证，Source/admission 使用现有合成 seed；不声称本探针再次覆盖 iOS 采集或默认 API admission。默认 API → 官方独立 Worker CLI 的证明仍由同版四场证据承担。

最终 Source：`fef63fda-1769-4edb-8f0b-5905eca8ec27`。最终 Run：`f72bcaa1-fe7e-56de-b567-70c4814a8678`。结果全部按这一个 Source/Run 查询，未与初版校准或旧 extract 分支混算。

| 检查 | 最终结果 |
|---|---:|
| Worker 实际落库候选 | 300 |
| 与原始合成输入逐条匹配的事实 | 300 |
| missing / duplicates / extra | 0 / 0 / 0 |
| wrongSource / wrongTurn / wrongSpan | 0 / 0 / 0 |
| evidenceHash / evidenceIdentity / contentHash | 0 / 0 / 0 |
| manifestBinding | 0 |
| 故意破坏的验收反例 | 7/7 被拦截 |

除 claim 集合和次数外，逐条检查：候选为 pending knowledge；候选 Source 和 version、Source 正文与 conversationTurns；候选证据 span；候选 content hash；候选所属 extraction；manifest 的事实集合与 candidate ID 集合；manifest → atom 的 claim/turn 对应；原文片段的 start/end、textHash、evidenceId；atom evidence hash；Run/manifest 的 Source version/hash 和 published 状态。

7 个反例仅修改读回证据的内存副本，不改数据库：等量替换为重复候选、删除一项、增加非输入事实、错 Source、改为助手轮、改 evidence hash、改 evidence ID。等量替换即使仍为 300 条，也会报告 missing + duplicates，不能假通过。

## 预期值独立性与初版失败保留

`probe_exact_300.py:41` 的 fixture 在 Worker 执行前由固定公式生成 300 个原始事实、各自 user turn、拼接 Source 的字符 span，以及每个原文事实的 UTF-8 SHA-256。oracle 不从 Provider 响应或候选读回值生成预期事实。

Provider 请求内的片段 ID 与持久化证据 ID 是两个坐标合同。现有实现位于：

- `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_memory_support.py:34`：Provider 页内 ID 包含 turn ordinal。
- `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/async_effects/owner_truth_candidate_extraction_worker.py:1510`：验证 Provider 引用属于当前原文，再于 1533 行按 `SHA256(turnIndex:start:end:textHash)` 生成持久化 ID。
- `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/app/services/owner_truth_live_long_memory.py:281`：atom evidence hash 绑定 indices、ranges、memory 的规范 JSON。

独立 oracle 使用合成原文自行计算上述持久化坐标与 hash，未调用被测绑定函数。候选对整段 Source 的 span 与 atom 对 user turn 内部的 fragment range 分开验证。每个候选合法引用同轮两个原文片段，助手轮不能成为事实证据。

初版探针误把 Provider 页内 ID/turnOrdinal 当作持久化 ID，因此第一次链虽然已生成 300 候选，oracle 报 evidenceIdentity 不匹配。已保留初版脚本、失败、数据库读回和日志于 `oracle-calibration-first-run/`，另保留 `oracle-calibration-failure.json`。修正的是验收探针的坐标合同，不是放宽 Source/turn/span/hash/唯一性要求；最终从全新数据库重新执行，结果见 `execution-final.log`、`result.json`。

最初沙盒内启动/连接 PostgreSQL 被共享内存和 loopback TCP 权限拦截，也保留于 `pg-init.log`、`execution.log`；随后按本地测试授权在沙盒外执行。该环境失败不是产品失败。

## 单 Run 预算

最终只有一个 Run、一个 succeeded extraction，无单独 direct extract 分支：

- 受控 HTTP 请求 323 次 = 该 Run 持久化 attempts 323 = provider_request_count 323。
- 每个实际请求的 JSON hash 与保守输入字节预算，都与同 Run reservation 多重集合完全一致。
- 预留输入 7,891,288，预留输出 1,323,008；分别小于原快照 32,000,000 / 8,000,000；请求 323 小于 2,048；recovery_request_count 为 0。
- 19 次初始 atomExtraction response 被合同拒绝，随后已有细分路径的 atomExtractionRefinement 38、atomSupportRefinement 38 及 relationReviewBatch 228 被接受，共 304 accepted。被拒绝请求同样计入 323 次总预算；不写成所有响应都成功，也不把分页细分计成另一个 Run。
- 保留预算快照及 hash：`4308b39912069b89db3d929447798f9c7211f33e7f953dd17d23a15686f8a572`，没有扩预算或真实费用/usage 声明。

## 版本绑定

`dependency-fingerprint.json` 重新核对了 final-06 入口清单中的 **1,127 个后端受保护依赖，差异 0**。基线组合指纹为 `338578d2ce37d22a8191e37f0869251001371884a73fc09d3a2371c13632f23c`；新增探针 SHA-256 为 `6a9fd6e1426925556c2b4a12665a703740cc8062030f2bf1cb1db8c7ef6a06ba`。

N1 的 iOS 测试屏障变更和最终 final-07 四场由主代理处理。本项后端产物可绑定到相同后端指纹，不把组合指纹变动误写成产品后端已变化。

## 可执行命令与清理

已有隔离 PG 在 `127.0.0.1:55520` 时：

```sh
/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/closure-2026-09-24/C-capacity/probe_exact_300.py
```

每次探针自动创建独立 `dj_closure_c_<随机后缀>` 数据库，迁移、测试、读回并在 finally 删除该数据库；本场模型 HTTP 服务和 store pool 均已关闭。完整命令存于 `commands.sh`。

本次独立 PG 数据目录 `/private/tmp/dj-closure-20260924-pg/data` 为全新 initdb，未复制历史或生产数据库。没有读取 `.env` 或真实密钥，模型地址固定 127.0.0.1，API key 仅为合成占位值。

按主代理要求，PG 服务继续保留供 final-07 四场使用；关闭权已交接主代理。本项未卸载任何既有依赖。临时 PG 管理 DSN 为 `postgresql://djtest@127.0.0.1:55520/postgres`，仅用于本机测试。

## 证据入口和边界

- `result.json`：最终结论、错误计数、预算和状态。
- `synthetic-input-oracle.json`：执行前生成的合成事实与证据预期。
- `pg-readback.json`：本场 Source / candidates / atoms / manifest / attempts / extraction 原始读回（全部合成数据）。
- `fact-evidence-audit.json`：300 行精确匹配与错误列表。
- `oracle-negative-controls.json`：7 个反例。
- `request-ledger.json`：最终单 Run 的 323 个实际请求 hash/输入估算。
- `cleanup.json`：本场数据库与 HTTP 清理。

真实 Provider、iPhone、物理 20/65 分钟、部署、生产和历史重处理均为 **NOT_RUN**。本项只完成原定本地 C，不将模型质量、声学效果或外部现场结果写为通过。
