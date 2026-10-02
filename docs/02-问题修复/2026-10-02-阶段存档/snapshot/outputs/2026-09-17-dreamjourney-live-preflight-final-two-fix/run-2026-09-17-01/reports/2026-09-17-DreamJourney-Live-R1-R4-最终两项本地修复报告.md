# DreamJourney Live R1-R4 最终两项本地修复报告

日期：2026-09-17\
依据：`2026-09-17-Astra-R1-R4复核-剩余两项.md`\
状态：`A_LOCAL_PASS / READY_FOR_LIVE_DEVICE_RETEST`

> 该状态只表示本轮本地确定性门禁完成。真实 iPhone、真实 Provider/SDK 顺序、物理 20 分钟仍为 `NOT_RUN`。

## 1. 基线与范围

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`，开工及交付时均保留前序大量未提交成果。
- 后端 HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`，工作树干净，本轮无后端改动。
- 未 reset、clean、整文件覆盖、commit 或 push；未启动或安装手机，未部署，未访问生产数据或处理历史任务。
- 前一 run 的 R1-R4、B8、B8-S01、S01-08 已通过成果全部保留。本报告只覆盖最终两项及其受影响回归。

## 2. 最终缺口 A：冻结成员与关闭边界

### 修前根因

`DialogProviderCanonicalIngressRouter.freeze` 已接受并冻结新 question 的身份，但首次 `registerCanonicalMember` 与正文都延迟到 Echo delivery。非空场此时停止会先持久化 `closeIntent`，Store 随后按正确的关闭保护拒绝这个尚未登记的新成员，导致关闭水位提前、材料丢失。

### 修改

- `DialogEngineManager.swift`
  - `DialogCanonicalTranscriptDeliveryBinding` 增加同步 `reserve` 合同，旧 delegate 默认保持允许。
  - `DialogProviderCanonicalIngressRouter.freeze` 在 router 锁内为首次新成员取得原场 reservation；失败时回滚本次 question 窗口注册并拒绝事件。
  - reservation 与冻结时捕获的 generation、operation 和原 delivery binding 一起归属原场；换场后不重新读取当前 sink。
- `EchoViewController.swift`
  - `EchoCanonicalTranscriptIngress.reserveFrozen/submitFrozen` 接入 coordinator 的 handoff barrier。
  - `EchoLiveMemoryCaptureCoordinator.reserveCanonicalHandoff/completeCanonicalHandoff` 持有并释放原成员交接权。
  - `finish`、`persistCloseIntent`、`persistCloseRequestIfReady` 将未完成 handoff 纳入空场、closeIntent 和最终 N/end 决策。
  - 普通 `persistAndEnqueue` 在 finish 期间完成后重新唤醒 `persistCloseIntent`，避免等待持久化时关闭意图永久遗漏。
- Store 的“closeIntent 后拒绝真正新 ID”规则未删除或放宽。

### 红绿证据

修前反例 `testManagerFrozenNewMemberDelaysNonEmptyCloseBoundaryUntilOriginalHandoff`：

- A 场 q1 已实际落盘；q2 identifier/final 已 freeze 但 delivery 暂停。
- stop A 后修前错误写入不含 q2 的 closeIntent，并提前发送 end；q2 后续被关闭保护拒绝。
- B 场保持零旧材料、停止后的 q3 仍被拒绝、账号失效保护由既有测试覆盖。

证据：

- 红：`evidence/red/remaining-two-pre-fix.xcresult`，该测试失败。
- 绿：`evidence/green/remaining-two-post-fix.xcresult`，相同业务断言通过。
- 最终组合：`evidence/green/final-two-affected-targeted-final.xcresult`。

状态：`PASS_LOCAL`。

## 3. 最终缺口 B：FeatureGate 同一逻辑时钟

### 修前根因

逻辑 20 分钟测试只向 Live request authority 注入 `fakeNow`，`FeatureGateService` evaluator 仍使用真实 `Date()`。因此测试虽然证明了 authority TTL 和组合链，却没有证明真实 gate/cache 在同一逻辑时间过期。

### 修改

- `DreamJourneyBackendClient.swift`
  - `FeatureGateService` 增加窄范围 `now: () -> Date` 依赖；生产 shared 默认 `Date.init`。
  - `requestDecision` 与 `revalidateRequest` 均把 `now()` 传入真实 evaluator。
  - QA 构造器允许测试注入同一时钟，没有增加生产权限旁路。
- `OwnerTruthContractsTests.swift`
  - `testB8FreshAcknowledgementUsesCurrentProductionDecisionWhenCapturedRouteExpired` 在 t0 捕获可用 route，推进到 t301 后断言真实 evaluator 返回 `capturedPolicyExpired`，再由新策略允许并继续真实 BackendClient GET/POST。

### 红绿证据

- 红：`evidence/red/remaining-two-pre-fix.xcresult`，t301 仍被旧真实时间判定允许。
- 绿：`evidence/green/remaining-two-post-fix.xcresult`，同一断言证明过期、刷新和 transport。
- 当前真实 deny 零写、未知 start 只读恢复、跨轮 poll 隔离及 20 分钟组合链均纳入最终 10 条定向包。

状态：`PASS_LOCAL`。

## 4. 完整回归中发现并关闭的关联遗漏

第一次完整 OwnerTruth 回归执行 501 项时，`testLiveCaptureDeliveryFailureDoesNotStopDurableCaptureOrCloseIntent` 的两个关闭断言失败。原因是 barrier 新增后 `finish` 会等待普通文本持久化，但普通 `persistAndEnqueue` 完成只尝试第二阶段 close request，没有重新触发第一阶段 close intent。

修复只在该成功出口对 `isFinishing` 调用 `persistCloseIntent`。相同测试随后通过，最终完整回归为 501/501。该中间结果保存在 `evidence/green/OwnerTruthContractsTests-full.xcresult`，不得当作最终绿证据；最终包为 `OwnerTruthContractsTests-full-v2.xcresult`。

## 5. 实际测试与构建

实际命令形态：

```bash
xcodebuild test -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=8C90FF12-82E3-41A6-A003-EE0BB26BEAA6' \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests/<定向用例> \
  -resultBundlePath <本 run/evidence/green/*.xcresult>

xcodebuild test -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=8C90FF12-82E3-41A6-A003-EE0BB26BEAA6' \
  -only-testing:DreamJourneyTests/OwnerTruthContractsTests \
  -resultBundlePath <本 run/evidence/green/OwnerTruthContractsTests-full-v2.xcresult>

xcodebuild test -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=8C90FF12-82E3-41A6-A003-EE0BB26BEAA6' \
  -only-testing:DreamJourneyTests/AudioOwnerLeaseModelTests \
  -only-testing:DreamJourneyTests/EchoTurnIntentReducerTests \
  -resultBundlePath <本 run/evidence/green/Echo-Audio-regression.xcresult>

xcodebuild build -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -configuration Debug -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO -resultBundlePath <本 run/evidence/build/Generic-Simulator.xcresult>

xcodebuild build -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -configuration Debug -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO -resultBundlePath <本 run/evidence/build/Generic-iOS.xcresult>
```

| 门禁 | 结果 | 原始证据 |
| --- | --- | --- |
| 两项修前反例 | `FAIL`，0/2 通过 | `evidence/red/remaining-two-pre-fix.xcresult` |
| 两项相同断言修后 | `PASS`，2/2 | `evidence/green/remaining-two-post-fix.xcresult` |
| R1-R4 + 新缺口 + S01-08 定向 | `PASS`，10/10 | `evidence/green/final-two-affected-targeted-final.xcresult` |
| OwnerTruth 完整回归 | `PASS`，501/501 | `evidence/green/OwnerTruthContractsTests-full-v2.xcresult` |
| Echo/音频保持性 | `PASS`，22/22 | `evidence/green/Echo-Audio-regression.xcresult` |
| 通用 iOS Simulator 编译 | `PASS` | `evidence/build/Generic-Simulator.xcresult` |
| 通用 iOS 设备无签名编译 | `PASS` | `evidence/build/Generic-iOS.xcresult` |
| `git diff --check` | `PASS` | `evidence/source-and-build-fingerprints.txt` |
| 后端/隔离 PostgreSQL | `NOT_RUN / N/A` | 本轮未改后端合同或事务 |
| 独立 XCUITest UIQA | `NOT_RUN` | 本轮要求由真实 UIKit 对象及本地组合链覆盖，不冒充独立 UIQA |
| 真实 iPhone | `NOT_RUN` | 按要求暂停 |
| 真实 Provider/SDK 顺序 | `NOT_RUN` | 受控包使用生产解析与 assembler，不等同真实 SDK |
| 物理 20 分钟 | `NOT_RUN` | 已有注入时钟逻辑 20 分钟，不等同物理运行 |

## 6. 保持性与安全边界

- 原 start、append、end、ack、admit 的 command/session/batch/版本和账号作用域保持绑定。
- unknown write 继续只读核实，未增加业务 POST 重放。
- 真实 deny、账号切换、换场和停止后的新事件继续 fail closed。
- 原生 Live 声音、持续聆听、低延迟、打断和正式记忆绑定未改；未恢复串行语音链路。
- 本轮没有新增正文、token、密钥、完整响应或原始业务 ID 日志。

## 7. 部署判断、风险与回退

- 后端：无改动，无部署需求。
- iOS：只有后续获得授权后才安装并做真机验收。
- 残余风险：reservation 是进程内 freeze-to-handoff barrier；真实 SDK 的极端字段缺失/乱序及进程在 handoff 前被系统杀死仍需真机观察。系统杀死窗口不会伪造已关闭水位，但可能保留为旧 Live 未完成状态。
- 局部回退只限：Manager binding/reserve 块、Echo handoff barrier 与关闭唤醒块、FeatureGate 时钟注入块及对应测试。不得回退 Store 关闭保护、账号隔离或 unknown-write 只读恢复。
- 后续最小真机验收应先验证一场已有内容的 Live，在下一问刚完成识别时立即手动停止，确认该问仍进入同场候选且关闭水位无重复；随后等待一次自然 TTL 跨越并关闭，确认 fresh policy 后原场 end/ack/admit 完成。任一阶段出现材料缺失、跨场、重复写、`statusUnknown` 无法只读恢复或旧状态覆盖，应立即停止并保存脱敏日志，不继续制造场次。

## 8. 结论

复核指出的最终两项已取得真实红绿配对，最终代码通过定向、完整 OwnerTruth、Echo/音频和两类构建。本地状态恢复为：

`A_LOCAL_PASS / READY_FOR_LIVE_DEVICE_RETEST`

工作停在本地交付，等待复核及后续真机授权。
