# DreamJourney Live 非 ASR 误登记局部修复

- 状态：`LOCAL_PASS / DEVICE_PENDING`
- iOS 基线：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 后端变更：无
- 生产部署：未执行，且本轮无后端部署需求
- iPhone / 真实 SDK 回调：`NOT_RUN`

交付报告：

- [本地修复报告](reports/2026-09-17-DreamJourney-Live非ASR误登记与真机未封存-本地修复报告.md)
- [D1-01 至 D1-06 执行清单](reports/D1-01-D1-06执行清单.md)

权威证据：

- 修复前：`evidence/pre-fix/D1-01-D1-02-red.xcresult`
- 定向绿测：`evidence/post-fix/D1-targeted-green-v2.xcresult`
- D1 回归：`evidence/post-fix/D1-01-D1-06-regression-v2.xcresult`
- 完整回归：`evidence/post-fix/ownertruth-audio-regression-v5.xcresult`
- 模拟器构建：`evidence/post-fix/simulator-build.xcresult`
- 通用 iOS 设备构建：`evidence/post-fix/generic-ios-device-build.xcresult`

目录中的其他 `v2` 至 `v4` 结果包记录了测试装配稳定化过程，不作为最终通过证据。
