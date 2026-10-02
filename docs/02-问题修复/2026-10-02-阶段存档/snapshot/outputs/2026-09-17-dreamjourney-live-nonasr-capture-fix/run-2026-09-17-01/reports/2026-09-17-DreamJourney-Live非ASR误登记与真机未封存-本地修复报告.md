# DreamJourney Live 非 ASR 误登记与真机未封存本地修复报告

## 1. 结论

状态：`LOCAL_PASS / DEVICE_PENDING`。

本轮确认并修复了一个源码缺陷：SDK `.other` 消息此前会沿用通用 metadata 中的 `question_id`，登记 owner canonical member、占用 active question window，并在没有 ASR 正文时形成不可封存缺口。该缺陷能够解释“非 ASR 消息造成额外缺口”的本地反例，但不能据此宣称它是历史真机未封存的唯一原因。

本轮未部署、未安装或启动 iPhone、未访问生产数据、未处理历史任务、未 commit/push。现场缺陷继续保持待真机验收。

## 2. 基线与边界

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 分支：`feature/prd-stitch-ui-adaptation`
- 开工时工作区已有大量 B4/B6/B7/B8、FM-POLICY 与 Live 未提交修改；本轮未 reset/clean，未整文件覆盖。
- 后端代码未修改，无新增数据库合同或部署需求。
- 保留原生 Live、SDK 内置播放、声音、连续聆听、打断、正式记忆绑定以及未知写只读核实边界。

## 3. 修改内容

### `OwnerTruthContracts.swift`

- `NativeLiveCanonicalTranscriptIngressKind`：持久记录 owner member 的可信入口种类。
- `NativeLiveCanonicalTranscriptMember` 及磁盘 envelope：保存 ingress kind，兼容旧记录的缺省值。
- `OwnerTruthInterviewCanonicalCoverageSummary`：汇总 registered、missing body、partial、complete blocked、delivery、server confirmed 及首个缺口的安全元数据。
- `OwnerTruthInterviewLiveTurnOutboxStore.snapshot`：从实际 member/turn/delivery 状态生成覆盖摘要，不记录正文、原始 ID 或业务 hash。

### `DialogEngineManager.swift`

- `DialogProviderSDKEventClassifier`：集中锁定 SDK 事件映射；只有 ASRInfo、ASRResponse、QueryConfirmed 具备 owner ingress 身份。
- `DialogProviderCanonicalIngressRouter.freeze`：`.other` 不读取 `question_id`，不登记 member、不占用或回滚 active question window；原 ordinal 与原 handler 行为保留。
- `canonicalIngressClassified`：只记录类型、是否含身份/正文、finality、登记/派送结果及 ordinal。

### `EchoViewController.swift`

- `EchoLiveMemoryCaptureCoordinator.applyCanonicalCoverage`：在登记、正文落盘、写回执及恢复时同步覆盖摘要。
- `renderLiveMemoryCaptureState`：存在已保存正文时显示“已保存收到的内容，部分内容尚未完整记录”；完全没有可确认正文时显示“尚无法确认本次内容已完整保存”。
- `flushPersistenceForTesting`：仅 DEBUG 的磁盘串行队列栅栏，用于确定性装配测试，不改变生产调度。

### `OwnerTruthContractsTests.swift`

- 新增非 ASR 开场、两问两答完整交接、迟到非 ASR 不回滚、无正文覆盖文案反例。
- `InterviewNaturalInputClientSpy` 补齐 typed append 的可控完成，确保测试真实观察“写入已准备但尚未回执”的磁盘状态。
- 隔离共享 Manager 的测试 scheduler，避免跨用例遗留调度影响结果。

## 4. 红绿证据

### 修复前

`evidence/pre-fix/D1-01-D1-02-red.xcresult`

- 2 项执行，0 通过，2 失败。
- 失败事实：TTS sentence start 被错误构造成 owner member；两问两答场景在真实 Manager 入口同样先产生了错误 opening member。

### 修复后

- `evidence/post-fix/D1-targeted-green-v2.xcresult`：4/4 通过。
- `evidence/post-fix/D1-02-deterministic-green-v6.xcresult`：完整 start/append/end/ack/admit 装配 1/1 通过。
- `evidence/post-fix/D1-01-D1-06-regression-v2.xcresult`：12/12 通过。
- `evidence/post-fix/ownertruth-audio-regression-v5.xcresult`：OwnerTruth 505 + 音频 5，共 510/510 通过。

中间 `D1-02-deterministic-green-v2/v3/v5` 与 `ownertruth-audio-regression-v2/v3/v4` 记录的是测试装配时序修正过程，不作为最终绿证据，也未被删除或覆盖。

## 5. 构建与静态检查

- 通用 iOS Simulator：PASS，`evidence/post-fix/simulator-build.xcresult`。
- 通用 iOS 设备目标、`CODE_SIGNING_ALLOWED=NO`：PASS，`evidence/post-fix/generic-ios-device-build.xcresult`。
- `git diff --check`：PASS，无输出。
- 真机安装/启动：NOT_RUN。
- 真实火山 SDK 顺序：NOT_RUN。

## 6. 源码指纹

| 文件 | SHA-256 |
|---|---|
| `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift` | `7ad24e61a794e65d73864ae4b147eae95e3dc224c7b403505500afbbbc919b30` |
| `DreamJourney/Sources/Services/DialogEngineManager.swift` | `b8c40ed64fcf0031974b26caa7cc06ca258b405b66f088fa155960dd6b08f603` |
| `DreamJourney/Sources/Modules/Echo/EchoViewController.swift` | `1037de8232772362de812bc2782877dfa27bd13ee38204941c576404fea26e3a` |
| `DreamJourneyTests/OwnerTruthContractsTests.swift` | `b236753acefdc4c81931dac4b693fac878c77a19bca0c5434f6be2a158e35508` |

这些是包含前序未提交修改的整文件指纹；不能将整文件 diff 归因于本轮。

## 7. 部署与残余风险

- 后端：无修改，无需本轮部署。
- iOS：需后续授权后安装本修复工作树对应构建。
- 残余风险：锁定的 SDK raw type 已由 SDK 头文件和本地共享 classifier 验证，但真实设备回调顺序、供应商字段差异及历史现场是否由此缺陷触发仍待真机证据。
- 历史 DJ-LIVE-DEVICE-01：保持 FAIL；修复版真机复测为 NOT_RUN。

## 8. 最小真机复测（待授权）

1. 安装时保留 App 数据，先确认账号与候选读取正常。
2. 开一场 Live，先说明确事实 A，再问普通问题，再说明确事实 B。
3. 等两次回答完整结束后手动停止；记录停止状态及 15 秒后状态。
4. 刷新候选列表，确认 A、B 同属本场、各一条、无额外“用户问过……”候选。
5. 完全杀掉 App 后重开，确认同一场状态可只读恢复，且不自动开麦、不重复发送业务写。
6. 任一处出现“部分内容尚未完整记录”或“尚无法确认完整保存”即停止后续依赖测试并保存安全日志。

## 9. 局部回退

若发布前必须回退，只回退本轮四个代码块：SDK event classifier 的 owner eligibility、router 的非 ASR 身份隔离、coverage summary/文案、对应测试与 DEBUG 栅栏。不得回退 B8 未知写只读核实、B6 冷启动恢复、账号租约、CAS、Binding 或前序 fresh authority 修复。回退会重新暴露非 ASR 误登记风险，因此不建议在无替代修复时发布。
