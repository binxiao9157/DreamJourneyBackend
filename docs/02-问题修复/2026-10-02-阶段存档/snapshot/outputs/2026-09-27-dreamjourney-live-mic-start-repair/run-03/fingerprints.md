> 历史版本说明（2026-09-29追加）：本文件保留当时结果，最新接手收尾与指纹见[唯一最新入口](../../2026-09-29-dreamjourney-live-mic-start-takeover/run-01/README.md)。原文 final-06 的“2 GET/1 GET”描述已在新报告纠正为“恢复任务数2/1”；原始失败产物保留。

# Final local source, build and configuration identity

Verified 2026-09-28 after the final MIC-08 test fixture barrier change. The repositories remain dirty; neither HEAD alone represents the build.

| Identity | Value |
|---|---|
| iOS HEAD | `11d0d0051b9be3cce57822dd059472d1e2536866` |
| Backend HEAD | `8141ff271228b78c35237f9947f4bf026332af43` |
| Protected source fingerprint | `749429978a1525ad7ccebee4c50f1c5d4d7bd7004239e0b2b1fca796d292d32c` |
| Simulator build fingerprint, both 20/65 chains | `8650a00bfc4c80f89c5a92995122c87ede45cc1d35dabf842cdca4d8e6f7e814` |
| Logical20 config fingerprint | `a6fa56f6ab479ea45fb23680568c3175de331fca28e53081e3cf28967ffae545` |
| Logical65 config fingerprint | `20623e8d6eddca7eaecb4550eb10630020c4b9ce8c34cb960efc57c8ff0df5b0` |
| iOS tracked binary diff SHA-256 | `ae63b4d672b74b44d352d30fba1ee0a5425bce06f60257874046bbfbfbe9cd17` |
| Backend tracked binary diff SHA-256 | `460bf0efa03526e04c9c6a53c3edfc104ded81f6ec5bbad356c51894ae8e78eb` |

`evidence/final-source-build-link-03.json` verifies 1,297 protected dependencies, 312 built artifacts and zero artifact mismatches against the final `four-scene-final-03` manifest. `reuseEligibleBySourceAndBuild=false` compares to an older entry fingerprint; the relevant check is `artifactMatchesCurrentSourceAndBuild=true`. The final build changed from `four-scene-final-01/02` because the test target changed, so old receipts are not reused.

## Reproducible local commands

The exact invocation used for the final iOS Simulator bundle (exit 0, 770/0/3):

```sh
cd /Users/gaominge/Documents/Codex/Video/DreamJourney_dev
xcodebuild test -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' \
  -resultBundlePath /Users/gaominge/Documents/liftora/outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-03/evidence/all-ios-final-03.xcresult \
  -parallel-testing-enabled NO -quiet
```

Generic unsigned iOS build (exit 0):

```sh
cd /Users/gaominge/Documents/Codex/Video/DreamJourney_dev
xcodebuild build -workspace DreamJourney.xcworkspace -scheme DreamJourney \
  -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO -quiet
```

Backend affected regression (29 passed, `STORE_BACKEND=memory` is not PG evidence):

```sh
cd /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend
STORE_BACKEND=memory .venv/bin/python -m unittest \
  tests.test_voice_launch_diagnostics tests.test_realtime_voice_proxy
```

Four-scene isolated PG chain (exit 0; the tool refuses to overwrite an output root and aborts a long scene when its preceding short gate fails):

```sh
env -u DEEPSEEK_API_KEY -u DEEPSEEK_BASE_URL -u VOLCENGINE_API_KEY \
  -u VOLCENGINE_APP_KEY -u VOLCENGINE_APP_TOKEN \
  /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python \
  /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/tools/run_round_simulation.py \
  --admin-dsn postgresql://djtest@127.0.0.1:55520/postgres \
  --output-root /Users/gaominge/Documents/liftora/outputs/2026-09-27-dreamjourney-live-mic-start-repair/run-03/four-scene-final-03 \
  --scenario all
```

All PG work used the dedicated local PostgreSQL 16.10/pgvector 0.8.1 instance on `127.0.0.1:55520` with migration head `0124` and synthetic data. No real Provider credentials were supplied. The command is a reproduction record; the output root already exists and should not be reused. `git diff --check` returned exit 0 in both workspaces.
