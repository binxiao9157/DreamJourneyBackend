# Astra 本地短场门禁执行命令

仅合成输入与本机模拟器。生产源码和原 run-02 证据均未修改。短场専用副本、diff、VFS overlay 都保存在本目录。

1. PostgreSQL 16 initdb: `/Volumes/Postgres-2.9.4-16/Postgres.app/Contents/Versions/16/bin/initdb -D /private/tmp/astra-short-gate-pg-20260921 -A trust -U astra_local`
2. PostgreSQL start: `pg_ctl -D /private/tmp/astra-short-gate-pg-20260921 -o '-h 127.0.0.1 -p 55439 -k /private/tmp' -w start`。
3. `xcodebuild -workspace DreamJourney.xcworkspace -scheme DreamJourney -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' -derivedDataPath /private/tmp/DreamJourneyDerivedDataCaptureFix CODE_SIGNING_ALLOWED=NO 'OTHER_SWIFT_FLAGS=$(inherited) -vfsoverlay <本目录>/swift-overlay.json' -resultBundlePath <本目录>/build-escalated.xcresult build-for-testing`。
4. 使用 `env -i` 清空继承环境，仅注入系统PATH、本地 DATABASE_URL、synthetic DEEPSEEK_API_KEY、PYTHONDONTWRITEBYTECODE=1；backend `.venv/bin/python -B <本目录>/cap15_short_gate_server.py --admin-dsn postgresql://astra_local@127.0.0.1:55439/postgres --port 58639 --config /private/tmp/astra-cap15-short-config.json --evidence <本目录>/server-evidence.json`。未读取真实 .env，控制模型是进程内 httpx.MockTransport。
5. 将 DerivedData 的 xctestrun 复制到 `/private/tmp/astra-cap15-short.xctestrun`，替换 `__TESTROOT__` 为原 Products 绝对路径，并将本地合成认证JSON作为base64注入测试 `EnvironmentVariables.DJ_CAP15_BIDIRECTIONAL_CONFIG_JSON_BASE64`。不打印认证值。
6. `xcodebuild test-without-building -xctestrun /private/tmp/astra-cap15-short.xctestrun -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' -only-testing:DreamJourneyTests/OwnerTruthContractsTests/testAstraCAP15ShortGateRealBackendPostgresResponses -resultBundlePath <本目录>/short-gate.xcresult`。
7. 使用 `xcrun xcresulttool get test-results summary --path <本目录>/short-gate.xcresult` 留存原始结果摘要。
8. `pg_ctl -D /private/tmp/astra-short-gate-pg-20260921 -m fast -w stop`；服务器完成验证后自行正常退出，退出码0。未卸载共享的Postgres卷。

首次沙箱运行不能访问CoreSimulator/创建共享内存；随后局部授权重试成功。完整日志保留，未将环境阻断误写为产品缺陷。
