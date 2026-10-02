# Final local source/build/config linkage

- iOS checkout HEAD `11d0d0051b9be3cce57822dd059472d1e2536866`; backend HEAD `8141ff271228b78c35237f9947f4bf026332af43`. Both have preserved dirty changes. `git diff --check` passed; no commit/push.
- Final protected source fingerprint `7aae5172321cc96fdf79866b6214c09a8780de7a1034b364a02457dad60d4830`. [Link verification](evidence/final-source-build-link.json) compared 1,297 protected dependencies and 312 recorded artifacts: current fingerprint equals the four-scene manifest; zero artifact mismatches. Its `reuseEligibleBySourceAndBuild=false` compares against an older run-03 entry baseline; it is **not** a current mismatch. `artifactMatchesCurrentSourceAndBuild=true`.
- Final Simulator app dylib SHA-256 `57a32fb9af4fa42b8179d7fb8837661c5c6c7638894393fd17447dbfd6bc99cd`, test binary `4b636850d1cd62584af34644faa1db9ab92378604a6b2cf05974bd525a4c10eb`, xctestrun `3ae6e6077a474a510fc4c511c7861a625844629ae2a0e73f106d771484ff5f5c`. Full [source/build manifest](four-scene-final-02/artifacts/source-build-manifest.json).
- Generic device unsigned app dylib SHA-256 `6594d6bb67062906e5789ab63de5341b46bbb878c5170abd5b9d0b2a3699baee`; [build log](evidence/generic-ios-final.log) exited 0. This is not an installed or signed iPhone build.
- iOS tracked dirty diff SHA-256 `bbae827f56b8affd03e8a2610a90e79851c329ce811b8a4d88537948f0aaf1c3`; backend tracked dirty diff `fb795c4ace9b56e79fdbb7c902a5d7e0208cfe299478946e91dd73102dc9de92`. These are supplementary and omit untracked files; the protected source fingerprint above includes the applicable untracked diagnostic module/test.
- Backend new diagnostic module SHA-256 `1e90b59c0ff37cfd1dc36c67e3f6a7ded2541e1f1b08babbceedf35dcb9feb85`; test `bc2afd5a3e386b4a8d043ca98578d760b0bb5c16f5f26db78919f555376accb8`.
- Xcode 27.0 (27A266a), iOS Simulator 26.5, local PostgreSQL 16.10 + pgvector 0.8.1, isolated migration head `0124`. Controlled model HTTP; no real Provider. Logical20 configuration fingerprint `cda8ce388a42d46a4ced8f9b786a5c955184974653a0c83077254bfb28e8df79`; logical65 `b5c8df639ed092701209adf392e1974247f374d1be1b1d5c0993430734c9ee19`. Shared build fingerprint `61e0003b44d4fcaf36935c8991036d03fe56cda9798df494299a44292685d691`.

## Reproduction commands

All commands use the local isolated database and synthetic data. Recreate the dedicated PG cluster before the PG-dependent commands; never substitute a production DSN.

```sh
cd /Users/gaominge/Documents/Codex/Video/DreamJourney_dev
xcodebuild -workspace DreamJourney.xcworkspace -scheme DreamJourney -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' CODE_SIGNING_ALLOWED=NO test
xcodebuild -workspace DreamJourney.xcworkspace -scheme DreamJourney -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```

```sh
cd /Users/gaominge/Documents/liftora
env -u DEEPSEEK_API_KEY -u DEEPSEEK_BASE_URL -u VOLCENGINE_API_KEY -u VOLCENGINE_APP_KEY -u VOLCENGINE_APP_TOKEN \
  /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python \
  /Users/gaominge/Documents/liftora/outputs/2026-09-23-live-save-unified-repair/run-03/tools/run_round_simulation.py \
  --admin-dsn postgresql://djtest@127.0.0.1:55520/postgres \
  --output-root /private/tmp/dj-mic-new-local-run --scenario all
```

The runner refuses to overwrite an existing output root and enforces the two independent short gates before long scenes. Historical or production data are not inputs.
