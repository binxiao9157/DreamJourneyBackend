# DreamJourney FM-POLICY-01 五项遗漏补充修复报告

日期：2026-09-14

## 1. 结论与边界

本轮五项本地缺口均已用修复前反例和修复后相同业务断言闭环，状态恢复为：

**`A_LOCAL_PASS / READY_FOR_FM_POLICY_DEVICE_RETEST`**

该结论仅代表本地代码、自动化、模拟器 UIQA 和构建门禁通过。FM-POLICY-01 现场缺陷仍为 **FAIL**，修复版本真机复测为 **NOT_RUN**。本轮没有部署、没有安装或测试 iPhone、没有访问生产业务数据、没有 commit/push，也没有修改 B6 冷启动恢复或 Live 模型链路。

## 2. 基线

- iOS：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
  - branch：`feature/prd-stitch-ui-adaptation`
  - HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- Backend：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`
  - branch：`main`
  - HEAD：`a25b993922fc90dde1e689d19e51becb68fccdbf`
- 既有未提交修改全部保留。本轮只修改 iOS 的正式记忆只读恢复相关代码和测试；后端脏文件属于前序 B4 工作，不归因于本轮。
- Xcode：26.6 (17F113)；Simulator：iPhone 17 Pro / iOS 26.5。

## 3. 五项修复映射

| 要求 | 已证实根因 | 文件/函数 | 测试与原始证据 | 状态 |
| --- | --- | --- | --- | --- |
| P1 共享策略恢复不受首个页面取消影响 | 共享物理策略请求直接持有首个等待者的 attempt state；首个页面取消会让后续 401 认证恢复被取消状态截断 | `DreamJourneyBackendClient.swift`：`CandidatePolicyRefreshWaiter`、`CandidatePolicyRefreshFlight`、`refreshCandidateInboxPolicy`、`absorbRecoveryProgress` | 红：`FM-policy-five-gaps-red.xcresult`；绿：`FM-policy-supplement-targeted-03.xcresult` 中 P1 两项 | PASS |
| P2 整体期限贯穿 runtime 恢复 | 通用 `fetchRuntimeConfig` 没有携带原读取的 deadline/cancel/预算；迟到 200 会更新运行配置并继续资源 GET | `DreamJourneyBackendClient.swift`：`fetchRuntimeConfigForOwnerTruthRead`、`requestJSON`、`CandidateInboxReadAttemptState` | 红：`FM-policy-runtime-deadline-red-v2.xcresult`；绿：定向结果包 P2；断言迟到 runtime 后资源 GET=0 | PASS |
| P3 页面生命周期取消/解绑 | 三个真实 controller 没有在离开、后台或销毁时统一取消并作废 UI 所有权，返回时也没有可靠重读 | `OwnerTruthFormalMemoryViewControllers.swift`：三个 controller 的 `viewWillAppear`、`viewDidDisappear`、前后台通知和 `detachReadForLifecycle` | 红：`FM-policy-five-gaps-red.xcresult`、`FM-policy-strengthened-red.xcresult`；绿：定向结果包 P3 | PASS |
| P4 搜索防抖立即作废旧结果 | generation、handle 和分页游标直到 350ms 防抖结束才替换，窗口内旧响应仍可提交 | `OwnerTruthFormalMemoryListViewController.updateSearchResults` | 红：`FM-policy-strengthened-red.xcresult`；绿：定向结果包 P4；旧响应 UI commit=0 | PASS |
| P5 详情重读失败撤销旧编辑依据 | 重读开始/失败仍保留旧 `detail`；错误标签已被旧渲染移出 arrangedSubviews，用户可能看不到错误并继续编辑 | `OwnerTruthFormalMemoryDetailViewController.load`、`showReadStatus`、`editTapped`、`failClosedForAccountChange` | 红：`FM-policy-five-gaps-red.xcresult`；绿：定向结果包 P5；旧 editor push=0 | PASS |

## 4. 实现说明

### 4.1 独立共享 flight 与等待者所有权

策略 single-flight 现在拥有独立的 trace、期限和恢复状态。每个页面等待者保留自己的取消、deadline 和读取所有权；一个等待者退出只会使自己的 completion 失效。物理 flight 的 401 认证恢复不依赖首个页面的取消状态。

flight 完成时只向仍有效的等待者传播最终 attempt/认证恢复进度。所有等待者都失效后，新读取可建立新 flight；旧 flight 通过 UUID fencing 不能移除、释放或覆盖新 flight。等待者数量不会增加策略、认证或资源 GET 的上限。

### 4.2 runtime 恢复纳入原读取意图

正式记忆只读请求的 runtime 恢复使用同一 trace、单调 attempt、共享恢复预算和原 deadline。任务创建前、响应处理后、配置采用前及继续资源 GET 前都检查 attempt state 和账号租约。

过期或取消后的 runtime 200 不会更新恢复配置，也不会继续发送正式记忆资源 GET。原 completion 由整体完成门在期限到达时只结束一次；迟到回调不能释放新请求所有权。

### 4.3 三个真实页面生命周期

正式记忆列表、详情和人物记忆归纳页面均显式持有读取 handle/read ID/generation：

- 导航离开或进入后台：取消本页消费者、递增 generation、释放 loading，并标记返回后重读。
- 返回或前台恢复：只在当前账号、当前页面范围内发起新读取。
- 销毁：移除观察器并取消当前 handle。
- 旧回调必须同时匹配 read ID、generation 与账号租约才能提交 UI。

没有恢复 `handle.deinit` 自动取消，因此旧兼容入口即使丢弃返回句柄，也不会被意外终止。

### 4.4 搜索和筛选的立即失效

搜索文字真正发生变化时立即取消旧读取、清空旧列表/分页游标/发布选择、递增 generation，并显示“正在准备新的搜索...”；350ms 仅用于延后新请求，不再延后旧 UI 权失效。

UIQA 另外发现 UIKit 在退出搜索状态时可能回调相同文字。若无实际搜索变化，现实现不会清空发布选择，避免公开副本流程被误伤。

### 4.5 详情失败后的只读保护

每次详情重读开始先撤销旧 `detail` 和编辑依据。失败后重新把可见错误标签加入真实 stack；旧内容和旧编辑闭包均不能继续使用。恢复成功后才从新响应建立新的 detail，用户仍需重新触发编辑操作。

账号或 authority 失效时继续 fail closed：清理当前详情与操作入口，不向新主体显示旧内容。

## 5. 红绿与回归

### 红测

- `evidence/pre-fix/FM-policy-five-gaps-red.xcresult`：P1、P3、P5 失败。
- `evidence/pre-fix/FM-policy-strengthened-red.xcresult`：强化后的 P3、P4 失败。
- `evidence/pre-fix/FM-policy-runtime-deadline-red-v2.xcresult`：P2 失败，证明期限后仍出现资源 GET。

未能证明业务问题的早期 fixture/工具失败仍保留，但未作为红测结论。

### 绿测

- `evidence/post-fix/FM-policy-supplement-targeted-03.xcresult`：11/11 PASS。
- `evidence/post-fix/ownertruth-audio-full-regression-03.xcresult`：434/434 PASS，0 FAIL，0 SKIP。
- 后端相关保持性门禁：62/62 PASS；本轮无后端业务变化。

覆盖内容包括首等待者取消后策略 401、全部等待者取消后的新 flight、runtime 迟到 200、三页面离开/返回/销毁重建、搜索防抖乱序、详情成功后失败、旧 editor 拒绝、同 trace 与最终 attempt、候选共享恢复及音频保持性。

## 6. UIQA 与编译

- 最终 UIQA：`evidence/post-fix/formal-memory-uiqa/fm-policy-supplement-20260914-03/`
  - 列表、详情、人物公开副本编辑、预览均 PASS。
  - 四张截图人工检查为非空、文字可读、无重叠。
- 模拟器 Debug：`build/simulator-build.xcresult`，成功，0 error。
- 通用 iOS 设备目标：`build/generic-ios-device-build.xcresult`，成功，0 error。
- 最终回归环境与统计可由 xcresult 复核，详见 `evidence/post-fix/test-build-gate-summary.md`。

## 7. 诊断链示例

以下为合成测试中的白名单语义序列，不含正文、token、请求头或原始业务 hash：

```text
trace=<random> attempt=1 stage=featureGate event=requestDenied reason=expiredPolicyCache
trace=<random> attempt=1 stage=policyRefresh event=policyRefreshJoined
trace=<flight-random> attempt=1 stage=policyTransport event=taskCreated
trace=<flight-random> attempt=1 stage=policyTransport event=taskResumed
trace=<flight-random> attempt=1 stage=http event=responseReceived status=401
trace=<flight-random> attempt=2 stage=authentication event=recovered
trace=<flight-random> attempt=2 stage=http event=responseReceived status=200
trace=<random> attempt=2 stage=policyRefresh event=completed
trace=<random> attempt=3 stage=resourceTransport event=taskResumed
trace=<random> attempt=3 stage=typedDecode event=completed
trace=<random> attempt=3 stage=uiCommitted event=success
```

物理共享 flight 使用独立 trace；每个读取意图自己的 trace 贯通门禁、恢复完成、资源 GET、解码与 UI。最终 UI 使用实际成功 attempt，而不是默认重置为 1。

## 8. M01-M28 / W0-W6 校正

修订清单见 `reports/M01-M28-W0-W6修订清单.md`。

上一轮 M13/M14、M19、M28 仅凭已有接线或间接保持性证据标 PASS，证据不足。本 run 先把整体状态降为 `A_LOCAL_INCOMPLETE`，补充真实反例后重新建立 PASS：

- M13/M14：共享 flight 独立生命周期和首等待者取消。
- M09/M10/M12：deadline/cancel 贯穿 runtime 恢复。
- M17/M18：防抖前立即作废 UI 与分页状态。
- M19/M20：三个真实 controller 的导航/前后台/重建。
- M28：详情成功后失败的可见错误和旧编辑撤销。

M24 的隔离 PostgreSQL 零写证据沿用 run-01；本轮没有后端合同变化，因此没有重复启动数据库。该项在修订清单中明确标为“历史证据复核”，不冒充本 run 新执行。

## 9. decision-result 部署证据校正

上一轮报告写“两个 decision-result 接口尚未部署”，与 9 月 14 日留存证据冲突，现校正如下：

- 留存发布报告记录 API 镜像 `dreamjourney-b4-correction:20260914-0055`，镜像 ID `sha256:cf4b531c6f5cfddbd29e7b6a15eddbb8120c3ce04e9a9f10a58cddd713e28115`。
- 同一报告记录两个 decision-result 路由已随 API 发布并通过鉴权分类探针。
- 本轮明确禁止访问生产，因此没有重新探测当前在线镜像或接口；当前运行状态标为 **待现场复核**，不据此重复部署。
- 接口已有发布证据不等于 unknown 写结果恢复已完成真机验收，两者继续分开记账。

证据来源：`/Users/gaominge/Documents/liftora/outputs/2026-09-14-dreamjourney-b4-correction-preview-device-retest/run-2026-09-14-01/reports/2026-09-14-DreamJourney-B4更正预览与真机闭环复测报告.md` 与相邻 `evidence/device/deployment-postcheck.txt`。

## 10. 部署判断、残余风险与停止点

- 本轮是 iOS 局部修复，无后端业务代码或合同变化，无迁移需求，不需要后端部署。
- 生产历史失败的精确现场层仍需修复版本真机日志与服务端只读证据确认；本地通过不能替代现场通过。
- 策略共享 flight 的并发边界已由受控网络验证，但真机弱网、系统挂起和真实策略 401 组合尚未执行。
- 第三方 SDK/旧源码编译警告仍存在，不属于本轮缺陷。
- FM-POLICY-01 现场：FAIL。
- 修复版本真机复测：NOT_RUN。
- B4、B4-8、B6 及其他专项：沿用各自最新证据，本轮不升级或降级。

当前停止在 **`A_LOCAL_PASS / READY_FOR_FM_POLICY_DEVICE_RETEST`**，等待用户另行授权安装与真机复测。

## 11. 局部回退

如必须回退，只反向撤销本轮三个文件中的局部改动：

1. BackendClient 的独立 policy flight、runtime-aware read 恢复和 attempt 合并。
2. 三个正式记忆 controller 的生命周期、搜索立即失效、详情失败保护及 UI 诊断。
3. 本轮 P1-P5 回归测试与 QA-only 页面时序钩子。

不得整文件覆盖、不得 `git reset`，也不得回退前序候选审核、typed 差异、关联组、CAS、Binding、Live 或 B6 修改。回退会重新暴露本报告记录的五项缺陷，因此仅在出现更高优先级回归时使用。
