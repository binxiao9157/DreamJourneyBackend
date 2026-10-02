# DreamJourney Live 采集回归与持续保存补充本地交付报告

日期：2026-09-17

状态：`A_LOCAL_INCOMPLETE`（2026-09-17 独立复核后撤回 READY）

> 复核校正：本报告原先对 LC-08、TTL-L04 和整链生产装配的描述超出结果包实际覆盖。既有 10/10、492/492、52/52 与两类构建证据继续保留，但不再据此宣称真机前置完成。R1-R4 补充修复与新证据另行交付。

## 1. 基线与范围

本轮在 B8、B8-S01、S01-08 与 2026-09-16 Live 修复的未提交工作树上继续开发。没有 reset/clean，没有整文件覆盖，也没有回退前序成果。

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 后端 HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`
- 后端工作树：本轮无修改
- 真机、部署、生产数据、历史任务、Dead Letter、commit/push：均未执行
- 第三项历史 UI 仲裁：未开始

本报告只归属本次 Live 增量。iOS 工作树中其余 B4-B8/FM/B6 前序修改继续保留，不把整份 dirty diff 归入本轮。

## 2. 本轮补齐的三项实现

### 2.1 SDK 原始入口冻结身份与接收顺序

修改：

- `DreamJourney/Sources/Services/DialogEngineManager.swift`
  - 在 SDK 原始消息入口建立 canonical member，冻结 message identity、identifier window 与接收顺序。
  - identifier-only 信息先登记成员，再把事件排入 Manager/Echo；无 ID 数据不再靠当前 turn 猜归属。
  - simulator 与 production 的 `DialogEngineDelegate` 同步声明 member registration 回调。
- `DreamJourney/Sources/Modules/Echo/EchoViewController.swift`
  - `onCanonicalTranscriptMemberRegistered` 在关闭 barrier 前同步登记成员。
  - 停止时先排空已登记但 Manager/Echo 尚未处理的成员；stop 后新成员 fail closed。
- `OwnerTruthContracts.swift`
  - canonical snapshot/outbox 保存成员身份、顺序、finality 和 close coverage。

行为变化：事件已经进入 SDK/Manager 队列、但 Manager 尚未来得及处理时，停止动作不会把它当作“停止后新事件”丢弃；同时不会把它错误归入下一问题、下一场或新账号。

### 2.2 start 原命令、场次坐标与曝光状态持久化

修改：

- `OwnerTruthContracts.swift`
  - start command 支持持久化，保存原 command、session、productSession、entry mode 和请求坐标。
  - start dispatch 使用 `preparedNotExposed → mayExpose/outcomeUnknown/serverRejected/committed` 单向状态。
  - store 重建时保留 prepared start 与曝光状态；旧记录缺信息时保守按 unknown，不猜 notSent。
- `EchoViewController.swift`
  - 在 start POST 前先写入原 command；网络任务可能曝光前先持久化 `mayExpose`。
  - 回执丢失或不可绑定时按原坐标只读 status；只有精确 start operation 命中才绑定。
  - unknown、missing、超时或错配均不再发送第二次 start POST，也不创建替代 command。

行为变化：TTL-L08 不再因“客户端尚未持久化 start 坐标”而 BLOCKED。页面/对象重建后可只读核实原 start，仍遵守未知写禁止重放。

### 2.3 fresh request authority 贯通 inbox/ack 与 BackendClient

修改：

- `OwnerTruthContracts.swift`
  - `OwnerTruthInterviewNaturalInputRequestAuthority` 作为不可变请求授权，携带 lease、fresh FeatureDecision、期限与诊断上下文。
  - inbox/ack typed 接口必须接收当前意图取得的新鲜 authority；默认旧接口 fail closed。
- `DreamJourneyBackendClient.swift`
  - inbox/ack/start/append 实际 transport 消费调用方传入的 authority，不在下层重新抓取过期 route。
  - 业务写继续关闭认证恢复后的自动重放；已曝光后失败保持 unknown/read-only recovery。
- `EchoViewController.swift`
  - 本场 policy refresh 后把同一新鲜 authority 贯穿 UseCase 和 BackendClient。
  - 保留 B8 回执绑定、检查点、admission fresh authority 及 S01/S01-08 隔离。

行为变化：上层刷新成功而下层仍复用旧授权的断层被关闭；inbox/ack 与 start/append 使用一致的 lease 和 fresh decision，同时不扩大业务写重试权限。

## 3. 真实装配与逻辑 20 分钟

新增本地装配覆盖：

- `testManagerIngressFreezesIdentifierWindowBeforeEchoQueueAndStop`
- `testManagerEchoOutboxTenQuestionsShareOneExpiredPolicyRefresh`
- `testLiveLogicalTwentyMinutesRefreshesMultipleTTLsWithMonotonicDelivery`
- `testLiveExpiredPolicyExplicitDenyDoesNotExposeOrPostAppend`
- `testLiveStartCommandAndExposureSurviveStoreReconstruction`
- `testLiveUnknownStartReconstructionUsesExactReadOnlyStatusWithoutSecondPost`
- `testB8FreshAuthorityFlowsThroughRealBackendInboxAndAcknowledgement`

10 问测试不是直接向 Outbox 塞 `.complete`：输入先经过 Manager 原始入口和 identifier-only/interim/explicit-final 解析，再到 Echo、磁盘 Outbox、UseCase/受控 BackendClient。断言 10 个 owner final 单次定稿，assistant 以实际 reply 聚合，N 由本场登记成员计算，并发 TTL 唤醒只触发一次策略恢复。

逻辑 20 分钟使用注入时钟推进多个 TTL 周期，并插入短断网、并发唤醒、fresh allow 和 explicit deny。断言本场坐标不变、complete 持续按序落盘、可靠未曝光命令有界首次发送、unknown 不重放，且只有最终 stop 固定关闭水位。

## 4. 红绿证据

| 场景 | 修前/首次结果 | 修后结果 |
|---|---|---|
| LC-06 原始入口与 stop 交接 | `evidence/red/LC06-ingress-stop-red.xcresult` | `evidence/green/Live-final-targeted-10.xcresult` |
| LC-08 10 问装配 | `evidence/red/LC08-ten-question-red.xcresult`：3 通过、1 失败 | `evidence/green/LC08-ten-question.xcresult` 及定向 10/10 |
| start durable/read-only recovery | 上一报告 TTL-L08 为 BLOCKED | `evidence/green/TTL-L08-start-recovery.xcresult` |
| fresh inbox/ack BackendClient | 旧路径会在下层重取旧授权 | `evidence/green/TTL-L10-real-backend-inbox-ack.xcresult` |
| 完整 OwnerTruth 首次回归 | `evidence/red/OwnerTruth-regression-red.xcresult`：490/492；测试 spy 未提供新必需 authority | 同断言 2/2 后，完整 `OwnerTruth-full-492.xcresult`：492/492 |
| 通用 iOS 编译 | `evidence/red/Generic-iOS-protocol-red.xcresult`：production delegate 缺 member 回调，2 个编译错误 | `evidence/build/Generic-iOS.xcresult`：0 错误 |

完整回归的两项红是测试装配未跟随“fresh authority 必填”合同产生，修复时没有降低业务断言，也没有恢复 always-allowed 旁路。

## 5. 实际验证结果

| 验证 | 结果 | 证据 |
|---|---:|---|
| LC/TTL/B8/B6 定向组合 | PASS 10/10 | `evidence/green/Live-final-targeted-10.xcresult` |
| OwnerTruth 完整回归 | PASS 492/492 | `evidence/green/OwnerTruth-full-492.xcresult` |
| 音频/Echo 保持性 | PASS 52/52 | `evidence/green/Audio-preservation-52.xcresult` |
| 模拟器通用目标编译 | PASS | `evidence/build/Generic-Simulator.xcresult` |
| 通用 iOS Device 无签名编译 | PASS | `evidence/build/Generic-iOS.xcresult` |
| `git diff --check` | PASS | `evidence/source-and-build-fingerprints.txt` |

未执行：真实 iPhone、真实 Provider/SDK 新序列、物理 20 分钟、独立 XCUITest/UIQA、部署、生产数据和隔离 PostgreSQL。本轮后端无代码/schema/事务变化，隔离 PG 对本增量为不适用，不能据此声称后端或生产闭环已验证。

## 6. 源码指纹

详见 `evidence/source-and-build-fingerprints.txt`。关键文件：

- `OwnerTruthContracts.swift`：`948dd28dee5b93b01a47cecf6a98ca491cef03d8369fcb9f82462df2ccdbb305`
- `EchoViewController.swift`：`253cb4b2598622ab07ee184394609891701924ffe3693a2e59b94232d75b3cd3`
- `DreamJourneyBackendClient.swift`：`02a5a36f55f6e9ca829894332d6bcd5384810ba46b572005ca8721d0f5369ae8`
- `DialogEngineManager.swift`：`37b215d01e032a3f041dcb79f2dcf9c7e00870c2fb25970f4240217743a7ed76`
- `OwnerTruthContractsTests.swift`：`fe18f9c777f08a4440faae10ddc3efc7735ed71a7c962bd3239caca331cf6c46`

## 7. 部署判断与发布顺序

- 本轮仅修改 iOS；后端无需因本轮重新部署。
- 若后续获准发布，先确认现有后端合同版本与 iOS 兼容，再发布 iOS；本轮没有新的后端迁移顺序。
- 当前未部署、未安装 iPhone、未访问生产数据，也未 commit/push。

## 8. 残余风险与真机最小验收

仍待现场确认：

1. 真实 SDK 对 identifier-only/interim/explicit-final 的实际顺序是否与受控输入一致。
2. 设备调度下“消息已入队、Manager 未处理即 stop”是否完整排空且不产生跨场污染。
3. 真实网络跨 TTL、短断网、进程切换后的 start/append unknown 是否只读恢复且零重放。
4. 真实长场中的声音、持续聆听和主动打断保持性。

最小真机步骤应在另行授权、日志就绪并安装本构建后执行：先做一场含 10 个明确标记的连续 Live，中途跨一次自然 TTL 并短暂断网；停止后核对单场 N、候选完整性、零重复及无自动写重放；随后重启 App 验证原场只读恢复。物理 20 分钟仍需单列，不用本地逻辑时钟结果替代。

## 9. 局部回退

按代码块回退，不触碰 B8/B8-S01/S01-08 或其他前序修改：

1. 回退 DialogEngineManager 的 member-first ingress 与 production/simulator delegate 回调。
2. 回退 Echo 的同步 member registration 和 close drain 交接。
3. 回退 outbox 的 prepared start command/start dispatch state 持久字段及精确只读恢复。
4. 回退 inbox/ack typed fresh authority 与 BackendClient 对应重载。
5. 删除本 run 新增测试。

该回退会恢复停止竞态漏采、start unknown 无法安全跨重建核实和下层复用旧授权的已知缺陷；不得配合删除本地恢复文件或补发未知写。

## 10. 停止点

独立复核后状态恢复为 `A_LOCAL_INCOMPLETE`。在 R1-R4 的生产接线和组合证据完成前，不进入真机复测；不会自动部署、安装、启动 Live 或开展第三项历史 UI 仲裁。
