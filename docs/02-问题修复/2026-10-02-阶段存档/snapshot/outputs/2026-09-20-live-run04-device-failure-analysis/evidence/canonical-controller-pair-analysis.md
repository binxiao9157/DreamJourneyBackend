# 同 question ID 两份终稿导致整场采集退役：真实组合诊断

日期：2026-09-20。诊断结论：**缺口已在当前业务源码上稳定复现；尚须真机同场日志确认是否为此次现场触发原因。**

## 执行方式与边界

- 使用真实 `DialogEngineManager → EchoViewController → EchoLiveMemoryCaptureCoordinator → OutboxStore → FeatureGateService → DreamJourneyBackendClient`。
- `URLProtocol` 拦截 `owner-truth.qa.invalid`，提供受控 HTTP 回执；所有输入均为新合成内容。未调用真实 Provider、未访问手机、未操作生产或历史数据。
- 通过 Swift VFS overlay 仅向原测试文件的隔离副本加入两个诊断方法及一个可选探针分支；未修改产品源码或工作区测试源码。
- 复用 `runManagerEchoRealGateBackendLogicalTwentyMinutes` 的真实组合装配、HTTP 合同、磁盘和 FeatureGate，运行在已启动的 iPhone 17 Pro / iOS 26.5 Simulator。
- 测试源码修改前后 SHA-256 相同，见 `canonical-controller-original.sha256`。

## 正反对照

先以正常 ASR 与助手回调完成 3 个用户回合、6 个 HTTP 201 append，并等待本地确认排空。随后给第三个 question ID 再发 `queryConfirmed`。

| 场景 | ASR final | queryConfirmed | 实际结果 |
|---|---|---|---|
| 正对照 | `第 3 条完整用户表达。` | `第 3 条完整用户表达。` | 后续继续采集 10 个用户回合，完成 end / ACK / admit / pendingReview |
| 缺陷反例 | `第 3 条完整用户表达。` | `第 3 条完整用户表达` | `canonicalTurnUpsert accepted=false`，`live → unavailable`；下一 question member 被拒绝；磁盘停在 3 用户回合 / 6 段；停止后没有 close watermark、end、ACK、admit |

两个诊断测试均通过，含义是**成功证明正对照正常、缺陷反例仍存在**，不是产品已修复。

关键安全日志：

```text
event=canonicalTurnUpsert accepted=false finality=complete reason=persistenceRejected role=owner
event=captureStateChanged from=live to=unavailable ownerTurnCount=3 persistedOwnerTurnCount=3 queuedTurnCount=0
ASTRA_CANONICAL_PROBE conflictDifferentPunctuation=REPRODUCED ownerTurns=3 messages201=6 nextMemberRejected=true acceptsTurns=false closeIntent=false endAckAdmit=0 ui=echoLiveMemoryUnavailable
ASTRA_CANONICAL_PROBE control sameFinalConfirmed=PASS nextTurns=10 endAckAdmit=PASS
```

## 代码因果链

1. `DialogEngineManager.swift:1128` 将显式 ASR final 与 confirmed 均映射为 `.complete`。
2. `DialogEngineManager.swift:1314` 基于 engine generation、owner、question ID 建立相同 canonical ID。
3. `OwnerTruthContracts.swift:16760` 发现同 ID 已 complete，且收到不同正文，抛出 `canonicalTurnConflict`，避免覆盖已确认事实。
4. `EchoViewController.swift:1665–1679` 对该异常没有局部分类；统一将 `isCaptureOpen=false`、状态设为 `.unavailable`。
5. `.unavailable` 属于终态（同文件 320）；真实 Controller 在 13047–13053 关闭 canonical ingress、移除 active/retained coordinator。
6. 后续新 question 无法登记，`finishLiveMemoryCaptureIfNeeded` 不再拥有可结束的协调器，故不会产生 end / ACK / admit。

音频与这条记忆采集链独立。该诊断未运行音频或真实 SDK，不能声称验证了现场音频；它证明即便后端所有已有 append 均为 201，也能由客户端单个终稿冲突切断后续保存。

## 原绿测漏检位置

`OwnerTruthContractsTests.swift:865` 的 `testLiveCanonicalConflictDoesNotBlockFollowingOwnerTurn` 只直接调用 Store：在捕获 q1 冲突异常后由测试代码继续写 q2。它没有经过 Coordinator 的终态处理及 Controller 的生命周期清理，因此不能证明真实界面仍会采集 q2。

## 现场关联所需证据

本地反例不能替代真机首次错误证据。需核对本场断点附近是否存在：

- `canonicalTurnUpsert accepted=false` 或 member registration rejected；
- 同 canonical 身份的先前 complete 与后续 complete，及两者是否正文不同（只返回差异类型、长度、哈希，不披露正文）；
- `captureStateChanged ... to=unavailable`，以及当时 registered / persisted / queued 数量；
- 无后续 append、无 end、音频仍持续的时序。

不得据该合成反例宣称真实 Provider 必然发了标点变化。完整修复还需确定厂商 final / confirmed 的版本语义，并保留已确认正文、局部冲突证据、后续场次采集与现有未知写保护。

## 证据

- `canonical-controller-pair.xcresult`：2 个测试，0 失败，0 跳过。
- `canonical-controller-pair-safe-sequence.log`：安全阶段与对照结果。
- `OwnerTruthContractsTests-overlay.swift`：隔离测试副本。
- `canonical-controller-overlay.json`：仅测试文件 VFS 映射。
- `canonical-controller-original.sha256`：原工作区测试源码指纹。


补充测试边界：本次 Simulator 构建带 `UI_QA_SIMULATOR`，Manager 为该条件分支；共享 production canonical parser/router/assistant stream 与真实 Controller/Coordinator/磁盘/FeatureGate/BackendClient 在组合中执行。原生 SDK 的 `handleProviderMessage` switch 不在 simulator 分支，不能将本探针表述为真实 SDK 回调测试。之后增加了无3021的重复ASR final和assistant重复ended配对证据，参见 `ios-capture-path-exclusion-and-increment-probes.md`。
