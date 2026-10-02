# DreamJourney Live 候选整理 run-02 收尾本地交付报告

日期：2026-09-19

## 结论

本轮仅完成 run-02 放行复核第 3 至第 5 节的剩余项。默认生产接线、隔离数据库正常正式记忆链和 failed 冷启动链未重做、未回退。

交付状态：

- `LOCAL_PASS`
- `PROVIDER_NOT_RUN`
- `DEVICE_NOT_RUN`
- `DEPLOY_NOT_RUN`
- `HISTORICAL_EXACT_TRIGGER_UNRESOLVED`

本地通过不关闭现场问题，也不代表生产已部署。

## 基线与范围

- Backend HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 两端均保留已有未提交修改；未执行 reset、clean、commit 或 push。
- run-02 已通过主链作为基线。本轮没有重新解释或替换 run-02 的短场、长场、正式记忆及 failed 冷启动证据。

## 实际修复

### 1. 默认阶段日志与语义时点

文件：

- `app/async_effects/owner_truth_candidate_extraction_worker.py`
- `app/services/deepseek.py`

行为变化：

- 默认阶段 sink 直接向 stderr 输出单行安全 JSON，不依赖宿主另行配置 logger 才可观察。
- `supportValidated` 从“JSON 已解析”之后移到真实 `validate_live_memory_support` 成功之后。
- 日志只输出阶段、随机 correlation、attempt、状态码和计数，不输出会话正文、token、密钥、原始身份或业务 hash。

修前反例证明默认 sink 静默，并且不合法的支持结果也会提前发出 `supportValidated`。相同断言修后通过。

### 2. `finish_reason` 类型合同

文件：`app/services/deepseek.py`

行为变化：

- Provider envelope 的 `finish_reason` 只允许 `null` 或字符串。
- 数组、对象及其他类型返回 typed `finishReasonTypeInvalid`，不再落入缺失字段或普通结构异常。
- 第一次合同失败仍遵守原恢复预算，未放宽候选语义、引用或权限检查。

### 3. 后续合法 transient 历史

文件：`app/async_effects/lease_repository.py`

行为变化：

- attempt 2 及以后只允许 organization/support 请求阶段的 `timeout`、`transport`、`rateLimited`、`httpTransient` 作为连续瞬态历史。
- 任意合同、授权、语义或未知错误不能伪装成 transient 来领取额外尝试。
- 仍使用原 job、原正文、原场次和持久预算，不创建替代 job。

### 4. PostgreSQL 并发与事务故障门禁

新增 QA：

- `scripts/backend-owner-truth-live-candidate-concurrency-postgres-smoke.py`
- 扩展 `scripts/backend-owner-truth-live-contract-retry-postgres-smoke.py`

隔离 PostgreSQL 16.15、临时数据库、schema head `0121` 下验证：

- 两个独立 Worker/store 竞争同一 job，仅一个取得活动 lease。
- Provider 等待期间数据库连接没有保持 idle-in-transaction。
- 真实 consume 后、candidate commit 中途注入事务故障，extraction/candidate/inbox/completion receipt 全部回滚；operationAccepted 保留，job 进入 retryWait。
- 同一原 job 的 attempt 2 恢复成功且只产生一份候选。
- Provider 等待期间 authority epoch 变化后禁止提交候选。
- attempt 2 的 repeated contract、authorizationRejected 和 arbitrary history 均被拒绝，attempt 3 无新增 context。

所有临时数据库均由脚本创建和删除；本地 PostgreSQL 已停止并卸载，没有访问生产数据库。

### 5. IR-02、IR-05、IR-07

文件：

- `DreamJourneyTests/OwnerTruthContractsTests.swift`
- `DreamJourneyTests/Fixtures/OwnerTruth/owner-truth-candidate-status-v3-backend-generated.json`
- `scripts/generate-owner-truth-candidate-status-ios-fixtures.py`

IR-02：真实 Capture Controller/Coordinator、临时磁盘与故障 FileManager 组合覆盖 failed/quarantined。observation 落盘失败时保留 outbox、follow-up、checkpoint，且恢复业务写为 0。

IR-05：通过真实同场 Capture 清理路径覆盖 pendingReview/empty，并在清理第 1、2、3 步分别注入失败。已确定终态不倒退；下一次合法同场读取重试清理，保持幂等且不产生业务写。

IR-07：后端生成器经过真实 FastAPI status route 和 serializer 生成 v3 字节，iOS 通过 URLProtocol、BackendClient、真实 FeatureGate evaluator、RecoveryService/Coordinator 和 EchoViewController 消费。queued、retryWaiting、failed、reviewReady 每种只发出一次只读 GET。

生成器只在自己的测试进程内替换合成上下文与读取服务；它证明路由序列化与 iOS 解码/状态提交兼容，不冒充真实生产数据库调用。

## 中间方案校正

本轮曾尝试在冷启动 RecoveryService 的 pendingReview/empty 路径直接清理坐标。OwnerTruth 全量发现该方案会破坏 B6 完成观察、批次冲突识别和 failed 冷启动坐标保护，因此已完整撤销。

最终 iOS 运行时代码不包含这项变更。IR-05 被放回真实同场 Capture 收尾路径验证。相关红测保留在 `ios-ownertruth-ir05-regression-red.log`，但该中间方案不是最终交付实现。

## 红绿证据

### 真实业务红

- `evidence/red/backend-section3-red.log`
  - 默认阶段 sink 无输出。
  - `supportValidated` 在语义校验前发出。
  - 非法 `finish_reason` 类型未得到精确分类。
  - 任意后续失败历史仍可领取尝试。
- `evidence/red/ios-ir02-ir05-ir07-red-host-02.log`
  - 暴露 IR-05 同场终态清理失败后缺少可验证的幂等恢复行为。
- `evidence/red/ios-ownertruth-ir05-regression-red.log`
  - 证明错误放置的冷启动清理会破坏 B6，促使撤销非目标生产改动。

IR-02 的既有生产保护修前已通过；IR-07 是缺少跨端真实响应覆盖，并非已确认的运行时业务失败。没有为凑红测人为改坏预期。

### 非业务红

system Python 缺依赖、沙箱本地网络限制、错误本地 PG role、UUID 大小写夹具、缺少 simulator 以及 PG 脚本断言校正均单独保存在 `evidence/red/`，未计作业务缺陷证据。

### 最终绿色结果

- Backend 第 3 节定向：8/8。
- Backend 受影响回归：111/111。
- PostgreSQL 并发/事务：PASS。
- PostgreSQL 后续 transient 历史：PASS。
- iOS IR-02/05/07 + B6 定向：6/6。
- iOS OwnerTruth 全量：529/529。
- iOS 音频/账号保持性：5/5。
- iOS 通用设备无签名构建：成功。
- 模拟器编译：由最终 XCTest 构建并执行成功证明。
- 两端 `git diff --check`：通过。

Backend 全仓测试本轮未重跑。run-02 的全仓结果仍是 2619 项执行、7 项既有 routeCount 基线失败；不得将本轮 111/111 写成全仓全绿。run-02 UIQA 本轮未重跑，保留原证据但不计作 run-03 新执行项。

## 源码与构建指纹

完整清单：

- `evidence/green/source-fingerprints.txt`
- `evidence/green/build-fingerprints.txt`
- `evidence/green/backend-status.txt`
- `evidence/green/ios-status.txt`
- `evidence/green/backend-diff-stat.txt`
- `evidence/green/ios-diff-stat.txt`

跨端 fixture 的后端输出与 iOS 输入 SHA-256 一致，见 `evidence/green/cross-end-fixture-sha256.txt`。

## 部署与兼容判断

- 后端生产代码有局部变化，未来发布需随 Backend 部署。
- iOS 本轮最终只保留测试和夹具增量；没有新的 run-03 运行时行为需要单独安装，但整体待测版本仍包含前序未发布修改。
- 无数据库迁移，无公共 API 版本变化。
- 未部署后端、未安装 iPhone、未访问生产数据。

## 回退方案

- 默认日志：仅回退 `_log_stage_diagnostic` 的 stderr 输出块，不移动其他 Worker 接线。
- 语义时点与 `finish_reason`：分别回退 `deepseek.py` 的事件位置和类型分支，不能删除既有候选语义校验。
- transient 历史：只回退 `lease_repository.py` 的精确 allowlist 代码块，不扩大为接受任意失败。
- QA：测试、PG smoke、fixture generator 可独立回退，不影响生产 schema 或数据。
- 不允许用回退恢复未知写重放、放宽权限或删除恢复坐标。

## 残余风险与后续

- 真实 Provider：`NOT_RUN`。
- 真机短场与物理 20 分钟长场：`DEVICE_NOT_RUN`，由用户后续主动发起。
- 后端/iOS 部署：`NOT_RUN`。
- 历史失败现场缺少完整 Provider 原始输出及精确检查点，实际第一次失败原因继续为 `HISTORICAL_EXACT_TRIGGER_UNRESOLVED`。
- 未处理、未重放任何历史任务或 Dead Letter。

真机清单沿用 run-02 原标准：短场两轮含补充；长场物理 20 分钟至少五项首中尾事实并包含后半补充和纠正；两类均验证待确认候选可见、用户确认、正式记忆可查、冷启动仍在且不重复。该清单只是后续准备，不表示本轮已开始真机测试。
