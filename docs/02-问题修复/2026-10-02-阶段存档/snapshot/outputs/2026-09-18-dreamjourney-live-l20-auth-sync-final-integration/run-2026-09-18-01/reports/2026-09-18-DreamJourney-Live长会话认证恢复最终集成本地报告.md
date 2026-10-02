# DreamJourney Live 长会话认证恢复最终集成本地报告

日期：2026-09-18\
状态：`LOCAL_PASS / DEVICE_NOT_RUN`

## 1. 范围与基线

- 按《2026-09-18-Astra-Live长会话反复失败-独立复核与局部修复指导》第 6 节，在现有未提交工作树上集成。
- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`。
- 后端 HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`。
- 保留历史 UI 仲裁、Live 音频、B7、覆盖摘要及其他前序未提交修改；未 reset/clean，未整文件覆盖。
- 本轮未修改后端。后端既有未提交文件 `tests/test_owner_truth_interview_input_api.py` 保持原状，不计入本轮成果。

## 2. 实际根因与局部修复

### 2.1 已确认产品缺口

认证拒绝后的只读核实能够确认原第 49 段未应用，但恢复流程在取得新鲜请求授权和实际重试曝光前就领取唯一重试名额。恢复准备失败随后又会被当作原 append 的 `notSent`，试图把磁盘状态从 `authenticationRejected` 回退为 `preparedNotExposed`。这会消耗重试预算、混淆原写事实并阻止原场次继续排空。

修改：

- `EchoLiveMemoryCaptureCoordinator.prepareVerifiedAuthenticationRetry`：只验证磁盘中的原命令、拒绝证据、场次和未领取资格，不提前 durable claim。
- `EchoLiveMemoryCaptureCoordinator.ensureNaturalInputSession` 的 `willExposeLiveTurnCommand`：在 fresh authority/current-session 前置条件通过后、网络曝光前领取唯一重试名额，再写 `authenticationRetryExposed`。
- `EchoLiveMemoryCaptureCoordinator.receive`：恢复准备失败只结束本次准备，保留 `authenticationRejected`、正文、原命令和拒绝证据；已曝光重试收到 `notSent` 时继续保持只读核实边界，不回退也不产生第三次 POST。
- `OwnerTruthInterviewLiveTurnOutboxStore.prepareAppendExposure`：重试曝光必须由 `authenticationRejected + claimed + matching evidence` 共同授权。

### 2.2 本轮额外发现并修复的真实边界缺口

重试期间 fresh decision 已明确 deny 时，delivery-status 策略刷新回调即使返回完成，上层没有再次确认当前策略是否真的可用，递归形成刷新循环。修前测试观察到 857 次刷新。

修改：

- `EchoLiveMemoryCaptureCoordinator.verifyDeliveryStatusIfNeeded`：刷新完成后重新校验 `naturalInputPolicyAvailable()`；仍为 deny 时有界结束为 `statusUnknown`，保留原未决记录，零重试 POST。

### 2.3 测试装配校正

- 长会话凭据 successor 改为在刷新发生时按当时逻辑时钟签发，保留用户、family、parent、递增版本和 CAS 身份。
- 70/48 用例在越过第 49 段时，确定性等待受控 401 已命中且只读 delivery-status GET 已挂起，再推进后续 22 段；不依赖调度偶然性。
- 明确 deny 用例先建立真实 start/已确认正文，再使策略过期并拒绝下一段。
- 其余旧用例补齐 fresh decision 和有界异步等待，不使用固定 sleep 或全局常开 gate。

### 2.4 迟到策略回调证据补强

- 新增同账号对照：首次 append 收到可验证的认证拒绝、只读核实未观察到写入并等待策略刷新时，先恢复有效策略，再返回迟到回调；恢复链以原 `commandID/messageID` 进行唯一一次合法重试并回到 `live`，未生成替代命令。
- 强化账号切换场景：同样先恢复有效策略，再切换账号后返回迟到回调。回调获得主队列有界处理机会后，start、append、end、current-session、delivery-status、ACK、admit 请求计数均不增加；重试名额未领取，原磁盘 command 和 `authenticationRejected` 状态保持不变。
- 用 XCTest expectation 的主队列完成信号替换原固定 `0.1` 秒等待，使断言依赖明确的回调处理机会，而非时间猜测。
- 两项均由现有业务实现直接通过，未发现需要修改业务源码的新缺陷；本次补强仅修改测试装配和请求计数观察点。

## 3. 红绿证据

### 产品红 → 绿

- 原产品红：`/Users/gaominge/Documents/liftora/outputs/2026-09-18-live-l20-second-review/recovery-eventual-completion-red-bound.xcresult`。恢复准备失败后状态非法回退，原场次停在确认水位 48。
- 最终绿：`evidence/tests/final-targeted-13.xcresult`，13/13 通过。覆盖原 9 个失败方法、恢复准备先失败后凭据恢复并完成 70 段、以及 deny/迟到回调/曝光后 notSent。
- 长链与 durable 边界：`evidence/tests/longchain-and-durable-boundaries.xcresult`，4/4 通过。

### 新发现 deny 风暴红 → 绿

- 修前：`evidence/tests/auth-retry-narrow-boundaries-final.xcresult`。明确 deny 用例失败，实际刷新计数 857；另外两项通过。
- 修后：`evidence/tests/auth-retry-narrow-boundaries-green.xcresult`，3/3 通过；deny 只刷新一次，零重试 POST，原记录仍是未领取的 `authenticationRejected`。

### 迟到回调补充证据

- 两场景最终配对：`evidence/tests/late-refresh-policy-account-pair-v2.xcresult`，2/2 通过。
- 更新后的最终定向组合：`evidence/tests/final-targeted-14-late-refresh-supplement-v2.xcresult`，14/14 通过；包含原 13 项、新增同账号恢复对照及完整零新增请求断言。

### 不作为产品红的装配证据

- `auth-retry-narrow-boundaries.xcresult`、`auth-retry-narrow-boundaries-rerun.xcresult`：早期测试预置没有建立当前进程绑定回执，未进入目标重试路径。
- `ownertruth-full-final.xcresult`：520/521 通过；唯一失败是夹具在受控 401 到达前继续推进逻辑时钟。确定性等待校正后，同一全量范围转绿。

## 4. 最终验证结果

| 验证 | 结果 | 原始证据 |
|---|---:|---|
| 最终定向组合 | PASS，14/14 | `evidence/tests/final-targeted-14-late-refresh-supplement-v2.xcresult` |
| 迟到策略回调配对 | PASS，2/2 | `evidence/tests/late-refresh-policy-account-pair-v2.xcresult` |
| OwnerTruth 全量 | PASS，521/521 | `evidence/tests/ownertruth-full-green.xcresult` |
| 账号/Echo/音频保持性 | PASS，51/51 | `evidence/tests/selected-echo-audio-account-51.xcresult` |
| 模拟器无签名构建 | PASS | `evidence/builds/simulator-unsigned.xcresult` |
| 通用 iOS 无签名构建 | PASS | `evidence/builds/generic-ios-unsigned.xcresult` |
| `git diff --check` | PASS | 无输出 |
| 真机、真实 SDK 顺序、物理 20 分钟 | NOT_RUN | 本轮明确不包含真机 |

最终关键行为：

- 恢复准备先失败不会消耗唯一额外发送名额，也不会回退原 append 的磁盘事实。
- 凭据恢复后先只读核实，再以原 command 恢复第 49 段；从 48 排空到 70。
- 同一场次只发生一次 end、一次 ACK、一次 admit，并完成同场 status 读取。
- 明确 deny 不循环刷新、不发送重试 POST；账号变化后的迟到回调不领取名额、不发送；已曝光后的 `notSent` 不产生第三次 POST。
- 同账号且策略已恢复时，迟到回调能够继续原命令；恢复策略后再切换账号时，迟到回调仍被账号租约隔离，且不会发出任何新增恢复请求。
- 未知业务写继续禁止自动重放；原 command、正文、场次、证据和一次额外尝试预算未放宽。

## 5. 修改文件与函数

本轮集成涉及：

- `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
  - `OwnerTruthInterviewLiveTurnOutboxStore.prepareAppendExposure`
- `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Modules/Echo/EchoViewController.swift`
  - `EchoLiveMemoryCaptureCoordinator.ensureNaturalInputSession`
  - `receive`
  - `verifyDeliveryStatusIfNeeded`
  - `prepareVerifiedAuthenticationRetry`
- `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourneyTests/OwnerTruthContractsTests.swift`
  - 原 9 项装配校正、70/48 helper、失败后恢复反例及三项安全边界测试

这些文件同时包含前序未提交成果；本报告仅声明上述局部代码块，不把整文件 diff 归于本轮。

本次迟到回调补强仅修改 `DreamJourneyTests/OwnerTruthContractsTests.swift`：新增同账号恢复对照、强化账号切换断言，并为测试客户端增加 scoped current-session 请求计数。`OwnerTruthContracts.swift` 与 `EchoViewController.swift` 未发生本次新增变化。

## 6. 指纹与差异判断

- `OwnerTruthContracts.swift`：`850ca0ead0055f1d019f14695199ee553ca72dfbbd0dff61d39bd278ff52bd04`
- `EchoViewController.swift`：`1a996e666e1d5d446a8e7585e7d272ba3e08dbe5abec4d126ba4b07fa90ecb79`
- `OwnerTruthContractsTests.swift`：`86a78b45ce27752d23fca4ada7ed24825e5802a2aadaee99158aa951fa7baffd`
- `git diff --check`：PASS。
- 后端：本轮无合同或业务代码变化，无需为本项重新部署后端。

## 7. 残余风险、发布与回退

残余风险：真实 iPhone、真实 SDK 事件顺序、真实网络凭据轮换和物理 20 分钟长会话尚未执行，因此现场缺陷不能据本地结果直接关闭。

发布判断：当前可进入后续由用户主动发起的真机验收；本轮不部署、不安装。后续 iOS 发布应包含上述三个文件的局部变更，后端不需要本项新增发布。

局部回退：仅回退上述四个 Coordinator/Store 代码块及对应测试校正；不得回退前序持久化、B8/B6、Live 音频、B7、覆盖摘要或未知写保护。回退会重新引入“恢复准备提前耗尽重试名额”和 deny 刷新循环，因此发布后如需止损，应停用该版本而不是清理磁盘恢复记录或重发历史写入。

## 8. 停止点

`LOCAL_PASS / DEVICE_NOT_RUN`

未连接、检测、安装或启动 iPhone；未部署；未访问生产或历史数据；未清理历史任务；未 commit/push。
