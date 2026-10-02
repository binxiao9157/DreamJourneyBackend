# DreamJourney Live 覆盖摘要 UI 刷新补充修复报告

## 1. 结论

状态：`LOCAL_PASS / DEVICE_PARTIAL_PASS / EXACT_TRANSITION_NOT_OBSERVED`。

主修复的覆盖摘要计算正确，但页面此前只监听 capture state。仅登记成员迟到写入 partial 正文时，未封存数量仍为 1，`.coverageGap(1)` 到 `.coverageGap(1)` 被状态去重，导致页面继续显示“尚无法确认本次内容已完整保存”。本轮为覆盖摘要增加独立观察通知，不伪造状态变化、不改变封存规则，也不触发业务写。

## 2. 修改

### `EchoViewController.swift`

- `EchoLiveMemoryCaptureCoordinator.onCoverageSummaryChange`：覆盖摘要变化时单独通知观察者。
- `applyCanonicalCoverage`：比较前后摘要，仅在真实变化时发布；原 unsealed、封存和诊断逻辑不变。
- `retainLiveMemoryCaptureCoordinator`：页面接收摘要变化，更新当前摘要并用协调器当前 state 重绘。旧协调器仍受 coordinator identity 检查约束。

### `OwnerTruthContractsTests.swift`

- 新增 `testEchoCoverageCopyRefreshesWhenLatePartialBodyPersistsWithoutGapCountChange`。
- 场景贯穿真实 `EchoViewController`、`EchoLiveMemoryCaptureCoordinator` 与临时磁盘 Store：仅登记且无正文 → finish 显示无正文文案 → 同成员迟到 partial 正文落盘 → 缺口仍为 1、state 仍为 `.coverageGap(1)`、页面切换为已有正文文案。
- 测试客户端只悬停 current-session 读取，隔离后续网络状态迁移，不替代被测 Controller/Coordinator/Store 路径。

## 3. 红绿证据

### 修前

- `evidence/pre-fix/coverage-summary-ui-refresh-red-v2.xcresult` 对应日志明确显示：`missingBody/hasPersistedLocalText=false` 已变为 `partialBody/true`，但页面在等待期内未更新目标文案。
- Xcode 在测试断言完成后卡于日志收尾，终止后该 xcresult 缺少 `Info.plist`；因此标记为原始失败日志证据，而非可解析结果包。
- `coverage-summary-ui-refresh-red.xcresult` 是早期直接读取 UI 的装配结果，存在主队列竞争，仅保留，不作为权威红证据。

### 修后

- `evidence/post-fix/coverage-summary-ui-refresh-green-v3.xcresult`：1/1 通过；状态和缺口均保持不变，UI 在摘要变化后更新。
- `evidence/post-fix/D1-04-D1-06-audio-regression.xcresult`：14/14 通过，包括 9 个 OwnerTruth 场景和 5 个音频保持性场景。
- `evidence/post-fix/generic-ios-device-build.xcresult`：通用 iOS 设备目标编译通过，`CODE_SIGNING_ALLOWED=NO`。
- `git diff --check`：通过，无输出。

## 4. 指纹与边界

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- `EchoViewController.swift`：`58fd0f38452c113f04c23542844bafc47d0911d0f30437f0d809ed664a91ef9a`
- `OwnerTruthContractsTests.swift`：`e5c32ef7714af45df65c47e6fb898c9d4d6e3e5c36d759fb43d0914954451115`

工作树中的前序 B4/B6/B7/B8、FM-POLICY 和 Live 修改全部保留。本轮没有后端改动，不需要后端部署；没有修改历史 UI 仲裁、音频、打断、正式记忆绑定、权限、CAS、hash、revision 或 Binding。

## 5. 最小真机验收

1. 保留 App 数据安装当前工作树构建，确认仍为原账号。
2. 开启一场 Live，只说一段较长的新合成测试信息；在识别正文仍可能未完整时手动停止。
3. 若页面先显示“尚无法确认本次内容已完整保存”，继续原地等待迟到正文落盘，不进入其他页面、不切后台。
4. 观察缺口状态未改变时，文案是否自动更新为“已保存收到的内容，部分内容尚未完整记录”。
5. 记录初始文案、变化后的文案、变化耗时及是否出现错误。不要审核或清理候选。

若无法稳定制造“仅登记后迟到 partial”时序，则标记真实 SDK 场景 `NOT_RUN`，不能用普通成功封存替代。

## 6. 回退

只回退 `onCoverageSummaryChange`、`applyCanonicalCoverage` 的摘要差异通知、控制器接线及对应新增测试。不得回退非 ASR 资格过滤、关闭排空、未知写只读核实、账号隔离或覆盖摘要本身。

## 7. 2026-09-17 最小真机验收

### 安装与环境

- 设备：iPhone 14 Pro Max，UDID `00008120-000C188E2184C01E`。
- 包名：`com.gaominge.dreamjourney.app`。
- 使用相同包名原位覆盖安装，未卸载、未清除 App 数据。
- 真机签名构建：`evidence/device/device-build.xcresult`，通过。
- 真机可执行文件 SHA-256：`e53b1c264772d3c2be5fc0d2b6b3e57def11d25806e54f3ed17cfd328fd78b80`。

### 第一次尝试

- 用户在 AI 回答完成后停止，日志显示 `membersWithoutBody=0`、`partialTurns=0`，没有命中待测覆盖缺口。
- 页面至少 45 秒保持“正在保存本次对话”；彻底关闭并重启后，只读恢复显示“上次对话已进入待确认记忆”。
- 结论：目标场景 `NOT_RUN`；长时间 saving 作为相邻现场观察保留，不归入本轮 UI 摘要修复 PASS。

### 第二次尝试

- 用户在最后一句仍在说时停止；未继续播放 AI 回答，无报错。
- 页面立即显示“已保存收到的内容，部分内容尚未完整记录”，15 秒后保持一致。
- 安全日志确认 `registeredMembers=1`、`partialTurns=1`、`membersWithoutBody=0`、`unsealedTurnCount=1`、`hasPersistedLocalText=true`，与 UI 文案一致。
- 结论：真机 partial 覆盖缺口展示 `PASS`。

### 证据边界

- 真机没有肉眼观察到“尚无法确认本次内容已完整保存”先出现、随后自动切换到 partial 文案；迟到 partial 在页面首次可见前已经落盘。
- 因而“摘要变化但 capture state/缺口数不变时自动刷新”仍由真实控制器、协调器和磁盘 Store 的本地红绿测试证明；真机精确瞬时切换标记 `NOT_OBSERVED`，不冒充完整现场 PASS。
- 真机操作及脱敏事件摘录见 `evidence/device/2026-09-17-minimal-ui-refresh-observation.md`。
