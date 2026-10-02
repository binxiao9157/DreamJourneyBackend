# DreamJourney Live 逐轮模拟与全链验收本地报告

## 结论

`LOCAL_PASS / READY_FOR_SEPARATE_DEVICE_AND_PROVIDER_RETEST`

本轮在不使用手机、真实模型或生产环境的条件下，完成逐轮事件到磁盘、真实 HTTP、后端 Source/Worker、真实 iOS 候选列表、隔离审核、正式记忆和 Store 重建读取的双向全链。逻辑 20 分钟 110 用户回合与逻辑 65 分钟 150 用户回合均通过；每条 long 前均由同一最终源码/构建/配置的独立 short 门禁放行。

这不是物理时长、真实 SDK 顺序、真实 Provider 或真机结论。

## 实际修改

### 产品代码

- `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
  - `OwnerTruthInterviewLiveTurnOutboxStore.requestClose`
  - 重复 close 未显式传 manifest 时，使用 envelope 中已冻结的 `closeManifest`，不再把已解决交接清单回退为空。
  - 不改变 unknown write、账号、策略、CAS、hash、revision 或 Binding 规则。

### iOS 验收装配

- `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift`
  - 新增 IDEMP-01 红绿用例。
  - 新增 logical20 110 用户回合场景；扩展 logical65 150 用户回合。
  - long 前实际消费一次性 short receipt，消费后重复请求必须为 409。
  - 候选读回使用真实 `OwnerTruthCandidateInboxViewController`、真实 FeatureGate 和 BackendClient，校验正文、预览、来源和数量。

### 后端受控测试适配

- `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py`
  - 受控模型适配器支持本轮跨批重复、补充、纠正和撤回夹具。
  - 普通纯问题整理单元允许可靠空结果；没有回退为拼接原文。Source、support review 和候选校验仍会阻断漏事实或非法证据。
  - 未修改本轮后端生产业务合同。

### 运行工具

- `tools/cap15_round_simulation_server.py`
  - 生产路由原样转发；仅增加本地控制端点、逐轮对账、短场收据、候选观察和负例探针。
  - 受保护指纹覆盖 Manager、Coordinator/Store、BackendClient、FeatureGate、候选 Controller、Worker、长记忆服务、配置、迁移、测试和 runner。
- `tools/run_round_simulation.py`
  - 强制 short→long 顺序；SKIP 按失败处理；为模拟器进程显式注入隔离配置。

## 先红后绿

### IDEMP-01

- RED：`red/idemp01-red.xcresult`
  - 默认空参数重复 close 后，`resolvedCloseManifestHandoffIDs` 从 1 回退为 0。
- GREEN：`green/idemp01-green.log`
  - 相同业务断言通过；重建 Store 后已解决清单保持 1。

### 验收装配校正

两次真实全链曾因验收断言错误而失败，均保留日志：

1. 同一 Source 内两处证据被错误要求为两个 `sourceRefs`。校正为支持复核草案必须包含两个不可变 turn 索引。
2. logical65 第二次同义表达含“仍然”，精确短语统计漏算第二生产 unit。校正统计器，不改变产品整理结果。

最终同一断言证明：两个生产 unit、两个 turn 证据、一个候选、一个正式记忆。

## 核心结果

### 逻辑 20 分钟

- 110 用户 + 110 助手 = 220 条事件，逐条角色、顺序和正文摘要全部匹配。
- 真实 iOS 候选页读到 4 条本场候选。
- short + long 完成审核后正式记忆共 5 条，Store 重建读取一致。
- 跨批重复证据：turn 7、189；生产整理 unit 数 2；最终稳定事实候选数 1。

### 逻辑 65 分钟

- 150 用户 + 150 助手 = 300 条事件，逐条匹配。
- 真实 iOS 候选页读到 17 条本场候选。
- short + long 完成审核后正式记忆共 18 条，Store 重建读取一致。
- 跨批重复证据：turn 7、221；生产整理 unit 数 2；最终稳定事实候选数 1。

### 负例

- 漏中段、漏尾段、错 Source、隐藏客户端候选：全部被验收工具识别。
- 缺失、失败、不同 run/source/config/build 的 short receipt：全部在 long 业务请求前返回 412，业务请求计数不变。
- 合法 receipt 第二次消费：409，不启动第二条 long。

## 回归、构建与诊断

- OwnerTruth 最终全量：563 tests，3 skipped，0 failures，PASS。
- 首轮 OwnerTruth 全量：1 个 checkpoint 分类断言瞬时失败；同用例隔离重跑 PASS，随后全量复跑 PASS。首轮证据保留在 `logs/owner-truth-full.log` 和 `regression/owner-truth-full.xcresult`。
- Echo/音频/账号/核心合同：51/51 PASS。
- 后端受影响 unittest：91/91 PASS。
- 模拟器 build-for-testing：PASS。
- 通用 iOS 设备无签名构建：PASS。
- 交付日志与 JSON 的凭据模式扫描：未发现 bearer、token、secret、private key 或非占位 API key。
- 逐轮账本只含 sequence、role、匹配状态和截断 digest，不含正文。

## PostgreSQL

- 使用本机隔离 PostgreSQL 16，监听 `127.0.0.1:55520`。
- 每条场景创建独立随机数据库，应用并验证迁移 head `0122`，结束后删除场景数据库。
- 实际验证 Source、候选、审核、正式记忆、Store 重建读取和幂等；未连接生产数据库。

## 发布判断

- 本轮新增产品差异是 iOS close manifest 幂等修复，后续随 iOS 版本发布。
- 本轮没有新增后端生产业务代码；但完整长场能力仍依赖当前工作树中前序后端长记忆实现与 `0122` 迁移按既定发布方案上线。
- 本轮未部署，因此不能据此声明生产已具备能力。

## 残余风险与 NOT_RUN

- 真实 Provider 的自然语言输出、真实 SDK 回调顺序：NOT_RUN。
- 真实 iPhone、物理 20 分钟、物理 65 分钟：NOT_RUN。
- 生产部署、生产数据、历史失败任务：NOT_RUN。
- 当前两端工作树包含大量前序未提交修改；本轮未 reset、未覆盖、未 commit/push。发布前必须按累计任务做独立差异审查。

## 局部回退

1. iOS 产品回退只涉及 `requestClose` 中 `effectiveManifest = envelope.closeManifest ?? manifest` 代码块；回退会重新暴露 IDEMP-01，不建议单独回退。
2. iOS 新测试、后端受控适配和 run-04 工具可按各自代码块撤回，不影响生产运行。
3. 不得通过删除磁盘坐标、重放 unknown write、放宽账号/策略/Binding 或跳过 Source/候选校验来回退。

## 后续真机清单

真机仍遵循每场先短后长：

1. 短场两轮，第二轮补充第一轮；候选可见且 Source 正确、无重复；用户审核后正式记忆可查；重启后仍在且不重复。
2. 物理 20 分钟长场，覆盖首中尾事实、后半补充、跨批重复/纠正/撤回；候选可见→用户确认→正式记忆→重启回查。
3. 未来物理 65 分钟长场同样执行；不得用本地逻辑时钟结果替代。
4. 任一 short 失败，停止对应 long，不复用旧 short 结果。
