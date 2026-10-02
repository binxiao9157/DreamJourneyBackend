# DreamJourney DJ-LIVE-SAVING-01 本地修复报告

- 日期：2026-09-17
- 状态：`LOCAL_PASS / DEVICE_PENDING`
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 分支：`feature/prd-stitch-ui-adaptation`
- 授权边界：仅本地代码、模拟器、自动化与构建；未安装或启动 iPhone，未部署，未访问生产数据，未处理历史任务，未 commit/push。
- 证据根目录：`/Users/gaominge/Documents/liftora/outputs/2026-09-17-dreamjourney-live-saving-fix/run-2026-09-17-01/evidence/`。下文 `evidence/...` 均指向该绝对目录。

## 1. 结论

设计中的 SV-01 假设已由真实装配反例确认：Live 期间 current-session GET 失败后，`OwnerTruthInterviewNaturalInputUseCase` 保留为非 ready 实例；手动停止把协调器置为 saving，但旧实现的 `ensureNaturalInputSession()` 因实例非空直接返回，`advanceIfPossible()` 又因用例非 ready 返回。此时没有请求、计时器或恢复轮次继续拥有工作，页面可无限停留在“正在保存本次对话”。

局部修复后：

1. 关闭中的同一读取意图，只在可证明未发送、账号和原 product session 仍匹配时，最多恢复一次 current-session 读取。
2. 正常恢复后继续使用同一场坐标完成 start、正文 append、end、ACK、admit、同场 status GET 和当前页面提交。
3. 恢复不能推进或请求悬停时，由关闭无进展期限结束 saving；已存在未知写暴露则进入待核实，否则进入待同步。磁盘坐标和正文不删除。
4. 到期会作废旧用例回调的提交权。迟到结果不能启动旧 start、覆盖新轮次或把页面重新改回 saving。
5. 重复 ended continuation 不再把 queued/pending 状态倒退为 saving。

这证明了一个真实代码根因，但不把它宣称为此前真机现象的唯一根因；修复版本真机闭环仍为 `NOT_RUN`。

## 2. 基线与保护

开工时工作区已有大量 B4、B6、B7、B8、FM-POLICY 和 Live 未提交修改。本轮未执行 reset/clean，未覆盖整文件，也未回退前序成果。

本轮基线 SHA-256：

| 文件 | 修前指纹 |
|---|---|
| `OwnerTruthContracts.swift` | `7ad24e61a794e65d73864ae4b147eae95e3dc224c7b403505500afbbbc919b30` |
| `EchoViewController.swift` | `58fd0f38452c113f04c23542844bafc47d0911d0f30437f0d809ed664a91ef9a` |
| `DreamJourneyBackendClient.swift` | `3014beeb51af7a7d40186a1991deccdaff56d746f1abdb0d9b9be385089578f6` |
| `OwnerTruthContractsTests.swift` | `e5c32ef7714af45df65c47e6fb898c9d4d6e3e5c36d759fb43d0914954451115` |

## 3. 修改范围

### 3.1 生产代码

| 文件 | 函数/区域 | 行为变化 |
|---|---|---|
| `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift` | `resumeCurrentSessionReadForClosingIfSafe()` | 仅允许 Live、无终态回执、失败/不可用、明确 requestFailed/requestNotSent 且 start 未暴露时，恢复原 current-session 读取；不创建新命令。 |
| 同上 | `invalidateClosingOperationAfterDeadline(outcomeUnknown:)` | 到期递增 operation generation、取消旧授权等待，并按暴露事实落为 outcomeUnknown 或 requestNotSent，阻断迟到回调。 |
| `DreamJourney/Sources/Modules/Echo/EchoViewController.swift` | `ensureNaturalInputSession()` | 已有失败用例不再成为永久挡板；关闭中最多进行一次安全恢复，否则交给有界收尾。 |
| 同上 | `armClosingProgressDeadline(stage:)` / `cancelClosingProgressDeadline()` | 为当前关闭轮次绑定 generation、任务身份和工作所有权；退出 saving 即撤销，旧 deadline 不能释放新轮次。 |
| 同上 | `receiveNaturalInputState` 及 finish/close/checkpoint 回调 | 在仍有真实关闭工作时续期同一预算；重复 ended 回调不能把较后阶段倒退为 saving。 |

未修改后端业务代码、Live 音频链路、B7 语义过滤、历史 UI 仲裁或本轮覆盖摘要规则。

### 3.2 测试与 UIQA 接线

| 文件 | 内容 |
|---|---|
| `DreamJourneyTests/OwnerTruthContractsTests.swift` | 新增 SV-01 真实组合红绿测试、无响应期限/迟到结果测试、saving 下文字入口与旧场隔离测试；稳定化等待 Controller UI 真正提交后的断言。 |
| `DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift` | 仅 UIQA 客户端补齐生产已要求的 fresh authority 协议接线，使用隔离 FeatureGate policy，不使用恒 true gate；生产默认仍 fail closed。 |

## 4. 修前与修后证据

### 修前红测

- 结果包：`evidence/pre-fix/SV-01-red-v3.xcresult`
- 结果：1 个业务用例失败。
- 关键事实：Controller/Coordinator/临时磁盘/真实 UseCase/BackendClient/FeatureGate/URLProtocol 装配下，首次 current GET 失败；停止后保持 saving，后续 current/start/append/end/ACK/admit/status 请求数均没有合法推进。
- `SV-01-red.xcresult`、`SV-01-red-v2.xcresult` 是测试选择环境错误，不作为业务红测证据。

### 修后同断言绿测

- `evidence/post-fix/SV-01-green-v2.xcresult`：同场恢复后到达 pendingReview；current GET 2 次，start/end/ACK/admit/status 各 1 次，append 2 次，无替代场次或重复命令。
- `evidence/post-fix/SV-02-green.xcresult`：current GET 永不返回时，关闭期限后退出 saving；零业务写；迟到 200 被丢弃，磁盘恢复坐标保留。
- `evidence/post-fix/SV-13-duplicate-ended-regression-green.xcresult`：重复 ended continuation 不再使 queued 倒退为 saving。
- `evidence/post-fix/SV-14-text-entry-green-v2.xcresult`：saving/待核实时真实 Controller 的文字入口可见并可打开，且不复用旧场命令。

## 5. SV-01 至 SV-16

完整逐项清单见同目录 `SV-01-SV-16执行清单.md`。结论：16 项本地门禁均为 `PASS`；真实 iPhone 复测统一为 `NOT_RUN`，不由本地结果替代。

主要组合结果：

| 结果包 | 结果 |
|---|---|
| `SV-03-16-targeted-regression-v4.xcresult` | 51/51 PASS |
| `OwnerTruth-full-regression.xcresult` | 509/509 PASS |
| `DreamJourneyTests-full-regression-v3.xcresult` | 672/672 PASS |
| `SV-07-b8s01-checkpoint-stability.xcresult` | 原检查点用例连续 3 次 PASS |
| `SV-10-checkpoint-classification-stability.xcresult` | 原分类用例连续 3 次 PASS |
| `SV-02-deadline-controller-stability-v2.xcresult` | 新 deadline + Controller 提交断言连续 5 次 PASS |

中间的 `OwnerTruth-full-regression-v2` 和 `DreamJourneyTests-full-regression-v2` 各出现一次异步观察竞态；单测重复稳定通过，并在修正等待 Controller UI 提交条件后由最终全量包 672/672 关闭。没有降低业务断言。

## 6. 模拟器 UIKit/UIQA

模拟器：iPhone 17 Pro，iOS 26.5，UDID `67D3337E-0623-4578-9479-31F4CD9033DA`。

| 场景 | 证据 | 结果 |
|---|---|---|
| saving 下文字入口 | `evidence/post-fix/UIQA-owner-truth-text-entry.json` | 入口可见、sheet 展示；未启动 Live、未触发持久业务写 |
| 冷启动进程 A 写入恢复坐标 | `evidence/post-fix/UIQA-b6-cold-start-seed-green.json` | PID 13136；同一 workflow；坐标保留 |
| 冷启动进程 B 只读恢复 | `evidence/post-fix/UIQA-b6-cold-start-recover-green.json` | PID 13229；1 次 status GET；无麦克风、无新 capture、只读 adapter；pendingReview |
| 恢复页面截图 | `evidence/post-fix/UIQA-b6-cold-start-recover.png` | 显示“上次对话已进入待确认记忆” |
| UIQA 编译 | `evidence/post-fix/UIQA-simulator-build-v4.xcresult` | PASS |

`UIQA-b6-cold-start-seed-failed.json` 保留了 UIQA 旧固定授权与生产 fresh-authority 合同不兼容的首次失败；修正测试接线后使用隔离、账号绑定的 FeatureGate policy 通过。它不是生产业务失败证据。

## 7. 构建结果

| 目标 | 结果包 | 结果 |
|---|---|---|
| Any iOS Simulator Device | `evidence/post-fix/build-generic-simulator.xcresult` | succeeded，0 error，58 warning |
| Any iOS Device | `evidence/post-fix/build-generic-ios-device.xcresult` | succeeded，0 error，77 warning |

警告为工程和依赖已有的 deprecated/sendability 等警告；本轮无编译错误。

本轮实际命令形态（所有命令均使用 `DreamJourney.xcworkspace`）：

```bash
xcodebuild -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' \
  test -only-testing:DreamJourneyTests/OwnerTruthContractsTests \
  -resultBundlePath <evidence>/OwnerTruth-full-regression.xcresult

xcodebuild -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' \
  test -resultBundlePath <evidence>/DreamJourneyTests-full-regression-v3.xcresult

xcodebuild -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'generic/platform=iOS Simulator' build \
  -resultBundlePath <evidence>/build-generic-simulator.xcresult

xcodebuild -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'generic/platform=iOS' build \
  -resultBundlePath <evidence>/build-generic-ios-device.xcresult
```

构建产物：

- Simulator executable SHA-256：`6c984d2f3e22ded9b82f56a0808b94015dc5279831ac29f3cfacd885e062f4eb`
- Generic iOS device executable SHA-256：`08684c2e1a5f081e3b33bf07a04a20905a8c20ab11e914f5757bdabe085793cf`

## 8. 最终源码指纹

| 文件 | SHA-256 |
|---|---|
| `OwnerTruthContracts.swift` | `f9f83cc139187a766f739b3f32e073869bdb915fa49bad06e0f1fd5f3804b133` |
| `EchoViewController.swift` | `9e13b09ad7dda526816068e0a65ae8ee25e6d2118640b74f91d8289c87d1db48` |
| `MemoryArchiveViewController.swift` | `d57d8ddf36d6f5d94e4bdf9840fa17f3827ff2d3ba84610d9688ed215f9a2531` |
| `OwnerTruthContractsTests.swift` | `842603593a9e88be9984b4267c80cb90b08beee49f918f5e83d5c6d79c705e13` |

## 9. 部署、残余风险与回退

- 后端：本轮无改动，不需要后端部署。
- iOS：需后续授权安装修复构建后，才能关闭现场缺陷。
- 残余风险：真实 iPhone 的系统网络调度、真实账号策略时序及现场手动停止后的视觉状态转换尚未运行；因此只标 `DEVICE_PENDING`。
- 真机最小验收停止条件：若停止后超过既有关闭预算仍停留 saving，或产生重复 start/append/end/ACK/admit，立即保存 trace/attempt/本场坐标并停止后续依赖测试。
- 局部回退：仅回退 `resumeCurrentSessionReadForClosingIfSafe`、closing progress deadline、重复 ended 阶段守卫及本轮对应测试/UIQA 接线代码块。不得回退 B6/B8 只读恢复、未知写禁止重放、fresh authority、检查点或磁盘保留逻辑。

## 10. 最终状态

`LOCAL_PASS / DEVICE_PENDING`

未执行：真实 iPhone、生产网络、生产数据、部署、历史任务处理、commit、push，均为 `NOT_RUN`。
