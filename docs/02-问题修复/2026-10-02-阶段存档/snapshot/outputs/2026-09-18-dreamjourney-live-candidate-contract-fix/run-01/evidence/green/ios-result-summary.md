# iOS 结果摘要

## 真实 Controller 组合

- 结果包：`ios-real-composition-test.xcresult`
- 设备：iPhone 17 Simulator，iOS 26.5，arm64
- 结果：`1 passed / 0 failed / 0 skipped`
- 场景：Controller、磁盘 FollowUpStore、FeatureGate evaluator、BackendClient/requestJSON、受控网络；失败结果只读核实，业务写为 0。

## D4 组合

- 结果包：`ios-d4-combination-tests.xcresult`
- 结果：`6 passed / 0 failed`
- 场景：pendingReview/empty 成功清理；terminalFailure/quarantined 与观察写盘失败时保留恢复坐标。

## OwnerTruth 全量

- 结果包：`ios-ownertruth-full-current.xcresult`
- 设备：iPhone 17 Simulator，iOS 26.5，arm64
- 结果：`524 passed / 0 failed / 0 skipped`
- 开始：2026-09-19 01:38:37 +08:00
- 完成：2026-09-19 01:39:01 +08:00

## 构建

- `ios-simulator-build.xcresult`：PASS
- `ios-generic-build.xcresult`：PASS，无签名通用 iOS 设备目标
