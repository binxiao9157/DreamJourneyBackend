# WI-S0-07-03 关键 Worker 指标覆盖清单

日期：2026-07-23

Work Item：`WI-S0-07-03`

子切片：关键 Worker 覆盖盘点，`G0` scoped evidence only

状态：`INTERNAL_READY / G0_WORKER_COVERAGE_MANIFEST_VERIFIED / SHADOW_ONLY / G2_G3_G4_OPEN`

## 目的

既有 operation metric middleware 已覆盖 Route Authentication Registry 中的 HTTP
路由，但没有证明后台 Worker 同样具有 request / operation / attempt 分母。本轮
不假装补齐该运行时能力，而是把当前覆盖边界变为可执行、fail-closed 的清单。

## 本轮实现

- 新增 `app/observability/operation_metric_coverage.py`。
- 从现有 Route Authentication Registry 构建 HTTP route 清单；每一条已登记路由
  明确分类为 `instrumented`，因为它们均由现有 shadow middleware 覆盖。
- 显式登记两个当前关键 runtime：
  - `AsyncEffectWorkerRuntime`
  - `OwnerTruthMemoryProjectionWorkerRuntime`
- 两个 Worker 均标记为 `notInstrumented`，原因是尚未接入
  `OperationMetricRecorder.record_attempt`。
- 清单只产生 value-free 的本地摘要：`coverageComplete=false`、
  `sloClaimAllowed=false`。未覆盖、未知或不适用的条目不得变成 SLO 通过。
- 新增 AST 回归测试：`app/async_effects` 中新增任何 `*WorkerRuntime`，若未被
  清单显式登记，测试失败。
- 新增 `scripts/run-backend-operation-metric-coverage-gate.sh`，并纳入
  `scripts/verify_backend.sh`。

## 验证

```bash
PYTHON_BIN=.venv/bin/python scripts/run-backend-operation-metric-coverage-gate.sh
PYTHON_BIN=.venv/bin/python scripts/verify_backend.sh
git diff --check
```

结果：通过。全量后端验证执行 `1147` 个 unittest，并通过既有 credential、Owner
Truth、ReleasePolicy、Provider、async-effect、Postgres backup 等 gate。

## 明确未做

- 未修改 `app/main.py`、未新增或修改路由、未改变 API 响应。
- 未启用任何 Worker、未写入业务数据、未接入 Provider、未创建数据库迁移。
- 因运行时镜像和线上行为未变化，本子切片不要求部署或线上 smoke，不能据此声明
  `G2` 或生产 SLO 完成。

## 后续边界

后续如需提升该项，只能逐个为 Worker 增加等价的 value-free attempt 记录、重跑
Postgres persistence / restart smoke，并完成 retention、阈值和 Operations owner
审阅。完成本清单不关闭这些 Gate。

## 2026-07-30 更新：已执行 Worker 的 scoped attempt 覆盖

两个真实执行的 Owner Truth worker 已接入同一份 value-free attempt 合同：

- `OwnerTruthCandidateExtractionWorkerRuntime`
- `OwnerTruthMemoryProjectionWorkerRuntime`

它们只在成功领取一个 typed job 后记录 HMAC-protected job/operation 标识、attempt、
component、outcome 和耗时；不会记录 Source、Candidate、Memory、Projection checkpoint、
owner identity 或 HTTP route。记录 sink 失败不会改变 worker 的完成、重试或终态 lease。

`AsyncEffectWorkerRuntime` 继续为 `notInstrumented`，原因不是漏接，而是它没有业务
handler、从不领取或执行 job；为它生成 synthetic attempt 会伪造分母。覆盖清单仍为
`coverageComplete=false`，`sloClaimAllowed=false`。下一步只有在 generic shell 真正注册
typed consumer 后，才能为该执行路径设计独立指标，且仍需真实 Postgres persistence/restart
smoke、保留期、阈值和 Operations 审阅。
