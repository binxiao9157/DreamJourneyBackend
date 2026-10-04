# 当前火山会话音色告别修复

方案：[分析与修复设计](../../../02-问题修复/Live语音与开麦/2026-10-04-十分钟告别音色不一致/分析与修复设计.md)。

状态：LOCAL_PASS / PROVIDER_PROTOCOL_PASS；未部署、安装、真机或push。完整结论见[实施与验证](../../../02-问题修复/Live语音与开麦/2026-10-04-十分钟告别音色不一致/实施与验证.md)。

## 证据入口

- red.xcresult / red-summary.json：旧系统朗读实现，实际 Controller → 生产 Manager → 受控 SDK 断言失败（SayHello 仍只有原开场一次，预期再有告别一次）。
- native-green-01.xcresult：首轮 7 项真实 Manager 受控 SDK 回归通过。
- native-green-final.xcresult：随后 8 项回归通过，含拒绝、空音频、取消边界；不是最终新增播放器 PCM 与跨新场回调断言的证据。
- native-final-02.xcresult：7过1失败，第二场一次性测试监听未重注册，失败保留。
- native-final-03.xcresult：修正测试监听后8/8。
- native-final-04.xcresult / native-final-summary.json：最终8/8，进一步在启动阶段绑定真实持久化入口并以正常回答落盘作正向对照。
- full-regression.xcresult：普通 UI_QA_SIMULATOR 完整回归，结果以 full-summary.json 为准。
- provider-farewell-probe.py / provider/result.json / provider/events.json：一个真实供应商合成会话，开场 → 普通回复 → 告别三次输出；音频保存在 provider/。没有修改业务数据。
- pg-regression/pg-result.json / pg-regression.log：默认 API/Worker + 本地隔离 PostgreSQL，short-A → logical10 → short-B 候选、正式确认、重开读取及补缺/幂等保护通过；模型 HTTP 为受控，时间为逻辑时间。
- backend-unchanged.json：与前轮已部署后端修复的 5 个受保护文件指纹完全相同。
- device-build.log / DeviceDerivedData：真实 iOS SDK 分支无签名构建，未安装。
- 最终代码差异、指纹和验收说明见后续 final-fingerprints.json、repair.patch 及实施报告。

## 复现方式

XcodeBuildMCP session：DreamJourney_dev/DreamJourney.xcworkspace、scheme DreamJourney、模拟器67D3337E-0623-4578-9479-31F4CD9033DA。test_sim 统一使用 CODE_SIGNING_ALLOWED=YES、CODE_SIGN_IDENTITY=-、-parallel-testing-enabled NO、testRunnerEnv DJ_ISOLATED_UNIT_TEST_HOST=1。

普通回归编译条件 DEBUG UI_QA_SIMULATOR；生产 Manager 专项额外加 LIVE_MANAGER_CONTROLLED_SDK，选择 OwnerTruthContractsTests/testLiveFarewell* 四项、testMICProductionManagerControlledSDKLifecycle、testMICStopSealsCaptureBeforeNativeStopReturns 及 testLiveTenMinuteControllerFarewellClosesAndPersistsPendingTail、testLiveTenMinuteOldFarewellCannotCloseNewSession。每次使用新 xcresult 路径。不能把 stub SDK 当作真实 SDK/真机结论。

通用设备构建：xcodebuild -workspace DreamJourney.xcworkspace -scheme DreamJourney -configuration Debug -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath <本轮DeviceDerivedData> CODE_SIGNING_ALLOWED=NO build。

PG 使用前轮 pg-grouping.py 原文件（指纹见 backend-unchanged/最终指纹），LOCAL_LIVE_TEST_ADMIN_DSN 仅允许127.0.0.1:55548，LOCAL_LIVE_TEST_OUTPUT 指向新证据目录，STORE_BACKEND=memory；脚本自行创建隔离库，运行本地 API。测试后停止PG并卸载镜像，数据保留。

apply/add/extend 等脚本是本轮修改过程记录，不是可重复执行的日常工具。禁止对最终源码重复应用。
