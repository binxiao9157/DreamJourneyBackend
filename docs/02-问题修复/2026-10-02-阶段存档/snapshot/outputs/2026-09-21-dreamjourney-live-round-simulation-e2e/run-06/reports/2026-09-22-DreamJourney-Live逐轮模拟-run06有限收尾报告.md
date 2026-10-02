# DreamJourney Live 逐轮模拟 run-06 有限收尾报告

日期：2026-09-22

状态：`LOCAL_PASS / REAL_PROVIDER_NOT_RUN / DEVICE_NOT_RUN / DEPLOY_NOT_RUN`

## 1. 范围与结论

本轮严格只完成 run-05 复核要求的 A3、B1/B3、C1/C2，并纳入 Astra 已完成的 429/timeout Worker 预算证据。没有重做或改写已认可的 Live 保存、音频、认证恢复和候选整理系统。

修前探针确认缺口位于验收接线：实际依赖未全部进入源码门禁，跨阶段证据仍按位置配对，候选到正式记忆缺少精确关系，support/relation 受控边界可放行伪造事实，长场负例被 200 轮前置限制挡住。修后相同业务断言通过。没有出现需要修改产品业务代码的新缺陷，因此本轮产品实现保持不变。

最终同一源码、配置和测试构建按以下顺序完整通过：

`logical20-short -> logical20 -> logical65-short -> logical65`

## 2. 修改范围

### iOS 验收代码

- `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift`
  - 从 SDK canonical member 冻结 raw callback/canonical/handoff/ingress 身份。
  - 从磁盘 canonical snapshot 读取 canonical→deliveryMessageID、capture ordinal、handled ingress/handoff 映射。
  - 用 message ID 查找真实 delivery，再记录 command/message/sequence/role/body digest；取消按位置 `zip` 的身份假设。

### 后端验收代码

- `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py`
  - 使用生产 `build_live_memory_evidence_catalog` 构造合法分页证据。
  - 事实语义与 Source 证明分开校验；允许生产合法的完整原 turn 或精确 evidence fragment 组合。
  - 核对 evidence range/hash、用户角色和页覆盖；不把内部 `_...` 字段或引用字段混入事实语义相等判断。

### run-06 验收装配

- `tools/cap15_round_simulation_server.py`
  - A3 依赖清单增至 25 项，并支持临时内容替换负例。
  - B1 串联 raw→disk→HTTP→receipt→Source 身份。
  - B3 串联 candidate→decision→formal memory/version→Source/manifest。
  - C1 核对 organization/support/relation 的当前事实、正文、证据引用和独立真值。
  - C2 使用生产合法 unit 真实命中受控 transport，不提高 200 轮上限。
- `tools/run_round_simulation.py`：固定最终门禁和 `short→20→short→65` 顺序。
- `tools/cross_stage_probe.py`、`tools/provider_input_probe.py`：隔离红绿探针。

## 3. 红绿证据

### A3

- 红：`red/gate-ledger-probe.stdout.json` 显示 4 个实际模块未进入 21 项门禁。
- 绿：`artifacts/protected-dependencies.json` 列出 25 项真实依赖及逐文件 SHA-256。
- 绿负例：两份 `negative/*-gate-negatives.json` 中修改候选读取实现后均返回 412；业务请求绝对计数保持 21，增量为 0。

### B1/B3

- 红：原探针在同角色同正文交换 raw 身份、损坏确认 identity 时仍接受；正式记忆只按数量/关键词验证。
- 绿：`green/cross-stage-probe-result.json` 为 4/4 正常映射，两个身份负例均 `detected`。
- logical20：220/220 跨阶段身份匹配；稳定事实关联 turn `[7,189]`。
- logical65：300/300 跨阶段身份匹配；稳定事实关联 turn `[7,221]`。
- 同正文错候选关系负例两场均被拒绝。

### C1/C2

- 红：`red/provider-input-probe-result.json` 中 support 伪事实和 relation 伪事实被接受；原 logical20/65 负例 transport 次数为 0。
- 绿：`green/provider-input-probe-result.json`：
  - short、logical20、logical65 的合法 unit 替换均实际命中 transport 1 次后拒绝。
  - 正常 support/relation 对照通过。
  - support 伪事实、缺必要证据、relation 伪造 incoming 事实均拒绝。
- 两份 full-chain 的 Provider 后置证据同时验证同索引替换和中段缺失。

## 4. 最终全链

| 场景 | 轮次 | 逻辑时间 | 候选 | 正式记忆 | 关键结果 |
|---|---:|---:|---:|---:|---|
| logical20-short | 4 | 120 秒 | 1 | 1 | 第二轮补充、Source 绑定、候选可见、审核、重建读回 PASS |
| logical20 | 220（110 用户） | 1200 秒 | 4 | 5（含 short） | 真实 iOS 候选读取、3→4 隐藏负例、401→刷新→原命令 201、3 次策略刷新 PASS |
| logical65-short | 4 | 120 秒 | 1 | 1 | fresh receipt 门禁后才允许 long PASS |
| logical65 | 300（150 用户） | 3900 秒 | 17 | 18（含 short） | 真实 iOS 候选读取、16→17 隐藏负例、最后一条响应丢失后只读 status、原 POST 1 次、12 次策略刷新 PASS |

原始结果：

- `green/logical20-short.xcresult`
- `green/logical20-long.xcresult`
- `green/logical65-short.xcresult`
- `green/logical65-long.xcresult`
- `green/logical20-full-chain.json`
- `green/logical65-full-chain.json`
- `artifacts/runner-complete.json`
- `artifacts/xcresult-summaries.txt`

四份 xcresult 各 `1/1 PASS`，0 failed，0 skipped。

## 5. 隔离 PostgreSQL 与回归

### PostgreSQL 全链

实际运行：

```bash
DREAMJOURNEY_OWNER_TRUTH_LIVE_FORMAL_SMOKE=1 \
OWNER_TRUTH_LIVE_FORMAL_SMOKE_ADMIN_DATABASE_URL='postgresql://<local-user>@127.0.0.1:55520/postgres' \
.venv/bin/python scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py
```

结果：PASS。临时数据库自动创建、迁移至 schema `0122`，通过真实 HTTP admission、Worker、候选审核、正式记忆、Store 销毁重建、Source/正式记忆读回及命令幂等重放后自动删除。

- short 候选 1
- long 候选 40
- 150 用户轮密集场候选 149
- 正式记忆合计 190
- 密集场 Source 301 turns / 73,728 characters
- 尾部纠正 `长舟终点号` 保留

证据：`green/backend-live-formal-postgres-smoke.json`。

### 受影响回归

- 后端：`92/92 PASS`
  - `tests.test_owner_truth_live_long_memory_pipeline`
  - `tests.test_owner_truth_candidate_extraction_worker`
  - 证据：`green/backend-unit-regression.log`
- run-06 脚本编译及 A3/B1/C1/C2 探针：PASS。
- 模拟器 build-for-testing：PASS；宿主、测试包、xctestrun 均绑定到 manifest。
- 通用 iOS 无签名构建：PASS；产物 SHA-256 `08684c2e1a5f081e3b33bf07a04a20905a8c20ab11e914f5757bdabe085793cf`。
- iOS、后端 `git diff --check`：PASS。
- run-05 已有效且本轮未受产品改动影响的 OwnerTruth `561 PASS + 3 SKIP`、Echo/音频/账号 `95/95 PASS` 保留为复用证据，本轮未虚报为重新执行。

### 429/timeout

Astra 追加的四项 Worker 探针直接纳入：429 两项、ReadTimeout 两项均 `failureCount=0`、`errorCount=0`、`injectedCount=1`。本轮未把它们改写成“原 92 项全部注入”。

## 6. 报告校正

1. `hiddenResponseCount=2`、`normalResponseCount=4` 是累计 GET 响应次数；候选可见负例的实际变化是 logical20 `3→4`、logical65 `16→17`。
2. SQL 改动属于 `read_live_delivery_status`：`s.current_thread_id AS thread_id`，不是正式记忆重建 SQL。
3. authentication retry 接受同场、同版本、active，并依赖 fresh authority 流程；它不直接比较 epoch 单调性。delivery-status 只读核实分支才显式验证 `status.authorityEpoch >= boundReceipt.authorityEpoch`。
4. unknown write 的 logical65 证据是受控服务端保存成功但原 201 未交付，不表述为真实物理断网。

## 7. 指纹

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 后端 HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`
- protected source fingerprint：`791f00bbbd1c1db6b840e563a9b1115f25da6b920c26b2b08f912e00edc3782e`
- Simulator host dylib：`9ee9a8ad850de4b8e90ebf46f57e2d3ce830439b53601a2eacfc822260a9e017`
- test executable：`5a5482759cb416e93e18cd4e08d86848d2d51238460be52065cff2f53b4104a9`
- xctestrun：`3ae6e6077a474a510fc4c511c7861a625844629ae2a0e73f106d771484ff5f5c`
- run-06 server：`34ea70661dc2cee7904944f790676b4d68e95a3253596489ffff51cae2c0459b`
- runner：`099cf8a04e7ec7aedd0a66a2972046d8727fe82a1a1218a95647f52eb04cc664`
- logical20 证据：`4ba8aef660dd474600424931150863f254a4af6ea6faef5ad68e0407b3564c0a`
- logical65 证据：`a2552557b23f32b51f36e07a25a89a4785aa49a41df96b687513f5fba47bc614`

完整清单：`artifacts/run06-artifact-sha256.txt`、`artifacts/source-build-manifest.json`、`artifacts/protected-dependencies.json`。

## 8. 未执行与发布判断

- 真实 Provider：`NOT_RUN`
- 真实 iPhone/SDK 顺序：`NOT_RUN`
- 物理 20 分钟与 65 分钟：`NOT_RUN`
- 部署、生产访问、历史任务处理：`NOT_RUN`
- commit/push：`NOT_RUN`

本轮仅补验收工具和测试断言，没有新增产品业务修改，因此不产生额外部署要求。既有后端/iOS 产品差异仍按其原发布计划处理，不能从本地 PASS 推导为生产已具备能力。

## 9. 后续真机门禁

真机由用户另行主动发起。每个长场前仍先执行真实短场，并完成：待确认候选可见 → 用户审核 → 正式记忆可查 → 重启后仍在且不重复。物理 20 分钟通过后，再执行物理 65 分钟；本报告不将逻辑时钟结果冒充物理时长或真实 Provider 结果。

## 10. 回退

如仅回退本轮有限收尾，可按代码块撤回：

1. `OwnerTruthContractsTests.swift` 的 CAP15 canonical→delivery ledger 记录增强；
2. 后端 PostgreSQL smoke 的 controlled support/relation 校验增强；
3. run-06 四个验收脚本和本目录证据。

不得借回退恢复按位置配对、放宽模型边界、扩大 200 轮输入上限或业务重试预算。产品保存、音频、认证和候选整理实现不在本轮回退范围。
