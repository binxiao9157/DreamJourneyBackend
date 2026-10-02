# DreamJourney Live 采集回归与持续保存本地修复报告

日期：2026-09-16

状态：`A_LOCAL_INCOMPLETE`

## 1. 范围与基线

本轮以 B8-S01 及 S01-08 已完成工作树为基线，保留 fresh admission authority、回执绑定、检查点、未知写只读核实和跨轮次 poll 隔离。没有 reset/clean，没有覆盖其他任务修改。

- iOS：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 后端：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`
- 后端 HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`
- 后端工作树：本轮未修改
- 真机、部署、生产数据、历史任务、commit/push：均未执行

当前 iOS 工作树含多轮前序未提交成果。本报告只描述本篇 Live 增量，不把全部 dirty diff 归到本轮。

## 2. 已确认原因与修改

### 2.1 L1-A：显式 final 被降为 interim

已确认源码缺陷：provider 的显式 `is_interim=false` 在 canonical 接口中曾被映射为 interim，导致没有 QueryConfirmed 时 owner 无法定稿。

修改：

- `DialogEngineManager.swift`
  - 新增严格 `DialogProviderASRParser`，保留 finality evidence 和 evidence source。
  - JSON Bool `is_interim=false/true` 分别映射 explicitFinal/explicitInterim。
  - 缺失、null、字符串、数字按 unknown/invalid；旧格式 `definite` 只接受严格整数 0/1，Bool 不被桥接成 Int。
  - identifier-only 只登记身份，不生成空 turn；ASREnded 不提升 interim。
  - 移除按 turn sequence 猜测 question/reply ID 的 fallback。
  - explicit final 直接映射 canonical `.complete`，QueryConfirmed 仅作补充确认。
- `OwnerTruthContracts.swift` / `EchoViewController.swift`
  - canonical event 与磁盘槽位保存终态证据。
  - complete 后迟到 interim 不降级；相同 final 幂等；不同 final 显式冲突，后续新问题仍可继续。

修前红：

- `evidence/red/LC01ExplicitFinalBusinessRed.xcresult`
- 1 test / 1 failure：期望 `complete`，实际为 `interim`。

修后绿：

- `evidence/green/LC01-LC02-ExplicitFinal-v2.xcresult`
- 2 tests / 0 failures。

### 2.2 L1-B：长场派送复用旧授权及未知写边界不完整

已确认源码缺陷：Live start/append 上层刷新后，下层仍可能重新取旧 route；start/append 也缺少与 end/ack 一致的 typed exposure 分类。

修改：

- `OwnerTruthContracts.swift`
  - 增加 `OwnerTruthInterviewNaturalInputRequestAuthority`，将一次请求的新鲜 FeatureDecision 作为不可变输入。
  - Live start/append 使用 typed write outcome，不以旧 route 到期推翻可绑定成功回执。
- `DreamJourneyBackendClient.swift`
  - 新鲜 request decision 经 scope/版本/期限校验后传入实际 requestJSON。
  - Live start/append 业务 POST 禁止 transport 内部认证刷新重发。
  - task 暴露后的断连、401、5xx、解码失败保守归入 unknown/serverRejected，不以错误名称猜 notSent。
- `EchoViewController.swift`
  - outbox 持久化 `preparedNotExposed / mayExpose / outcomeUnknown / serverRejected`。
  - 旧记录缺曝光字段时按 unknown 读取，不反推为未发送。
  - 只读 status 精确命中 append 后才确认并推进连续前缀；missing/timeout 不重发 POST。

修前红：

- `evidence/green/LC04-TTL-Authority-v3.xcresult`
- 路径历史上误放在 green 目录，实际结果为 3 tests / 1 failure；fresh authority 未进入 typed start/append。

修后绿：

- `evidence/green/LC04-TTL-Authority-v4.xcresult`
- 3 tests / 0 failures：fresh authority、曝光状态单向转换、迟到 interim 保护。

限制：当前 fresh authority 绿测使用真实 UseCase 和持久状态，但 client/evaluator 仍是可控实现，不足以单独证明指导要求的真实 FeatureGate evaluator + BackendClient + 逻辑 20 分钟组合链。

### 2.3 停止边界：已接收 final 因主线程延迟而丢失

独立复核发现：provider 回调已在停止前进入 `onCanonicalTranscriptEvent`，但其 main queue closure 在停止后执行时，会因 active token/当前 coordinator 已清除而丢失。

修改：

- 新增 lock-backed `EchoCanonicalTranscriptIngress`。
- ingress 在关闭前原子登记事件，并捕获原协调器身份；关闭后的新事件立即拒绝。
- 延迟 delivery 执行前重新校验原账号租约，不跨账号、vault 或 generation 落盘。
- 非“自己助手”上下文提前退出时同步关闭旧 ingress。

证据：

- `evidence/green/LC-Ingress-Identity-v1.xcresult`
- 2 tests / 0 failures：
  - 停止前已登记 final 在停止后仍由原 coordinator 排空并写入磁盘；停止后的新事件不进入旧场。
  - 事件排队后账号代次变化时，旧事件不落盘，start 请求数为 0。

说明：本项修复前第一次尝试结果 `LC-StopDrain-v1.xcresult` 是模拟器目的地选择错误，属于工具链无效证据，不作为业务红测。没有伪造本项红测。

### 2.4 同场状态投影

- coverage gap、saving、queued 等当前关闭场状态与 idle 交互状态分离。
- idle 重绘不再隐藏已确定的本场 gap。
- 同值状态回调仍保持用户可见。

证据：`evidence/green/LC-Contract-UI-v1.xcresult` 中 `testEchoIdleKeepsCurrentLiveCoverageGapVisible`。

## 3. 实际测试与结果

### 3.1 定向测试

| 结果包 | 结果 | 说明 |
|---|---:|---|
| `evidence/red/LC01ExplicitFinalBusinessRed.xcresult` | 0/1，通过数/总数 | L1-A 修前真实业务红 |
| `evidence/green/LC01-LC02-ExplicitFinal-v2.xcresult` | 2/2 | 显式 final 与严格解析 |
| `evidence/green/LC04-TTL-Authority-v3.xcresult` | 2/3 | L1-B 修前真实业务红，目录名不代表结果 |
| `evidence/green/LC04-TTL-Authority-v4.xcresult` | 3/3 | fresh authority、曝光状态、迟到 interim |
| `evidence/green/LC-Contract-UI-v1.xcresult` | 6/6 | gap、冲突、去重、只读命中、UI |
| `evidence/green/CanonicalParser-Playback.xcresult` | 4/4 | strict parser 与文本/播放终态分离 |
| `evidence/green/LC-Ingress-Identity-v1.xcresult` | 2/2 | 停止排空与账号轮换隔离 |

### 3.2 完整回归与构建

- OwnerTruth 完整回归：`evidence/green/OwnerTruthContracts-full-v2.xcresult`
  - 485 tests / 0 failures。
  - 包含 B6、B8、B8-S01/S01-08、候选与正式记忆既有保护。
- 音频租约回归：`evidence/green/AudioOwnerLeaseModel-full-v2.xcresult`
  - 5 tests / 0 failures。
- 通用 iOS Device arm64 无签名构建：`evidence/build/Generic-iOS-v2.xcresult`
  - `succeeded`，0 errors，76 warnings。
  - 警告主要来自既有第三方弃用/Swift 6 兼容；另有工程已有 weak capture warning，本轮没有将警告当失败或 PASS 替代物。
- 模拟器构建：上述全部模拟器测试均完成 build-and-test。
- `git diff --check`：PASS。

未执行：独立 XCUITest/UIQA、真实 SDK、真实 Provider、iPhone、真实长场、隔离 PostgreSQL。后端无变更，本轮不存在需要 PG 验证的新事务合同。

## 4. LC/TTL 状态摘要

完整逐项表见 `reports/2026-09-16-DreamJourney-Live-LC-TTL执行清单.md`。

- LC-01/02/03/04/05/09/10/12/13/14/15：`PASS`（本地分层证据）。
- LC-06/07/11：`PARTIAL`，已覆盖冻结 coordinator、账号隔离和停止拒绝，但未完整覆盖 Manager 中 Q1/Q2 原始回调交错及 provider epoch 歧义。
- LC-08：`NOT_RUN`，未执行真实 Manager→Echo→Outbox→UseCase 的 10 问完整场景。
- TTL-L01/05/06/07/09/10/11/12：`PARTIAL`，局部合同已验证，但没有按同一受控 clock 贯穿真实 evaluator、BackendClient 和逻辑 20 分钟。
- TTL-L02/03/04：`NOT_RUN`。
- TTL-L08：`BLOCKED`。start command 在当前实现中未形成足以跨进程精确核实的 durable prepared 记录；在不新增后端合同、不伪造 receipt、不重发 POST 的边界内，无法完成该验收。

因此不能标记 `A_LOCAL_PASS / READY_FOR_DEVICE_RETEST`。

## 5. 源码指纹

- `EchoViewController.swift`：`4f3ea01af3a68e4c1261fb905fd66e486693a56ca55dfbd1dfcbe5c1d9166b19`
- `DialogEngineManager.swift`：`7783cc4bc77be2fdc67eaa1fda53f3032f6122e9201e29ce0c463d9ebaaea793`
- `OwnerTruthContracts.swift`：`bdbe75e399d38beb0bd242f24a3de942f8fefc85373365f40b2a4728f07b7e8c`
- `DreamJourneyBackendClient.swift`：`6f15496ba8459b35cd5171b5ed670ca8f2506fa5d768dce551827f037847136f`
- `OwnerTruthContractsTests.swift`：`e032cde5939c7ab5b1a21cb137d73ec3615a8a3869d558c28f86b118830e37d0`

## 6. 发布与部署判断

- 本轮代码改动仅在 iOS。
- 后端无新增合同、事务或 schema 变化，无需因本轮重新部署后端。
- 本轮未部署、未安装 iPhone、未访问或修改生产数据。
- 由于 LC/TTL 严格门禁仍有缺口，不建议把当前结果称为发布就绪。

## 7. 残余风险

1. 完全无 ID、且在新问题登记后才到达的旧问题原始 SDK 包，若供应商没有可验证顺序/epoch，客户端不能凭文本可靠区分；当前策略应 fail closed，但缺少真实 SDK 序列证据。
2. 10 问和逻辑 20 分钟未在同一装配链上运行，无法证明多次 TTL 轮换下不会出现新的单飞/预算问题。
3. start 已曝光但回执丢失时，durable start 原命令坐标不足，不能安全实现跨进程只读绑定；不得用新 start POST 补洞。
4. 本地模拟器无法替代真实 Provider 回调顺序、设备进程调度和最终待确认闭环。

## 8. 局部回退

只按本篇代码块回退，不回退 B6/B7/B8/B8-S01 或其他 dirty 修改：

1. 回退 DialogEngineManager 的 strict parser/finality evidence 和 explicit final mapper。
2. 回退 canonical store 的 late interim、conflict 与 dispatch-state 增量。
3. 回退 Live request authority 与 typed start/append transport 增量。
4. 回退 `EchoCanonicalTranscriptIngress`、停止排空及本场 UI 投影增量。
5. 删除本篇新增测试。

该回退会恢复显式 final 被降级、旧授权复用和停止竞态丢 final 的已知缺陷，因此只能作为整个 iOS 版本回退，不得同时清磁盘记录或重放未知写。

## 9. 停止点

本轮在本地交付处停止。真机继续暂停；未开始第三项历史 UI 仲裁，也未开始新的 Live 场次。
