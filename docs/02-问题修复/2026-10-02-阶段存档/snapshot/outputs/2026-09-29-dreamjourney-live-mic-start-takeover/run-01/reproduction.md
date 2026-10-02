# 复现入口与执行边界

仅本地合成数据。原始产物目录不可覆盖，重跑时另建输出目录。

## 受控生产 Manager 专项

XcodeBuildMCP session 默认 workspace `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney.xcworkspace`，scheme `DreamJourney`，Simulator `67D3337E-0623-4578-9479-31F4CD9033DA`，DerivedData 使用新目录。

`test_sim` 参数：

- `extraArgs` 首项：`SWIFT_ACTIVE_COMPILATION_CONDITIONS=DEBUG UI_QA_SIMULATOR LIVE_MANAGER_CONTROLLED_SDK`
- 对 OwnerTruthContractsTests 所有 `func testMIC...` 逐项传 `-only-testing:DreamJourneyTests/OwnerTruthContractsTests/<name>`（本轮39项，名单见 nativefinal02-tool-result.json）。
- `-parallel-testing-enabled NO`、`-resultBundlePath <新目录>`。
- `testRunnerEnv: {"DJ_ISOLATED_UNIT_TEST_HOST":"1"}`。

标准完整回归换另一个 DerivedData，去掉受控SDK编译flags和only-testing；保留隔离host环境。3个无配置的四场入口应单独跳过，再用下方集成runner实际执行。全套结果见 allfinal02-tool-result.json，不把受控SDK配置冒充普通配置或真实供应商。

## 通用iOS构建

在iOS仓库执行，目标为generic，不连接手机：

```sh
xcodebuild build -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'generic/platform=iOS' -derivedDataPath '<新目录>/generic-ios' \
  -jobs 2 CODE_SIGNING_ALLOWED=NO
```

第二次清理修复后的最终同源构建见generic-ios-final-02.log。首次普通并发构建exit65日志保留在generic-ios-build.log；同源、同目录限并发重建exit0见generic-ios-rebuild.log。首次报若干SwiftCompile“exit code 0 but produced no further output”和缺少Info.plist；没有通过改源码绕过。精确Xcode内部首因未证明，不称永久解决工具链异常。

## 后端与四场

后端仓库 `/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`：

```sh
STORE_BACKEND=memory .venv/bin/python -m unittest \
  tests.test_voice_launch_diagnostics tests.test_realtime_voice_proxy

.venv/bin/python \
  /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/tools/run_round_simulation.py \
  --admin-dsn postgresql://djtest@127.0.0.1:55520/postgres \
  --output-root '<新目录>/four-scene-final' --scenario all
```

四场runner使用独立API与Worker进程、临时随机名数据库、默认入口和loopbackHTTP模型；拒绝真实Provider环境配置。按short-A→logical20→short-B→logical65，每个短場包含两轮补充；长场分别220/300条用户与助手合计turn，不是220/300次用户发言。短场receipt随源码/配置/构建指纹变化失效。

本次发现专用PG进程已运行（PID1715）。首次沙箱内status误报未运行，提升权限后的只读status/psql确认已运行；重复start因pid锁被拒绝，没有删除pid或强杀。复用该专用端口，由runner创建/清理本轮随机数据库；不因本轮结束擅自停止进入时已有PG进程。

## 未授权外部阶段

后续由用户主动发起部署/真实Provider/真机；先独立短场进入候选并审核到正式记忆，再物理20分钟，未来65分钟。源码或实际依赖变化需要失效旧短场门禁。此次没有操作任何手机、真实模型、生产或历史任务，没有commit/push。
