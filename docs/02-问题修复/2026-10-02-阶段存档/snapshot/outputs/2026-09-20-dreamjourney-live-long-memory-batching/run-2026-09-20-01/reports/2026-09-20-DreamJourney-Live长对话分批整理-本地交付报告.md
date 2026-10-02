# DreamJourney Live 长对话分批整理与会后统一发布本地交付报告

日期：2026-09-20\
交付目录：`/Users/gaominge/Documents/liftora/outputs/2026-09-20-dreamjourney-live-long-memory-batching/run-2026-09-20-01/`

## 1. 最终状态

`LOCAL_PASS / PROVIDER_NOT_RUN / DEVICE_20M_NOT_RUN / DEVICE_65M_NOT_RUN / DEPLOY_NOT_RUN / HISTORICAL_REPROCESS_NOT_RUN`

本地实现、受控网络、隔离 PostgreSQL、模拟器 UIKit/UIQA、相关回归和两种无签名构建已经完成。真实 Provider、真实 20 分钟和 65 分钟以上 Live、部署及历史重处理仍未执行，不能由本地结果替代。

## 2. 基线与范围

- 后端：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`
- 后端基线 HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`，工作树原本已有未提交修改。
- iOS：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- iOS 基线：`feature/prd-stitch-ui-adaptation`，HEAD `11d0d0051b9be3cce57822dd059472d1e2536866`，工作树原本已有未提交修改。
- 未执行 reset/clean，未覆盖或回退 B6/B7/B8、同步封存、认证恢复、音频和此前保存链成果。
- 本报告的文件清单是本功能涉及或依赖的差异，不声称当前脏工作树全部差异都由本轮产生。

## 3. 已实现行为

### 3.1 场次和持久状态

- 新增版本化 Live 长记忆 Run、WorkUnit、ProviderAttempt、Atom、Relation、Manifest 模型及内存/PostgreSQL 仓储。
- Run 绑定 owner、vault、productSession、captureGeneration、pipelineVersion 和 authority epoch；旧身份或旧 lease 不能写入新场。
- 原文仍沿既有 canonical transcript/Source 进入同一场次，新增处理层不替代原文持久化。
- 新 pipeline 默认关闭；新 Run 在创建时固定版本，旧 Run 不被切换到另一条处理链。

### 3.2 分批整理和关系处理

- 会话进行中按有界 WorkUnit 私密预整理；Source 未封定前，用户可见候选始终为 0。
- 支持普通批、超长单 turn 拆分、尾部不足一批和 partial 尾部；父子覆盖关系防止同一原文重复发布。
- 关系核对采用最多 8 个 incoming × 32 个 existing 的分页批合同；每个 incoming 必须有精确扫描回执和至多一个关系结论。
- 同场 duplicate、supplement、correction、retraction 在冻结 manifest 前统一处理；无法确认的关系保持 unresolved 并阻止发布。
- 300 个独立事实场景产生 228 次关系批请求，Provider 总调用 304，未超过全场 512 次预算；不存在末尾全量关系请求。

### 3.3 会后冻结和原子发布

- 既有 end → ACK → admit 继续负责正式 Source 封定；新链补齐尾部后校验 atom 覆盖、支持证据、关系闭合和 Source 绑定。
- 只有完整、可追溯且 authority 仍合法的 manifest 才能原子发布候选。
- 中途事务错误整批回滚；重试复用原 Source、job、manifest 和稳定候选 ID，不增加 Provider 请求。
- 候选仍使用既有审核流程；确认后创建正式 Memory/Version，拒绝不写正式记忆，重复命令幂等。

### 3.4 预算、并发和恢复

- 全场上限：2048 WorkUnit、512 Provider 调用、4M 输入估算、2M 输出估算、32 次共享恢复；每单元仅一份额外尝试。
- 单 Run 最多 2 个 Provider unit 同时持有 lease；隔离 PostgreSQL 已验证前两份获租约、第三份被拒绝。
- job 等待 Run 时不预占正式 extraction lease、不消耗失败 attempt、不回退旧全量单次路径。
- 旧 lease、authority epoch 变化、账号变化及迟到响应不能提交候选。
- ProviderAttempt 的“可能已调用”不等同业务写成功；Live start/append/end/ACK/admit 的未知写保护保持不变。

### 3.5 Live 专用容量和 iOS 观察

- Live Source 采用服务端专用容量合同：100000 用户字符、200000 总字符、2 MiB 序列化、单 turn 20000 字符；普通 Source 的 20000 字符限制不变。
- 客户端 metadata 不能自行启用大容量合同。
- 长 Live profile 由服务端选择并固定到 lease：7200 秒、1 GiB；普通 profile 不变，7201 秒和超过总流量时仍准确拒绝。
- iOS 同场只读状态观察采用 1/2/4/8 秒后 10 秒有界间隔，最多 24 次 GET，整体 180 秒；到期保留恢复坐标，不误报后台失败，后续新鲜读取可恢复。

## 4. 主要修改文件和函数

### 4.1 后端

- `app/services/owner_truth_live_long_memory.py`
  - 长场预算、稳定身份、WorkUnit 规划、租约、ProviderAttempt、Atom/Relation/Manifest、内存与 PostgreSQL 仓储适配。
- `db/migrations/0122_owner_truth_live_long_memory_pipeline.sql`
- `db/migrations/0122_owner_truth_live_long_memory_pipeline.json`
  - 新增私有持久表、约束、索引和迁移清单；未覆盖已有 `0120/0121`。
- `app/async_effects/owner_truth_candidate_extraction_worker.py`
  - 默认生产装配、Run ready 门禁、私密预整理、冻结 manifest 发布、旧 job 兼容和 typed 失败。
- `app/async_effects/lease_repository.py`
  - 正式 extraction job 对 Run ready/failed 的有界等待和唤醒。
- `app/services/deepseek.py`
  - 有界组织、支持和 relation batch HTTP 合同；严格 typed decode、finish reason 和安全阶段诊断。
- `app/main.py`
  - Live append 注册增量 segment，end 封定尾部；admission 绑定 Live Run/Source；容量 profile 由服务端选择。
- `app/services/in_memory_store.py`、`app/services/postgres_store.py`
  - 新 Run/Unit/预算/关系/manifest 的仓储接线。
- `app/services/realtime_voice_proxy.py`、`app/core/config.py`、`.env.example`
  - 服务端长 Live profile、7200 秒/1 GiB 配置及默认关闭的新 pipeline 开关。
- `app/domain/owner_truth/source_commands.py`
  - Live 专用 Source 容量，普通 Source 合同保持不变。
- `app/services/owner_truth_interview_candidate_proposal.py`、`app/domain/owner_truth/interview_candidate_single_review.py`、`app/services/owner_truth_candidate_review.py`
  - 新 manifest 候选继续使用原审核、CAS、Binding、哈希和正式记忆事务。

### 4.2 iOS

- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
  - 同场后台整理 180 秒/24 GET 有界观察、旧 poll/旧 round 隔离和恢复坐标保留。
- `DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`
  - typed 状态读取及现有 FeatureGate/account lease 接线。
- `DreamJourney/Sources/Modules/Echo/EchoViewController.swift`
  - 同场状态展示和冷启动只读恢复沿用既有安全边界。
- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthFormalMemory.swift`
- `DreamJourney/Sources/Modules/Archive/OwnerTruthFormalMemoryViewControllers.swift`
  - 候选确认后正式记忆列表、详情和版本回查保持性。

## 5. 红绿证据

实现前用于定位的反例包括：旧 8 条输出上限导致 12 项事实无法完整通过；长场关系核对在逐项扫描下超过全场调用预算；旧 iOS 6 次状态读取无法覆盖较慢后台整理；Live 大正文误用普通 Source 容量；最初迁移编号与现有迁移碰撞。实现中使用相同业务断言改为有界分批、关系批处理、180 秒观察、服务端专用容量和迁移 `0122`，现均通过。

证据边界：没有为每个修前反例单独保留完整终端输出文件，因此不把当前绿测伪称为完整的逐项红测原始包。修前行为、对应测试方法和同断言绿测映射已写入 LM/LI 清单；最终源码和全部绿测结果已留存。

## 6. 实际测试结果

### 6.1 后端定向

- 结果：122/122 PASS。
- 覆盖：12/20/65 分钟合成场、120/150 用户回合、241/301 总回合、300 独立事实、超长单 turn 20 facts、跨批关系、B7 语义、typed failure、默认 Worker 装配、Live 容量和 Realtime profile。
- 证据：`evidence/backend-targeted-tests.log`

### 6.2 隔离 PostgreSQL

- 并发/回滚：PASS，schema head `0122`；单 Run 最多两份 Provider lease，第三份无租约；两 Worker 竞争只产生一组候选；中途事务失败为 0 候选/0 extraction；旧 epoch 结果被阻止；Provider 等待期间不持有数据库事务。
- 正式记忆链：PASS；短场 1 条、长场 5 条候选，审核后 6 条正式记忆；重建 Store 后 Source/Memory/Version 可读且命令幂等。
- 合同恢复：PASS；同 job 恢复、调用计数 2、旧 lease 拒绝、预算耗尽不提交候选、未批准的 later history 被 typed 拒绝。
- 证据：`evidence/postgres-concurrency-rollback.log`、`evidence/postgres-formal-memory-chain.log`、`evidence/postgres-contract-retry.log`。
- 数据边界：仅合成数据；隔离实例已停止并卸载。

### 6.3 iOS 与 UIQA

- OwnerTruth：530/530 PASS，证据 `evidence/ownertruth-530.xcresult` 和 summary。
- LI-01/LI-02 定向：2/2 PASS，证据 `evidence/li01-li02.xcresult` 和 summary。
- 音频租约/迟到事件保持性：5/5 PASS，证据 `evidence/audio-5.xcresult` 和 summary。
- UIKit/UIQA：候选列表与结构化详情可见；两条候选原子审核成功；回执消费、候选移除、正式 Memory/Version 和版本历史可见；正式记忆列表/详情/发布编辑/预览通过。
- 证据：`uiqa/candidate/`、`uiqa/formal/`。

### 6.4 构建和差异

- iOS Simulator 无签名构建：PASS，`build/simulator-build-escalated.log`。
- generic iOS device 无签名构建：PASS，`build/generic-ios-build-escalated.log`。
- 初次受沙箱限制的构建日志保留在非 `-escalated` 文件中，不计业务失败。
- 两端 `git diff --check`：PASS，无输出。
- 指纹：`evidence/backend-source-fingerprints.sha256`、`evidence/ios-source-fingerprints.sha256`。
- 构建与结果包指纹：`evidence/build-artifact-fingerprints.sha256`。
- 工作树与差异统计：`evidence/backend-git-status.txt`、`backend-diff-stat.txt`、`ios-git-status.txt`、`ios-diff-stat.txt`。

### 6.5 仓库全量发现测试说明

后端执行了 2642 项全量发现测试，结果为 7 failure、46 error：

- 7 个 failure 来自既有路由认证/所有权清单固定数量 259，而当前实际路由数为 260。
- 46 个 error 来自全量发现环境未打开全局 `dreamjourney-api` psycopg pool。
- 本轮相关定向 122/122、隔离 PostgreSQL 三组门禁和默认生产装配均通过。
- 这些失败没有被标为本轮业务红测，也没有修改预期或清单来掩盖；原始证据为 `evidence/backend-full-discovery.log`。

## 7. 保持性和安全边界

- B7 纯问题、引述、助手建议不生成用户事实；同轮和跨批纠正仍保留。
- Live 原文同步、end/ACK/admit、短场保存、B6/B8 只读恢复和未知写禁止重放未改变。
- 原生 Live 声音、低延迟、持续聆听、主动打断及正式记忆绑定未改变。
- 不放宽账号、authority、FeatureGate、CAS、hash、revision 或 Binding。
- 内部草稿不会在 Source 封定前成为候选，也不会自动进入正式记忆。
- 日志只输出安全阶段、计数和脱敏 correlation；未发现正文、token、密钥或原始业务哈希写入新增诊断。

## 8. 未执行项和残余风险

- `PROVIDER_NOT_RUN`：真实 Provider 未执行；受控 HTTP 证明合同和预算，不证明供应商在真实长上下文中的语义质量与延迟。
- `DEVICE_20M_NOT_RUN`：修复版真实 iPhone 20 分钟场未执行。
- `DEVICE_65M_NOT_RUN`：物理 65 分钟以上场未执行；本地仅使用注入时钟和合成负载。
- `DEPLOY_NOT_RUN`：迁移、后端、Worker、开关和 iOS 均未部署。
- `HISTORICAL_REPROCESS_NOT_RUN`：未读取或重放历史失败场、生产候选或 Dead Letter。
- 全量发现环境仍有路由清单和 API pool 基线，需要在独立仓库门禁任务中修复，不能据此宣称整个仓库全绿。
- 真实 Provider 的跨批关系质量、真实设备内存/电量/网络切换、物理 65 分钟 SDK 事件顺序仍需后续验收。

## 9. 后续真机清单

### 9.1 20 分钟场

1. 开始前记录候选数量并确认测试代号不存在。
2. 同一场 Live 至少包含首段、中段、尾段、后半补充、明确纠正和撤回，期间正常问答并验证声音、打断和继续聆听。
3. 物理持续约 20 分钟后手动停止，记录即时、5 秒、20 秒和最终状态；不能只以提示文案判断完成。
4. 候选列表确认全部应保留事实可见，重复/被纠正/被撤回事实不错误发布，且同场无重复。
5. 用户逐条审核；确认后的正式记忆可搜索、内容和版本正确，拒绝项不进入正式记忆。
6. 杀掉 App 重启，正式记忆仍在、候选不复现、无重复写入。

### 9.2 65 分钟以上场

1. 至少五项首中尾事实，包含 61 分钟后的补充和纠正；保持同一产品场次。
2. 跨过旧 3600 秒边界，验证声音、聆听、网络恢复和状态不创建第二场。
3. 停止后按与 20 分钟相同的候选、审核、正式记忆、重启幂等链验收。
4. 记录候选完整内容、来源、修订版本、重复数和所有错误原文。

## 10. 结论

本轮本地必要项已完成，状态为 `LOCAL_PASS`。本结论只覆盖本地受控网络、隔离 PostgreSQL、模拟器/UIQA、回归和构建，不代表真实 Provider、部署或真机场景已经通过。
