# DreamJourney Live 长对话分批整理 run-04 本地交付报告

日期：2026-09-20\
交付目录：`/Users/gaominge/Documents/liftora/outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-04/`

## 1. 状态

`LOCAL_PASS / READY_FOR_LIVE_DEVICE_RETEST`

独立边界：`PROVIDER_NOT_RUN / DEVICE_20M_NOT_RUN / DEVICE_65M_NOT_RUN / DEPLOY_NOT_RUN / HISTORICAL_REPROCESS_NOT_RUN`

本结论只覆盖当前源码的本地受控 HTTP、隔离 PostgreSQL、Simulator 测试和无签名构建。没有连接或等待 iPhone，没有真实 Provider 调用，没有部署、生产访问、历史处理、commit 或 push。

## 2. 已确认根因

run-03 剩余的 `R02-B-PARA` 来自证据身份与生成表述耦合：`_bind_support_proof` 以前用整理后的 claim 在原话中做字面查找；合法同义改写找不到时回退到整段原文。分页复核随后把整段中的页外事实错误当成本页责任，产生 `factOmitted`，整场不发布候选。

这不是保存链、音频或 iOS 观察问题。本轮没有重写正文持久化、end/ACK/admit、B7 或未知写保护。

## 3. 局部修改

### 不可变证据身份

- `app/services/owner_truth_live_memory_support.py`
  - 新增 `build_live_memory_evidence_catalog`，由原始 user turn、位置、范围、文本 hash 生成稳定片段 ID。
  - `validate_live_memory_support` 支持明确的 `responsibility_atom_ids`；分页只审本页 atom，页外上下文不再变成本页遗漏责任。
- `app/services/deepseek.py`
  - 组织请求携带不可变证据目录；响应必须返回 `evidenceFragmentIds`。
  - parser 校验引用存在、属于声明的 user turn、无重复，并保存为内部 `_sourceEvidenceFragmentIds`。
  - support 请求加入 `responsibilityAtomIds`/`omittedOwnedAtomIds` 合同，实际生产 adapter 声明支持 atom 责任范围。
- `app/async_effects/owner_truth_candidate_extraction_worker.py`
  - `_bind_support_proof` 只接受可信证据 ID，并绑定回全局原文范围。
  - 非逐字表达缺引用时 typed 失败；重复原句仅靠字面匹配而位置不唯一时 typed 失败；删除整段回退。
  - 最终分页 support 仅向支持新合同的 reviewer 传递该页 atom 责任集合；全场 manifest/atom disposition 继续负责完整性。

### 验证和压力链

- `tests/test_owner_truth_live_long_memory_pipeline.py`
  - 增加真实 DeepSeek 请求构造/parser + MockTransport 的正常释义、合法补充、分页、重复位置、同义/语序、无效引用保持性测试。
- `scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py`
  - 受控 adapter 经过真实 request builder/parser 并返回不可变证据引用与 atom 责任。
  - 同一个 F-65 Source 现贯穿 HTTP admission、默认 Worker、149 条候选、逐项审核、正式记忆和 Store 重建，不再把大 Source admission 与另一组 41 条正式记忆混称为同链。

iOS 本轮没有源码修改。

## 4. 先红后绿

| 场景 | 修前 | 修后 | 证据 |
|---|---|---|---|
| 第五探针：12 atom，正常释义，合法补充后应为 11 候选 | `factOmitted`，Run 留在 organizing | 11 候选，readyToPublish | `evidence/R02-B-PARA-red.log`、`R02-B-PARA-green.log` |
| 前四项既有正向探针 | PASS | PASS | 同上 green log |
| 同义/语序变化共享证据 | 未覆盖 | PASS | `backend-live-long-memory-tests.log` |
| 重复原句不同位置 | 未覆盖 | PASS，ID 不同 | 同上 |
| 非逐字表达缺引用/无效引用 | 未覆盖 | typed 拒绝 | 同上 |
| 无证据新增、错误人物/时间/地点、缺 atom、伪替代/撤回 | 既有保护 | 保持拒绝 | `backend-live-long-memory-tests.log`、`backend-candidate-worker-tests.log` |

红、绿日志 SHA-256 分别为 `6c9dfd...d06d` 与 `8274a3...fad3`，完整值见证据目录。

## 5. 本地执行结果

### Backend

- 长场 pipeline：26/26 PASS。
- Candidate Worker：64/64 PASS。
- 审核/B7 受影响回归：54/54 PASS。
- Realtime/音频保持性：23/23 PASS。
- DeepSeek adapter 定向解析：3/3 PASS。
- 修改文件 `py_compile`：PASS，使用独立 `/tmp` pycache。

曾尝试的宽泛 Core/API 集合分别出现 41 和 8 个 `PoolClosed`，原因是测试进程未启动全局 `dreamjourney-api` pool；保留在 `backend-core-services-tests.log`、`backend-deepseek-adapter-tests.log`，不计业务红测。对应本轮合同由定向 adapter 测试和三条真实隔离 PostgreSQL 门禁覆盖。

### 隔离 PostgreSQL 16

- 正式链：PASS。schema `0122`；F-65 为 73,728 字、301 turn、150 user turn；149 条候选全部审核进入正式记忆。
- 汇总：短场 1 + 长场 40 + F-65 149 = 190 条正式记忆；Store 重建后列表/详情可读，command replay 去重。
- F-65 末尾纠正保留，旧首值不发布；候选数超过 32。
- 并发 lease、authority epoch、Provider 等待不持事务、manifest 原子失败、commit 中途回滚、failed Run 不再调用模型：PASS。
- 同 job transient 恢复、持久预算耗尽、迟到 lease、后续合法 transient 历史和非法历史拒绝：PASS。

证据：`postgres-formal-memory-chain-final.log`、`postgres-concurrency-rollback.log`、`postgres-contract-retry.log`。

### iOS / 构建

- OwnerTruth：532/532 PASS。
- 音频 owner lease：5/5 PASS。
- 长回答、主动打断、迟到回复、恢复聆听、PCM：25/25 PASS。
- Simulator 无签名构建：PASS。
- generic iOS 无签名构建：PASS。
- 已补充 simulator/generic 的 `DreamJourney.debug.dylib`、启动 executable 和测试 bundle 指纹，见 `evidence/build-artifact-fingerprints.sha256`。

## 6. F-65 与流量证据校正

F-65 当前证明的是同一个 Source 的完整本地组合链，而不是仅 admission。受控 HTTP 使用真实 adapter 请求构造和 parser；确定性模型响应仍是合成真值，不等同真实 Provider 质量。

3900 秒 relay 只证明指定假设下的容量：双向各 16 kHz、16-bit mono、50 帧/秒、每帧 640 PCM 字节加 16 字节合成 envelope；payload 255,840,000 字节，合成 WebSocket wire 估算 258,180,000 字节，小于 1 GiB。它不是 SDK 真实流量精确测量。完整公式见 `evidence/relay-load-assumptions.md`。

## 7. 差异、日志和指纹

- Backend HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`。
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`。
- 两端 `git diff --check`：PASS；全部前序未提交修改保留。
- 源码：`evidence/source-fingerprints.sha256`。
- 构建：`evidence/build-artifact-fingerprints.sha256`。
- 关键证据：`postgres-formal-memory-chain-final.log` SHA-256 `f5aff2...1345`；OwnerTruth 结果 `08f1ee...9939`。
- 日志扫描无 bearer、token、API key、password 或 secret 值，见 `evidence/log-redaction-scan.md`。

## 8. 发布、兼容与回退

本轮未发布。未来发布顺序仍为：备份并应用 migration `0122` → Backend API → Candidate Worker → 受控开启长场 profile → iOS 累计版本。R02-B-PARA 的新组织/support 合同要求 API 与 Worker 同版本发布；旧响应缺少证据引用会明确失败，不降级成整段 proof。

局部回退仅回退 evidence catalog、证据引用 parser、atom-scoped support 和相应 Worker 绑定代码块。回退时必须保留已有 Run/Source/manifest、持久预算、未知写不重放和正式记忆只读能力；migration `0122` 不做破坏性 down。

## 9. 残余风险和停止点

- 真实 Provider 的长上下文质量、响应分布、限流和成本：NOT_RUN。
- 物理 20 分钟、密集 20 分钟、物理至少 65 分钟：NOT_RUN。
- 部署后 Worker/API 版本组合与生产 migration：NOT_RUN。
- 历史失败任务、候选和 Dead Letter：未访问、未清理、未重放。

后续真机步骤见 `reports/2026-09-20-后续真机验收清单-run04.md`。本次到本地交付为止。
