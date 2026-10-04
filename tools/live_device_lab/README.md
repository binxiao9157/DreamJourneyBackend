# DreamJourney iPhone Live Device Lab

This tool runs synthetic speech through the **actual iPhone SpeechEngine SDK**, the app's current provider-owned answer and native SDK playback path, Live persistence, candidate review and formal-memory APIs. It is not a mocked SDK test or an acoustic microphone test.

**2026-09-29 historical status:** two new physical short scenes passed candidate review, formal activation and cold readback (3 and 4 formal proofs). Final STAGE07 short passed before LONG20. LONG20 failed at 1017.708 seconds: 32 completed audio exchanges, 31 persisted server turns, 24 turns preorganized into 16 internal atoms, **zero published candidates**. The immediate stop was `audioSchedulerStalled` (0.523595-second frame gap); final close/Source/publication did not complete. Root cause remains unproven. The user requests discussion before further changes or tests.

See [current device evidence and analysis](/Users/gaominge/Documents/liftora/outputs/2026-09-29-live-playback-device-retest/run-01/README.md). Prior failed runs remain preserved. Do not replay them.

## 2026-10-04 告别阶段输入与观测修复

十分钟场以真实 Controller 的 recorder pause 成功作为合成 PCM 停止点；工具不会替代产品调用结束，也不会伪造告别完成或 SDK 事件。产品原有告别完成回调及 20 秒兜底不变；测试额外 10 秒仅用于观察真实关闭。暂停时仍有排队输入则明确失败。

活动期语音/静音 0.5 秒调度门槛不变。暂停不能清除首错；首错冻结后仍继续原场的有界停止和发布观察，恢复不覆盖对话 FAIL。报告增加五个首达时点，区分暂停、告别回调、进入停止、SDK 停止前封存、SDK 停止返回。此记录只证明对应代码点到达，不单独证明远端持久化。

隔离副本注入受 DEBUG + LIVE_DEVICE_AUTOMATION 双门控，正常产品源码未改。本轮本地证据见 [实施与验证](../../02-问题修复/测试与验收/2026-10-04-十分钟告别阶段测试干预/实施与验证.md)。旧真机 0.6038 秒停顿来源仍未决，旧 FAIL 不改写；新工具必须重新 stage/build、通过同版独立短场后才能做十分钟验收。

## Scope and current safety rules

- Use only an explicitly authorized account. This run is authorized for the user's current test/demo account.
- Build an isolated copy of the current iOS workspace. The user's original checkout is not patched or reset.
- Instrumentation requires both `DEBUG` and `LIVE_DEVICE_AUTOMATION`. The SDK's recorder changes to STREAM only for an explicit lab `run` launch.
- Mac `say` writes synthetic speech to a file; it never plays it. iPhone uses 16 kHz mono signed 16-bit PCM in physical 20 ms frames. `feedAudio` length is **Int16 sample count**, not bytes. A single producer continues sending zero-valued frames while idle or awaiting a reply, as a real microphone continues capturing. The clock stops before the real stop action; for the 10-minute profile it also stops immediately after the real Controller successfully pauses recorder input for farewell.
- Keep the actual app player enabled. By default preflight verifies system media volume is zero; the user explicitly waived muting for the 9/29 run, using `--allow-audible`. This flag does not disable playback or change memory acceptance. The tool attempts a test-only system volume control when needed; on this iPhone that automatic setting did not take effect, so the user set the Control Center media slider to zero. Do not confuse the silent switch with media volume. Silent output does not prove the speaker or microphone hardware.
- Current product Live is provider-owned (`DialogLiveGroundingPlan.sessionSnapshot`), not a delegated `/echo/answers` turn loop. The product now observes actual **player and decoder** PCM. Completion requires matching reply/generation, exact nonzero sample counts and hashes, synthesis end and 200 ms of observed silent player PCM. No timer or synthetic SDK event can satisfy this proof. Decoded audio alone does not prove playback completion.
- Do not inject ASR/final events, bypass FeatureGate, seed candidates or alter the production server to make a run pass.
- Review only candidates bound to this run's actual capture → batch → Source. V5 proposals must be present and already bound by the product decoder; allow only nonempty add-only operations without existing memory/version targets or dependencies. Reject proposed changes to pre-existing memories. An unknown decision or activation result stops the run; there is no tool-level business replay.
- A run cannot be restarted after its durable launch latch. A fresh scene requires a new explicit test decision; it is not an automatic recovery strategy.
- A physical short test, including candidate activation and cold formal-memory readback, is mandatory before each long run. The receipt binds source, instrumented source, tool, the complete signed app bundle, device and account, expires after one hour, and is consumed once. Gate checks happen before installation/copy/launch; the actual account is checked again in the app before Live starts.
- `20m` and `65m` use real elapsed time, not a virtual clock. They require at least 1200 / 3900 seconds and 110 / 150 synthetic user inputs respectively. They do not guarantee a maximum completion time equal to that minimum.
- Do not process historical failed jobs, clean account data, deploy, commit or push.

## Commands

Run from `/Users/gaominge/Documents/liftora`. Choose **new** stage and run directories. The Xcode service, CoreDevice and macOS speech synthesis may require the host's normal execution permission. Do not interpret sandbox service failures as product failures.

```sh
python3 -m unittest discover -s tools/live_device_lab -p 'test_*.py' -v
python3 tools/live_device_lab/lab.py stage outputs/live-device-lab/STAGE
python3 tools/live_device_lab/lab.py build outputs/live-device-lab/STAGE --signed
python3 tools/live_device_lab/lab.py prepare outputs/live-device-lab/SHORT --profile short
python3 tools/live_device_lab/lab.py run outputs/live-device-lab/SHORT --stage outputs/live-device-lab/STAGE --device DEVICE_ID --current-test-account
```

Only after `SHORT/short-receipt.json` exists and says PASS:

```sh
python3 tools/live_device_lab/lab.py prepare outputs/live-device-lab/LONG20 --profile 20m
python3 tools/live_device_lab/lab.py run outputs/live-device-lab/LONG20 --stage outputs/live-device-lab/STAGE --device DEVICE_ID --current-test-account --short-receipt outputs/live-device-lab/SHORT/short-receipt.json
```

For a later authorized 65-minute run, first run another short gate on the same build, then prepare `--profile 65m`. Do not reuse the consumed receipt from the 20-minute run.

## Evidence and interpretation

- `manifest.json`: synthetic facts, per-input PCM hash, budgets and required fact terms.
- `lab-build.json`, `binary.json`, `identity.json`: source, instrumented source, tool and complete app bundle identities. Schema 2 includes `DreamJourney.debug.dylib`, frameworks, resources and signing material; hashing only the Debug launcher is insufficient. This host-side guard was added after short-08 and passed local tamper tests. It was used in the 9/29 short scenes. Earlier schema-1 stage/run evidence stays immutable; create a new stage rather than rewriting it.
- Preflight fields in `result.json`: current account hash, real policy decisions, system volume, mode and unique launch ID. Earlier setup attempts also have separate `preflight*.json`. The current runner performs preflight and Live in one app launch.
- `result.json`: completed inputs, actual ASR question hashes, decoded non-silent PCM bytes, player completion, UI text hashes, per-turn event timestamps and capture counts.
- `diagnostic-events.json`: on failure, only the current scene's existing minimized diagnostic ring; it is not the complete raw device console.
- `cold-readback.json`: new process identity and the same candidate → Source → formal memory/version readback. `labIssuedBusinessWrites=0` describes the lab's readback calls, not every background action in the whole app.
- `short-receipt.json`: issued only after physical short run **and** cold readback succeed.

Controller actions call the same button handler / confirmation controller used by the product. They do not constitute XCUITest hit testing. Timing reports distinguish speech input, actual final ASR, visible transcript, decoded PCM and playback completion; no arbitrary text or audio callback can fulfill the assertions.

The greeting gate requires actual synthesis, non-silent decoder PCM and product input-ready state; it is not evidence that the greeting physically drained. Each user turn requires native completion or the product's verified actual-player PCM completion plus restored listening. `playbackCompletions` remains the native event count; `verifiedPlayerPCMCompletions` is separate. The runtime never injects a playback-finished event.

Confirmation diagnostics expose only command-format validity, safe HTTP/error classifications and controller phase/notice. They do not export command IDs, candidate text or backend error detail. Never retry an unknown write merely to collect diagnostics.

The current long scripts emphasize repeated and supplemented facts and end-to-end stability. They are not proof of every semantic dimension, contradiction/withdrawal case, all provider limits or a dense 100-unique-fact workload. Candidate and formal-memory checks use actual Source binding and required synthetic facts; stored Source must include each actual recognized user turn.

## Failure handling

Keep the first failure and all current-run coordinates. Do not click “核实整理状态,” create a replacement Live, or resend an unknown POST as a recovery shortcut. A preflight failure that did not start Live can be corrected and rerun after preserving its evidence. A failure after the launch latch requires investigation; a long run stays NOT_RUN if its short gate failed.

The current runtime captures failure state before the product's asynchronous close chain necessarily finishes. `liveOpenAfterFailure=false` only proves Live stopped. Preserve that snapshot and separately collect current-scene, read-only post-stop outbox/completion/server summaries; do not overwrite the original failure or assume its early `closeIntentPersisted=false` proves a durable-close defect. Raw dialogue, secrets and whole device containers must not be exported. Short-08's supplementary summaries establish that close/end/ACK completed and admission failed.

Report local tool checks, real Provider, iPhone short/long, review/activation, cold readback, acoustic hardware and deployment separately. A compiled tool or successful logical simulation is not a physical PASS.

## 2026-09-30 独立场景语义

新增场景会明确说明另建一个不同的读书角，旧场所未改名。仅随机改名字不能证明独立，之前的相关短场FAIL原样保留。此合成脚本验证独立新增；不把它当关联补充/更正验收。关联更正另外用明确旧事实及用户纠正意图测试。linkedTopicID、Source以及旧正式记忆写保护未放宽。

每次模型提示词/合同修改必须包含真实模型有界对照。模型专项、本地四场、物理20/40分钟分别记账；65分钟已不属于当前验收。脚本变更后重新stage/build，旧short receipt不可复用。


## 2026-09-30 恢复核对 schema 2

恢复来源按真实封存消息集合核对，不使用 completedTurns×2。主机只读核对 run/launch/account/device/build/session/productSession/snapshot/Source 与逐条消息身份/摘要；旧本地文件不视作已投递。只有主机证明与手机当前封存账本一致才能进入独立新增确认。

ASR观察与完整回合PASS分离，普通和冻结canonical回调均记录。核心事实识别错误仍然FAIL，不自动确认含错误核心事实的主题；source校验失败或未发确认时 memoryConfirmationStatus=NOT_RUN，未知写保留UNKNOWN_WRITE且不重发。部分发布无法证明完整来源时自动确认阻断。

音频时钟新增128帧内存取证；0.5秒阈值和MainActor SDK约束保留。恢复PASS不发short receipt。所有旧stage/receipt失效，重新stage/build；新脚本补充专名辨字说明，真实ASR效果仍待现场。

本轮工具本地PASS；真实模型/物理短场/20/40分钟/声学测试NOT_RUN。完整材料位于 `02-问题修复/测试与验收/2026-09-30-真机自动验收中断与恢复核对/实施与验证记录.md`。
