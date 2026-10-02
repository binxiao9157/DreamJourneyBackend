# 本场 iOS 采集中断：排他分析与增量组合诊断

本文件为只读代码审计和隔离 Simulator 诊断；没有修复产品，没有操作真机、真实 Provider 或生产。测试以 VFS overlay 替换测试编译输入，产品源码和原测试文件未改，原测试文件 SHA-256 与前次记录相同。

## 结论与证据等级

1. **现场确定**：待确认记忆链路的 canonical Outbox 只到前 13 个 owner turn / 26 条正文，26 条均有 deliveryMessageID、pending 0；服务端也只有 26 条且均 201，最后更新时间 23:09:21；无 close intent、无 end。此后用户继续约 14 分钟，SDK 仍回调、语音仍工作。断点首先位于会中 canonical 采集/协调，而非会后 Worker 容量。
2. **代码与组合测试确定**：相同 canonical ID 出现不同 complete 正文时，Store 拒绝覆盖；Coordinator 却将单条冲突升级为整场关闭，Controller 移除当前采集入口。后续 SDK 音频与回调可继续，新的 member 不再落盘；手动 stop 也不再持久化 close intent。该错误机制已在真实 Controller/Coordinator/磁盘/FeatureGate/BackendClient/受控 HTTP 下重现。
3. **现场最符合但未唯一坐实的首触发**：同一 owner 的多次 ASR final 差异，或 assistant 多来源终稿冲突。两种都有真实源码入口，且后果与现场形状完全一致。手机保留日志从 23:10:15 开始，23:09 首故障窗口已覆盖，不能宣称某条具体 SDK 回调是现场唯一首因。
4. **仍需保留的次选解释**：26 条完成之后第一次新 member/body 的文件写入或 envelope 校验失败，同样会关闭全场。没有对应原始错误码，不能仅凭磁盘最终可读排除曾发生瞬时写失败。

## 真实链路与精确代码

源码根 `/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`。

- `OwnerTruthContracts.swift:16733` `upsertCanonicalTurn`：同 ID 已 complete 后正文不同，或已取得 deliveryMessageID 后正文不同，在 16760–16768 抛 `canonicalTurnConflict`。仅 trim 首尾空白，句号差异也冲突。
- `EchoViewController.swift:1620` `appendCanonicalTurn`：任何 Store failure 均只记录 `persistenceRejected`，1678–1679 设置 `isCaptureOpen=false`、`state=.unavailable`。
- 同文件 1685/1733–1734 `registerCanonicalMember`：注册失败同样关闭采集。Store 16458 检查 ID 非空/长度、角色、close intent 等。
- 同文件 13034 `retainLiveMemoryCaptureCoordinator`：terminal 状态会关闭 ingress、清空 active Coordinator 并移除 retained 条目。不能简单称对象必然立即释放，冻结的 SDK binding 仍可能持有入口；但 isCaptureOpen=false 已足以拒绝后续采集。
- 同文件 13229 `finishLiveMemoryCaptureIfNeeded`：active 已空则直接 return；Coordinator 自身 1740 `finish()` 对 isCaptureOpen=false 也 return，所以没有 close intent、end、ACK、admit。
- 同文件 13312：Live 开启时不展示会后状态；13044 已缓存状态，停止后再渲染。因此用户在 stop 才看到 unavailable 不代表故障发生在 stop。

## assistant 的两类终稿来源

`DialogEngineManager.swift`：

- 5825 `SEEventChatEnded` (3016) 经 `deliverCanonicalAssistantStreamPacket(.ended)` 提交 complete，然后将 chat 全文 `replace` 到 `providerCanonicalAssistantTextState`。
- 5547/5604 `SEEventTTSSentenceStart/End` (3008/3009) 都以 `updateCanonicalAssistantText` 更新同一正文缓冲。
- 5636/5650 `SEEventTTSEnded` (3011) 再把该缓冲按相同 replyID 提交 complete。
- 1685 附近 `DialogProviderCanonicalReplyTextState.update` 只用 `next.hasPrefix(text)` / `text.hasSuffix(next)` 判累计正文或重复片段，没有识别全文内已包含的非尾部句段。例如先已缓存“第一句。第二句。”，后收到 TTS 句段“第一句。”，会变成“第一句。第二句。第一句。”；下一 complete 与首次全文冲突。
- 若 TTS 先结束、Chat 后结束而两路标点或文本不同，也有同一冲突入口。

手机后段存在 3008/3009/3011/3016，说明该路径实际启用；但日志没有正文/终稿状态，且首故障窗口缺失，不能把上述例子直接写成本场已发生事实。

## 不依赖 3021 的两项增量组合

结果：`canonical-controller-increment-pair.xcresult`，独立 `xcresulttool` 核对 2 passed / 0 failed。每个测试内包含同正文控制与不同正文故障两支。

- `testAstraDeviceFailureRepeatedASRFinalPair`：前三轮 6 条 messages201 且队列排空后，同一 question 的 3013/ASR response 再发 `is_interim=false`，无 3021。相同正文继续 10 owner turn，完成 end/ACK/admit；仅移除一个句号则 unavailable，下一 question member 被拒绝，closeIntent=false，后续 end/ACK/admit=0。
- `testAstraDeviceFailureRepeatedAssistantFinalPair`：同 replyID 再发 ended。相同正文完成后续全链；不同正文导致完全相同的采集退出。此项覆盖 Manager 测试入口与生产共享的 ChatEnded 整理/路由实现，**没有宣称调用真实 SDK TTS 或真实外部模型**。

安全输出保存在 `canonical-controller-increment-safe-sequence.log`。两项“测试通过”表示准确验证缺陷与控制组，并不表示修复完成。

现有 `OwnerTruthContractsTests.swift:865` 的 `testLiveCanonicalConflictDoesNotBlockFollowingOwnerTurn` 只测试 Store：调用者捕获冲突后再直接 upsert 下一条；没有真实 Coordinator 的 failure 分支，所以不能证明产品会继续采集。

## 其它假设逐项核对

| 假设 | 代码与本场证据 | 判断 |
| --- | --- | --- |
| 单纯 release-policy/TTL 拒绝 | natural input unavailable/failed 在 Echo 2216–2300 映射 syncPaused/statusUnknown；注释明确传输暂停不可退休采集，canonical 接收只校验账号 lease、不校验 FeatureGate。传输暂停应有后续 member/body 和 pending 增长。 | 不能单独解释 26 后完全没有 member、pending=0 和 unavailable。 |
| /presentation 停止导致 canonical 停止 | presentation/UI 路径与 provider-owned canonical ingress 独立；现场 presentation 停后还有 6 条 messages201。 | 排除作为直接同步停止点；不排除旧 UI 路径另有显示问题。 |
| 第26条 append201 的确认落盘失败 | acknowledgePersistedTurn 2381–2435 失败会 unavailable；但正常失败应保留 inFlight durable pending，现场 pending=0、26均 deliveryID。 | 明显不符合；极端写成功后抛错仍不能仅凭最终快照绝对否定。 |
| dispatch state 落盘失败 | 2374 也可 unavailable，一般应已有待发 pending/body。 | 与 pending=0、无下一 member 不吻合。 |
| 丢写后的 GET 确认空队列路径 | 2733 存在 state=.unavailable 后重建用例的独立问题，但需要 live-delivery-status GET。 | 本场无该 GET，排除为本场首因。 |
| stop closeIntent/closeRequest 写失败 | 1984/2034 会 unavailable，但无法解释 23:09 后停止采集而 SDK 继续至23:23。 | 可能另有风险，不符合首个断点。 |
| ACK/admit/Worker 拒绝或 DeepSeek超时 | 必须先 end，现场无 end；本场只有预整理 DeepSeek2次 accepted/stop。 | 不能作为本次最早失败原因。 |
| account lease 失效 | reserve/submit 可在 lease无效时拒绝，但单独不会将开放中 capture 设 unavailable；用户还持续语音，auth刷新远晚于断点。 | 证据弱；若存在账号切换另需对应事件，不能用策略TTL等同账号 lease。 |
| 固定20分钟、33轮上限 | 本场5分46秒即停止canonical；没有该时长/轮数上限分支。 | 当前证据不支持。 |

## 日志与可恢复数据边界

- `ConversationMemoryManager.swift:670–729` 的 `PrivacySafeDiagnostics.log` 最终仅 `print`。`captureStateChanged`、`canonicalTurnUpsert` 不进入 `NativeLiveDiagnosticsRingStore`。源码未找到 stdout 重定向或 DDFileLogger 配置可将这些日志持久化。NativeLiveRing 即使未覆盖，也主要记录 Provider 事件/音频状态，不是 canonical 错误归因台账。
- Outbox 保存正文/水位而不保存完整 capture 状态迁移史；本场已核对前13个owner、26条全部落盘，不能据此反推下一次 upsert 的错误原因。
- **不要写“手机后半场所有内容都丢失”**。另有 `ConversationMemoryManager`：EchoViewModel 1551 接受 user intent 后 `recordUserTurn`，1600–1608 接受 reply intent 后 `recordAITurn`；manual stop 在 Echo 15453 附近调用 `endSession()`，该方法 234 将 `currentTranscript.suffix(20)` 持久化到 `Application Support/DreamJourney/Conversation/v2/<scopeDigest>/memory.json`。
- 上述旧路径受 UI turnIntent 门控，不是每个 canonical 回调都会进入；仅保留已被接受的最后20条，不保证全场，也不自动补进当前 OwnerTruth Outbox。`transcriptEntries` 仅内存最后4条。未读取本场该 memory.json，不能声称有或没有后半场正文。
- 当前“原对话已保留”通过主链证据仅能证明前13个owner/26条已保存，不能证明全部33个用户回合均可恢复。其它本地旧存储只能作为后续只读恢复调查线索。

## 当前可对外表述的根因置信范围

可确定：本场在 iOS 会中持久化入口/协调生命周期已发生停止，未到会后候选整理；候选0是该断链的后果。可确定一个与现场高度一致的内部缺陷：单条 canonical 冲突被升级为全场采集终止，且 stop 丢失 close 交接。最高优先核对或修复这个边界，但应将“本场首次冲突具体来自哪条 ASR/TTS/Chat 回调或一次磁盘失败”保留为未唯一确认。


## 最后一次可行性核查：真实 SDK TTS 分派未执行

Simulator 编译日志明确包含 `-DUI_QA_SIMULATOR`。`DialogEngineManager.swift:1943` 的条件分支在 Simulator 使用替身 Manager；原生 `SpeechEngineToB`、`SEMessageType` 与 `handleProviderMessage` 的完整 switch 位于 2387 `#else`，该构建不编译此分支。

已经跑过的组合使用产品共享的 ASR parser、canonical ingress router、assistant stream assembler，加真实 Controller/Coordinator/磁盘/FeatureGate/BackendClient 和受控 HTTP。它能证明“冲突→全场终止→stop没有交接”的产品缺陷；不能证明真实 SDK 会按所假设的3008/3009/3011/3016顺序产生相应正文。

现成测试钩子没有 TTS sentence/ended 分派入口。按不扩大工程、不改变产品源码要求，本轮没有新增替代 TTS switch，也没有将重复 ChatEnded 的测试冒充真实 TTS 顺序测试。确切限制是：**ChatEnded全文后TTS中间句追加的具体源码风险已定位；该真实SDK分派组合 NOT_RUN，现场首触发仍未唯一确认。**
