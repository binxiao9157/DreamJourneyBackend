# DJ-LIVE-START-20260924 本地局部修复交付

状态：**LOCAL_INCOMPLETE**。本轮没有部署、调用真实 Provider、连接 iPhone、处理生产或历史数据，也没有 commit/push。代码局部修复与已列出的回归通过，不等于设计的全部本地门禁通过，更不等于 9 月 24 日现场首因已证实。

## 基线与实际改动

- iOS：`feature/prd-stitch-ui-adaptation`，HEAD `11d0d0051b9be3cce57822dd059472d1e2536866`。进入时已有大量 dirty 文件及三个未跟踪文件，全部保留。本轮只修改 `EchoViewController.swift`、`DreamJourneyBackendClient.swift`、`UserManager.swift` 的 DEBUG 测试入口和 `OwnerTruthContractsTests.swift` 的 MIC 用例。
- 后端：`main`，HEAD `8141ff271228b78c35237f9947f4bf026332af43`。本轮修改 `app/main.py`、`app/services/tokens.py`、`app/services/realtime_voice_proxy.py` 和 `tests/test_credential_response_boundary.py`，未提交。
- 新开麦先捕获当前 FeatureGate 请求决定；仅缓存缺失/过期等可恢复原因允许一次策略刷新，刷新后重验冻结账号租约。当前有效 deny 不发 ticket。Live ticket 的 401 重发仅限经服务端可识别的规范写前鉴权拒绝，非规范 401、503 和未知结果不自动补发。
- 每次启动有随机 attempt、权限等待与启动计时分界、15 秒单调总预算、ticket transport 取消、迟到回调隔离、一次性权限结果认领和 SDK 成功所有权移交。同步 SDK 配置跨截止不会继续 StartEngine。阻断时仅关闭本次新建 capture，不无归属地结束旧场。曝光阶段单调前进：503 回执后迟到的 task-resume 通知不能把 `responseReceived` 倒退为 `taskResumed`。
- 启动诊断按 attempt 有界落盘，保留阶段、首次错误族、系统错误码、HTTP 状态、ticket 曝光与终态；网络错误可穿透 Alamofire 包装。后端仅在 Live ticket 路由记录随机 trace、阶段和本进程单调耗时；`ticketStoreWriteComplete` 与事务退出后的 `ticketStoreCommitted` 分开。日志不含正文、token、原始业务身份或原始业务 hash。

## 红绿与验证

- MIC-01：旧 route capture 过期但当前策略允许时，BackendClient+真实 Gate 修前失败 [`mic01-red.xcresult`](evidence/mic01-red.xcresult)，修后通过 [`mic01-green-initial.xcresult`](evidence/mic01-green-initial.xcresult)；完整 Controller→Client→Gate→受控 transport→SDK/listening 修后通过 [`mic01-controller-render-barrier.xcresult`](evidence/mic01-controller-render-barrier.xcresult)。**严格的同一完整 Controller 链修前红产物尚缺**，不能写成已满足 MIC-01 全部证据门槛。
- MIC-04：策略刷新期间账号代次轮换，修前仍暴露 ticket [`mic04-rotation-red.xcresult`](evidence/mic04-rotation-red.xcresult)，冻结租约后同断言转绿 [`mic04-rotation-green.xcresult`](evidence/mic04-rotation-green.xcresult)。
- MIC-06：重复权限 completion 修前产生 2 个 ticket POST、2 次 SDK start [`mic06-duplicate-red.xcresult`](evidence/mic06-duplicate-red.xcresult)，一次性认领后各为 1 [`mic06-duplicate-green.xcresult`](evidence/mic06-duplicate-green.xcresult)。
- MIC-13：受控 DNS 失败修前只落 `request`、丢失 `NSURLErrorDomain/-1003` [`mic13-network.xcresult`](evidence/mic13-network.xcresult)，局部解包后同断言通过 [`mic13-network-green.xcresult`](evidence/mic13-network-green.xcresult)。
- 其他定向：MIC-02/03/07 [`mic02-03-07.xcresult`](evidence/mic02-03-07.xcresult)、MIC-05 [`mic05-old-sdk.xcresult`](evidence/mic05-old-sdk.xcresult)、规范写前 401 [`mic07-canonical.xcresult`](evidence/mic07-canonical.xcresult)及恢复后 deny [`mic07-recovery-deny.xcresult`](evidence/mic07-recovery-deny.xcresult)、503 未知票据 [`mic08-final.xcresult`](evidence/mic08-final.xcresult)、MIC-09/14 helper [`mic09-14.xcresult`](evidence/mic09-14.xcresult)、真实 Controller ticket 悬挂与确定性截止 [`mic09-stable-timeout.xcresult`](evidence/mic09-stable-timeout.xcresult)、MIC-10 [`mic10-clock.xcresult`](evidence/mic10-clock.xcresult)、MIC-11 永不 `SessionStarted` 的确定性时钟验证 [`mic11-deterministic-green.xcresult`](evidence/mic11-deterministic-green.xcresult) 均通过。账号轮换时悬挂 ticket 的页面归属另有 [`mic04-pending-ticket-rotation-green.xcresult`](evidence/mic04-pending-ticket-rotation-green.xcresult)；原失败试验保留在 `mic09-pending-ticket`、`mic09-timeout-probe`、`mic09-lease-probe`，其实际断点是账号租约失效后的页面未收尾，并非稳定账号超时失败。MIC-08 的曝光倒退在全量 [`ios-final-v6.xcresult`](evidence/ios-final-v6.xcresult) 实测失败，相同 503/1 POST/曝光断言在 [`mic08-exposure-order-green.xcresult`](evidence/mic08-exposure-order-green.xcresult) 转绿。各自覆盖边界见 [MIC 矩阵](mic-matrix.md)。
- 最终冻结版 iOS Simulator：[`ios-final-v10.xcresult`](evidence/ios-final-v10.xcresult)，OwnerTruth + AudioOwnerLease 合计 596 PASS / 0 FAIL / 3 SKIP；通用 iOS 设备目标 `xcodebuild build ... CODE_SIGNING_ALLOWED=NO` 退出 0，没有签名或安装。此前全量失败的精确原因、原始产物与局部处理见 [回归隔离记录](regression-isolation.md)，不能把失败轮次抹掉或说成产品全链已验收。后端在 `env -i`、`STORE_BACKEND=memory` 下，`tests.test_release_policy tests.test_realtime_voice_proxy tests.test_credential_response_boundary` 共 81 PASS；**memory 不等于 PostgreSQL**。两端 `git diff --check` 均通过。
- 后端阶段样例（由合成 ticket 测试的 `assertLogs` 校验，非生产日志）：`voiceLaunch trace=<随机UUID> stage=inbound elapsedMs=<本进程单调毫秒> httpStatus=None`；顺序还覆盖 `ownerAuthorized`、`sessionAuthorized`、`capabilityReady`、`snapshotBound`、`ticketStoreWriteComplete`、`ticketResponsePrepared`、`responseReady`。测试断言不包含合成 token 或 user ID。事务真实 commit 阶段须由隔离 PG 验证。

## 未满足的本地门禁

- **MIC-15/16 与 D5：BLOCKED。**严格隔离 runner 使用 `postgresql://djtest@127.0.0.1:55520/postgres`；实际 runner 预检及本机 `nc -vz 127.0.0.1 55520` 在解除命令沙箱限制后均返回 `Connection refused`。本机无可用 `pg_ctl`/`initdb`/`psql`/Docker/Brew，未启动合成 PG。默认 API lifespan+真实票据 UoW、metrics/auth/pool 有限阻塞与并发 `/live`、事务故障未运行。既有全局 API 停摆风险仍归 [DJ-API-READ-STALL-01](../../../02-问题修复/服务端与认证/2026-09-24-DJ-API-READ-STALL-01-登录后读取停摆与超时误分类-待修复记录.md)，本轮未重构中间件。
- **MIC-18：BLOCKED。**复用原 runner 的 `--scenario all` 在 PG 连接门禁即退出，未启动 short-A→logical20→short-B→logical65；不能复用旧版四场当作本版通过。候选可见、审核、正式记忆、重建读回在本版均 NOT_RUN。
- MIC-01 完整链修前红，MIC-05 的晚到 ticket/离页组合，MIC-06 的多失败竞速，MIC-07 恢复后换号，MIC-08 的丢响应/timeout，MIC-09 策略/runtime/auth 等其他悬挂阶段，MIC-11 SDK 未 ready/启动失败，MIC-12 快路径时序，MIC-13 的 429/解码/snapshot，MIC-14 不可写磁盘与别的请求 latestDecision，MIC-17 完整采集/播放/停止保存，MIC-19 旧场保存同时新场失败等尚未逐项闭环；详见矩阵。缺少这些断言不能把总测试数量视为全覆盖。
- D4 的 policy/runtime/auth 子请求独立序号与后端响应**实际发送完成**证据仍不完整；已有 trace 证明 ticket 路由处理阶段，不能把响应准备当发送或把客户端 task resumed 当服务器收到。

## 历史、发布和回退

9 月 24 日“近一分钟”等待点、23:04 单次失败因果，以及历史 seq5 初始触发原因继续 **UNRESOLVED**。此修复尚未进入生产；真实 Provider、部署、真机短场/物理20分钟/未来65分钟、声学与静音自动化、历史重处理均 **NOT_RUN**。发布需先补本地门禁，再安排后端日志版本与 iOS 版本配套，不能将本地编译当生产发布。

局部回退以本轮函数/代码块为单位：后端仅撤 Live ticket trace middleware 与阶段回调；iOS 仅撤启动 attempt/当前策略准备及对应 DEBUG 接口。回退会重现旧 route capture 阻断风险，须保留原场 Outbox、未知 ticket 保护和全部未提交的其他任务修改；不得 reset 整仓或重放历史。

## 可复制命令与取证

```sh
cd /Users/gaominge/Documents/Codex/Video/DreamJourney_dev
xcodebuild test -quiet -parallel-testing-enabled NO -workspace DreamJourney.xcworkspace -scheme DreamJourney -configuration Debug -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' -derivedDataPath /private/tmp/dj-mic-start-derived -resultBundlePath /private/tmp/dj-mic-recheck.xcresult -only-testing:DreamJourneyTests/OwnerTruthContractsTests -only-testing:DreamJourneyTests/AudioOwnerLeaseModelTests CODE_SIGNING_ALLOWED=NO
xcodebuild build -quiet -workspace DreamJourney.xcworkspace -scheme DreamJourney -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath /private/tmp/dj-mic-start-device-derived CODE_SIGNING_ALLOWED=NO
```

```sh
cd /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend
env -i PATH=/usr/bin:/bin PYTHONPATH="$PWD" PYTHONPYCACHEPREFIX=/private/tmp/dj-mic-pycache STORE_BACKEND=memory .venv/bin/python -m unittest tests.test_release_policy tests.test_realtime_voice_proxy tests.test_credential_response_boundary
```

[指纹](fingerprints.md) · [MIC 矩阵](mic-matrix.md) · [回归隔离记录](regression-isolation.md) · [后续真机清单](device-runbook.md)
