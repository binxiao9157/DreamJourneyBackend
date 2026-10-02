# DreamJourney Live 候选整理修复本地交付报告

日期：2026-09-19

## 1. 结论

- `LOCAL_PASS`
- `PROVIDER_NOT_RUN`
- `DEVICE_NOT_RUN`
- `DEPLOYMENT_NOT_RUN`
- `HISTORICAL_REPROCESS_NOT_RUN`
- `HISTORICAL_EXACT_TRIGGER_UNRESOLVED`

本轮完成原设计与 9 月 19 日复核清单中可本地执行的全部门禁。没有访问生产、没有部署、没有处理历史 job/Dead Letter、没有审核真实候选、没有 commit/push，也没有检测、连接、安装或等待 iPhone。

后端全量 2619 项存在 7 项 `routeCount` 期望 259、实际 260 的既有基线失败；旧 HEAD 已独立复现，因此不属于本轮新增回归，但后端全量也不写成“全部通过”。本轮候选整理相关定向测试、真实 PostgreSQL 门禁、iOS 回归、模拟器 UIQA 与两种无签名构建均通过。

## 2. 实际确认的缺口与修复

### 2.1 默认生产接线丢失 retry_context

修前，Worker 默认使用外层 `ModelAssistedOwnerTruthSourceExtractor`，恢复上下文只传给直接注入的 Live extractor，导致生产默认链的后续 attempt 没有固定安全反馈。

修复：

- `app/async_effects/owner_truth_candidate_extraction_worker.py`
  - 在 Worker→SourceExtractor→LiveExtractor 委托链显式传递可选 `retry_context`。
  - 同一 job、当前有效 lease、固定安全白名单下才生成恢复提示。
  - 保留 contract→transient 的反馈，但不增加 attempt 资格和预算。

修前证据：`evidence/red/c1-default-worker-retry-context-red.log`。修后相同业务断言进入 131 项定向绿测。

### 2.2 Live 响应和证据索引校验不严格

修前，错误根 envelope、非对象 choice、非字符串 content、`finish_reason=length` 以及 bool 型证据索引可能落入非预期异常或被错误接受。

修复：

- `app/services/deepseek.py`
  - 只在 Live 边界严格验证 envelope、choice/message/content 类型和完成原因。
  - 截断或错误类型转成固定 typed contract failure，不提交候选。
- `app/services/owner_truth_live_memory_support.py`
  - 所有 turn index 明确要求非 bool 整数、范围合法、角色为 user，并保持完整支持复核。
- `app/services/owner_truth_live_memory_contract_errors.py`
  - 固定错误 stage/reason 和恢复资格，不携带原始响应或自由异常文本。

### 2.3 逐 job 诊断被跨任务去重

修前，同类 `(status, reason)` 可能把不同 job 的结果合并，且缺少实际阶段入口。

修复：

- `app/async_effects/owner_truth_candidate_extraction_worker.py`
  - 只对无任务 idle/blocked 心跳去重。
  - 每个 job/attempt 独立记录输入构建、transport、decode、organization、support、proposal、commit 等安全阶段。
  - 诊断接收器失败不改变业务结果。

修前证据：`evidence/red/c3-per-job-stage-diagnostics-red.log`。

### 2.4 Live 范围和持久重试上下文不一致

修前，共享 Worker 的 Live 新分类可能影响非 Live 路径；内存与 PostgreSQL 对失租和上下文资格不一致；仅匹配字符串形状不足以构成白名单。

修复：

- `app/async_effects/owner_truth_candidate_extraction_worker.py`
  - Live 特有 stage/reason 和预算只用于合法 Live source。
  - 非 Live source read/commit 保留既有分类。
- `app/async_effects/lease_repository.py`
  - 内存与 PostgreSQL 都要求当前 lease 有效。
  - 仅 attempt1 的白名单合同失败可创建上下文，连续 transient 可携带；跳号、异 job、失租、非法 reason 均拒绝。

修前证据：`evidence/red/c4-non-live-scope-red.log`、`c4-retry-context-whitelist-red.log`。

### 2.5 真实审核链暴露的两个合同缺口

隔离 PostgreSQL 串起候选到正式记忆后，发现内存测试没有覆盖的真实问题：

- V5 单条审核 route 未把 `expectedMemoryRevision`、`expectedChangeSetId`、`expectedProposalHash` 完整传入 domain command。
- 正式记忆 activation 幂等重放返回已持久的 `created`，应返回 `deduplicated` 且 `memoryVersionCreated=false`。

修复文件：

- `app/main.py`
- `app/domain/owner_truth/interview_candidate_single_review.py`
- `app/services/owner_truth_candidate_review.py`
- `scripts/backend-owner-truth-review-ready-confirmation-handoff-postgres-smoke.py`

没有放宽 revision/hash/Binding/CAS；历史缺少新字段时仍保持原 v1 哈希语义。

### 2.6 iOS 失败场冷启动真实恢复

- `DreamJourneyTests/OwnerTruthContractsTests.swift`
  - 从真实 Capture 失败记录和 Controller 开始。
  - 通过真实 appearance 离页，在 `autoreleasepool` 后确认旧 Controller/Capture 释放。
  - 销毁 Coordinator/Registry/Store，并以同一磁盘目录重建全部组件。
  - 真实 FeatureGate + BackendClient 只发同场 GET；业务 POST 为 0；最终 UI 为 pendingReview。
- `DreamJourneyTests/Fixtures/OwnerTruth/owner-truth-candidate-status-v3-backend-generated.json`
  - 由同版本后端 serializer 脚本生成，iOS typed decode 与 UI 映射消费。
- `Scripts/QA/prd-stitch-ui/run-b6-cold-start-read-recovery-smoke.sh`
  - 跨进程 seed/recover 使用不同 PID、同 workflow hash 和真实 BackendClient 只读适配。

早期 `ios-cold-start-03/04/06` 暴露的是测试未模拟真实页面离开导致旧 Controller 未释放；修正测试生命周期后 `ios-cold-start-07` 以相同业务断言通过，不将其夸大为生产缺陷。

## 3. PostgreSQL 真实门禁

环境：官方 PostgreSQL.app 16，本地 Unix socket `/private/tmp`、端口 55432；每个脚本创建独立临时数据库，schema head `0121`。没有连接生产，也没有以内存仓储替代。

### 3.1 候选→审核→正式记忆→重建读取

脚本：`scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py`

结果：

- 默认 Worker + SourceExtractor + LiveExtractor + 受控真实 HTTP transport。
- 短场两轮补充生成 1 条候选。
- 长场 6 项合成事实（含后半补充与纠正）生成 5 条有效候选。
- 状态/候选 API 后，逐条重新获取 fresh proposal，再走真实 review/activation。
- 形成 6 条正式记忆；重建 Store/TestClient 后列表、详情和 Source 仍可读。
- 命令重放为 deduplicated，不增加正式版本。

证据：`evidence/green/postgres-live-candidate-formal.log`。

### 3.2 持久预算、失租和重领

脚本：`scripts/backend-owner-truth-live-contract-retry-postgres-smoke.py`

结果：attempt1 固定反馈跨 transient 保留；模型返回后 lease 失效时旧 worker 不能提交；重领原 job 后总 Provider 请求数仍为 2；预算耗尽无候选提交。

证据：`evidence/green/postgres-live-retry-budget.log`。

### 3.3 review-ready 交接

账号/Vault 隔离、策略要求、值最小化、过期/脱敏过滤和无副作用读取均通过。

证据：`evidence/green/postgres-review-ready-handoff.log`。

测试 PostgreSQL 实例已正常停止，`postmaster.pid` 已移除。

## 4. 自动化与构建结果

- Backend 定向：131/131，`evidence/green/backend-targeted-unittest.log`。
- Backend 全量：2619 项，7 项既有 route count 基线失败，`evidence/green/backend-full-unittest-memory.log`。
- iOS OwnerTruth：526/526，`evidence/green/ios-ownertruth-full-01.log/.xcresult`。
- Echo/音频/账号：103/103，`evidence/green/ios-echo-audio-account-regression-01.log/.xcresult`。
- iOS 重点冷启动：1/1，`evidence/green/ios-cold-start-07.log/.xcresult`。
- 跨进程模拟器 UIQA：PASS；seed PID 64071，recover PID 64115；status GET=1、业务写=0、不启麦、不新建 capture，`evidence/green/uiqa-b6-cold-start/run-02/`。
- 通用 iOS Simulator 无签名构建：`BUILD SUCCEEDED`。
- 通用 iOS Device 无签名构建：`BUILD SUCCEEDED`。
- `git diff --check`：两端均无错误。
- 安全扫描：交付 `.log/.json/.md` 未匹配 Bearer、API key、client secret 或 private key；诊断关联使用哈希标识。

详细逐项映射见 [BE-IR 执行矩阵](2026-09-19-BE-IR执行矩阵.md)。

## 5. 指纹

### Backend

- HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`
- branch：`main`
- tracked diff SHA-256：`0c3e2405939a68b0cb91d8a63cd087ccf1bcc94fddf4ffd0162dab7ae504b0db`
- status manifest SHA-256：`96bdb8a666ab1b2d91087f00a6a38987568366ebfb7fdbdcc297a66beedbb3de`
- 默认 Worker：`eeea9b895d00775d9ae103c4a12ca0c47ecc87f8b32ce1d12b5fa1d5f3e8b180`
- lease repository：`9c70ed3e8279559564a01d16f0d95704bef1e724d198fbe439a01f04e9d9ff66`
- DeepSeek Live adapter：`01faa71006501a4006bc0ba52c4d3e7f3cd2272d05a505976a9b9ab55cf30669`
- PG formal smoke：`9e909c0768005db5449dae1f1c7b8dac0ce9070d1c519ba13405d6004a6b9f1c`
- PG retry smoke：`ac8c9545ed4385e8550a3f66678344e0bdff1aa0ee815feaacf0b9b92324b0b9`

### iOS

- HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- branch：`feature/prd-stitch-ui-adaptation`
- tracked diff SHA-256：`16c3eaa36474403dde76ae807a15206ec265d6de676beaac88bf15deb9d256f2`
- status manifest SHA-256：`95334b6afd76172def75b7d4eac991ac5f6ff019db840253474a739450d9ae01`
- OwnerTruth tests：`95fd68c90575cfcfad32a4ba94d49586404ecfdc7a705a1fe0bb30de3af2cebe`
- backend-generated fixture：`e3149522865cdee11f7d1bc2c1e179e74b13f7a746b95db9662ab6e8e608e291`
- cross-process UIQA script：`3ab704d2af62eca11383a0168c1b0196a0f9925beaacf4449a9b1635eadeacc6`

注意：tracked diff 不包含未跟踪文件，所以另列关键未跟踪文件哈希。两端工作区在本轮之前已有大量正确未提交修改；本报告不把所有 dirty 文件声明为本轮新增。

## 6. 发布判断、兼容和回退

### 发布判断

本轮没有部署。后续获授权时建议顺序：

1. 先发布后端 Worker/合同校验/审核幂等修复并完成健康检查。
2. 再安装与同版本 status 合同一致的 iOS 包。
3. 执行短场真机闭环；通过后再执行物理 20 分钟长场。

### 兼容边界

- 未改变 Live 音频、原生播放、持续聆听、打断或正式记忆快照绑定。
- 未放宽账号、FeatureGate、CAS、hash、revision、Binding 或未知写保护。
- 未自动确认候选；正式记忆只通过真实用户确认合同生成。
- 非 Live source 保持旧分类。

### 局部回退

如需回退，应按代码块分别回退：

1. Worker 的 `retry_context` 委托、Live-only stage 和 per-job diagnostics。
2. Live adapter 的 envelope/finish reason 和 support index 校验。
3. lease repository 的固定白名单与连续 attempt 校验。
4. V5 route 的 expected binding 传递和 activation replay 状态。
5. iOS 新增测试/fixture/UIQA 脚本。

不得 reset/clean 整个工作区，不得恢复未知业务写重放，也不得删除失败恢复坐标。

## 7. 未运行与残余风险

- 真实 Provider：`NOT_RUN`。受控 HTTP 不能替代供应商实际输出。
- 真机与真实 SDK 顺序：`NOT_RUN`。
- 物理至少 20 分钟长场：`NOT_RUN`。
- 部署和生产数据：`NOT_RUN`。
- 历史失败重处理：`NOT_RUN`；两场历史精确模型输出不可还原。
- 已知长输入限制：organization 可分块，但超长整场 support 仍可能因长度限制失败；未在本轮暗改架构或阈值。
- 7 项 routeCount 基线仍待独立任务校正测试清单，不影响本轮业务结论，但不能称后端全量全绿。

后续步骤仅见 [真机复测清单](2026-09-19-真机复测清单.md)。本地任务至此结束，不等待外部验证。
