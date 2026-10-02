# DreamJourney Live 记忆候选整理失败本地修复报告

日期：2026-09-19\
范围：Live 正文/end/ACK/admit 成功后，后台候选整理首次失败的真实整理链、持久预算内恢复，以及失败场恢复坐标保留。\
总状态：`A_LOCAL_INCOMPLETE / NOT_READY_FOR_DEVICE_RETEST`

## 1. 基线与边界

### iOS

- 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- 分支：`feature/prd-stitch-ui-adaptation`
- HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 工作区在本轮开始前已有大量未提交修改；本轮没有 reset/clean、没有整文件覆盖。

### 后端

- 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`
- 分支：`main`
- HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`
- `tests/test_owner_truth_interview_input_api.py` 的既有修改未纳入本轮实现，也未回退。

### 未越界事项

- 未连接、检测、安装或操作真实 iPhone。
- 未部署，未访问生产，未处理历史任务或 Dead Letter，未审核真实候选。
- 未 commit/push。
- 未修改 Live 音频、B7 语义边界、同步封存或认证恢复规则。

## 2. 实际定位

修前真实 HTTP 适配器遇到 organization/support 合同输出异常时，失败主要以普通 `ValueError` 或通用 provider error 汇总：

1. 无法稳定定位 organizationInput、organizationRequest、organizationDecode、organizationValidate、supportInput、supportRequest、supportDecode、supportValidate、proposalBuild、candidateCommit 的真实失败层。
2. Worker 不能仅凭持久化 attempt 判断“原 job 第一次、允许一次合同修复”，也无法安全地把固定修复提示贯穿到下一次 organization/support。
3. 失败后的 iOS terminalFailure/quarantined 会沿成功清理路径丢失 outbox/follow-up/checkpoint，导致同一批次无法在重建后继续只读核实。

历史现场究竟是哪一个模型输出字段触发失败仍无原始检查点证据，保持 `UNKNOWN`；本报告不把源码缺口冒充该次现场唯一根因。

## 3. 代码修改

### D1-D3 后端真实整理链与持久预算恢复

1. `app/services/owner_truth_live_memory_contract_errors.py`
   - 新增 `LiveMemoryContractFailure`，固定 stage/reason/category/eligibility。
   - 新增 `LiveMemoryContractRetryContext`，只允许固定安全 repair hint。

2. `app/services/deepseek.py`
   - `DeepSeekLiveMemoryOrganizationProxy` 支持受控 `httpx.BaseTransport`，测试与生产走相同 decode/validation 路径。
   - organization/support 分别分类输入、配置、HTTP、timeout、transport、envelope、content、JSON、schema 失败。
   - 不在适配器内部循环重试；修复提示不含正文、响应或原始业务标识。

3. `app/services/owner_truth_live_memory_support.py`
   - 将 schemaInvalid、coverageIncomplete、evidenceInvalid、evidenceOutOfRange、semanticUncertain、factOmitted、factWithoutFinalDraft 转为 typed failure。
   - 保留既有 B7 用户事实支持、纯问题与助手答案排除规则。

4. `app/async_effects/lease_repository.py`
   - InMemory 与 PostgreSQL repository 均增加 `load_contract_retry_context`。
   - 只接受当前租约、同一 job、前一 attempt 为 retryableFailed 且 failure 在白名单的上下文。

5. `app/async_effects/owner_truth_candidate_extraction_worker.py`
   - typed failure 优先分类，旧的非 Live 兼容分类仍保留。
   - attempt 超过原 intent `maxAttempts` 时在模型调用前停止。
   - 仅 attempt 1 的 eligible contract failure 可消费原持久预算；不创建新 job，不提高预算。
   - 后续 attempt 重新执行 organization 与 support；support 失败不会复用旧 support 结果。
   - candidate commit 失败也进入固定阶段诊断，不吞掉租约控制流异常。

### D4 iOS 失败场恢复坐标

`DreamJourney/Sources/Modules/Echo/EchoViewController.swift` 的 `finishSameSessionObservation`：

- `pendingReview`、`empty`：继续按成功终态清理。
- `terminalFailure`、`quarantined`：仅结束当前观察轮次，保留 outbox、follow-up、checkpoint 与 terminal observation。
- observation 写盘失败：失败/隔离仍保留原坐标，不伪称完成，也不把它当延迟成功清理。

## 4. 红绿证据

### 修前失败

- `evidence/red/backend-live-http-contract-retry.txt`
- 同一业务断言表明：真实 HTTP adapter 的合同错误不能按原 job、持久预算完成一次修复并生成候选。
- iOS 同断言修前会清掉 terminal failure 的恢复坐标；原始结果包保留于 Xcode DerivedData，绿测结果另存到本目录。

### 修后通过

- 后端 Worker + lease：`65/65 PASS`\
  `evidence/green/backend-worker-lease-65-tests.log`
- 后端审核/正式记忆相关：`179/179 PASS`\
  `evidence/green/backend-review-formal-179-tests.log`
- iOS D4 组合：`6/6 PASS`\
  `evidence/green/ios-d4-combination-tests.xcresult`
- iOS 真实生产装配组合：`1/1 PASS`\
  `evidence/green/ios-real-composition-test.xcresult`
- iOS OwnerTruth 全量：`524/524 PASS`\
  `evidence/green/ios-ownertruth-full-current.xcresult`

真实装配组合覆盖 Controller、磁盘 FollowUpStore、真实 FeatureGate evaluator、BackendClient/requestJSON 和受控 URLProtocol；断言只有候选状态 GET，没有 start/end/ACK/admit POST。

## 5. 短场与长场本地闭环

`test_live_short_and_long_http_candidates_confirm_into_formal_memory` 使用真实 HTTP adapter 与 Worker：

- 短场：2 个用户 turn + 1 个助手 turn。
- 长场：16 个用户 turn + 15 个助手 turn。
- 两场均从真实 builder 生成候选，而非假 organizer 或复制 fixture。
- 每个候选先取得真实 ChangeSet preview，再绑定 revision/changeSet/proposal hash 后调用审核服务。
- 审核后通过正式记忆 list/detail 读取；重建 service 后仍可读取。
- 同一审核命令幂等重放不增加正式记忆条数。

该证据使用内存持久层，不等价于隔离 PostgreSQL 或真机。

## 6. 回归与构建

| 项目 | 结果 | 证据 |
|---|---|---|
| iOS OwnerTruth 全量 | PASS，524/524 | `ios-ownertruth-full-current.xcresult` |
| iOS AudioOwnerLeaseModelTests | PASS，16/16 | Xcode DerivedData 对应结果包 |
| iOS 账号相关所选回归 | PASS，16/16 | Xcode DerivedData 对应结果包 |
| iOS 模拟器构建 | PASS | `ios-simulator-build.xcresult` |
| iOS 通用设备无签名构建 | PASS | `ios-generic-build.xcresult` |
| 后端定向回归 | PASS，65 + 179 | 两份 `.log` |
| 后端全量 | FAIL，2598/2605 | `backend-full-memory.log` |
| 两端 `git diff --check` | PASS | 2026-09-19 本轮终检 |

后端全量的 7 项失败全部是同一既有清单漂移：运行时路由数为 260，而测试/烟测仍固定为 259。当前本轮修改没有增加路由，也未改 `app/main.py`、route authentication 或 ownership registry，因此未通过改期望值掩盖该基线问题。

## 7. PostgreSQL 与外部验证

### 隔离 PostgreSQL

状态：`BLOCKED`

本机没有 `docker`、`podman`、`colima`、`pg_isready`、`psql`、`postgres` 或 `brew`；项目环境中的 `postgres:5432` 是 Compose 内部地址，当前没有可连接运行时。未使用生产数据库，也未以内存仓储替代 PostgreSQL 事务、并发、回滚或重启验收。

### 真实 Provider

状态：`NOT_RUN`。没有向外部模型发送合成或私人文本。

### 真实 iPhone

状态：`NOT_RUN`。本轮遵守指令未检测、等待、安装或操作手机。

## 8. 源码与构建指纹

### 后端关键文件 SHA-256

- `owner_truth_live_memory_contract_errors.py`: `440771640be6eb14029d6cda57c36bc1e2f8d4a76466a805a50177178eccbb2d`
- `deepseek.py`: `50dde8083a40377d40d3d55ee10e5d76bb11847f6b09e21feaa77d65ff36a507`
- `owner_truth_live_memory_support.py`: `45b71e2330a54c46bb736cba066058dafb77fe56a6ca27172f4fb04954e971d0`
- `lease_repository.py`: `b00b580739e3e3ca26451f05cde532494234dfc98e4f4df1a5b02ad887c6a196`
- `owner_truth_candidate_extraction_worker.py`: `ed9a268b973bda08743c9dce8a9ae8555b6140130fcab998c286434fb28ed239`

### iOS 关键文件与构建 SHA-256

- `EchoViewController.swift`: `9fe57640b83da4d92ffa8a5cfa36bdded1739ca00f6f7447084122f8e64abd8e`
- `OwnerTruthContractsTests.swift`: `78023baf102ef5a7cd81810da5b5b936ac2d66a089bc12379e8895aa561d6326`
- simulator app executable: `e4317d30dc68e93fe869fe215f89043123515344b398a456898db7e8cdb7b242`
- generic iphoneos app executable: `08684c2e1a5f081e3b33bf07a04a20905a8c20ab11e914f5757bdabe085793cf`

## 9. 发布影响与回退

### 发布影响

- 后端需先发布 typed contract failure、lease retry context 与 Worker 有界修复能力。
- 后端无新增路由、无数据库迁移；但正式发布前仍须补隔离 PostgreSQL 门禁。
- iOS D4 坐标保留需要随 App 发布；后端未就绪时不能用 iOS 状态变化宣称整理恢复完成。

### 局部回退

1. 后端可按代码块回退 typed error 模块、DeepSeek 分类、repository context reader 和 Worker 的 bounded contract retry。
2. iOS 仅回退 `finishSameSessionObservation` 的 terminalFailure/quarantined 保留分支。
3. 回退不得恢复未知写重放、删除失败恢复记录或改变 B7/音频/认证边界。

## 10. 后续最小真机清单

真机前置条件：隔离 PostgreSQL 门禁通过、后端全量路由清单问题被独立确认或修复、后端先发布、再安装对应 iOS 版本。

### 短对话

1. 在 Live 说一条全新唯一合成事实，正常结束。
2. 待确认记忆中应恰好出现 1 条对应候选，正文、来源和预览完整，无重复。
3. 由用户本人点击确认；候选消失，正式记忆可按唯一标记搜索到。
4. 彻底关闭并重启 App；正式记忆仍存在且仅 1 条，候选不回流。
5. 文字问答正确回查且不朗读；新 Live 正确回查且有声音。

### 物理 20 分钟长对话

1. 使用全新的开场、中段、结束三个唯一标记，自然对话满 20 分钟；记录三个说出时间点。
2. 正常结束后，待确认记忆必须覆盖三个标记，不缺失、不跨场、不重复。
3. 逐条打开最新预览，由用户本人依次确认；每条必须基于确认时最新正式记忆 revision。
4. 正式记忆搜索三个标记，核对内容、来源、版本和投影结果。
5. 彻底关闭并重启 App；三个正式记忆仍在且各 1 条，候选不回流，也不自动重放历史失败任务。

任何阶段若出现整理失败、结果未知、重复或版本错配，立即停止依赖该结果的后续确认，保留时间、脱敏 trace/attempt、页面状态与服务端只读证据。
