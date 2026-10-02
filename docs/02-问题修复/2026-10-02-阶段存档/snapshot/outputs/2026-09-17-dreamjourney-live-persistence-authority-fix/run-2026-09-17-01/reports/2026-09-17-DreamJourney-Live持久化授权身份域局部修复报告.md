# DreamJourney Live 持久化授权身份域局部修复报告

日期：2026-09-17\
状态：**授权阻断已本地修复，真实保存链/真机待验证**

## 1. 范围与基线

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`，分支 `feature/prd-stitch-ui-adaptation`。
- 后端 HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`，分支 `main`，本轮未修改。
- 开工时 iOS 已有大量前序未提交修改；本轮没有 reset/clean、整文件覆盖或回退其他任务成果。
- 本轮仅实施设计第 6 章的修复 A。修复 R、历史 UI 仲裁、真实 SDK、真机和部署均不在本轮执行范围。

## 2. 已确认根因

生产 `FeatureGateService` 的 `FeatureDecision.accountGeneration` 来自认证 session/user 身份源的 SHA-256 前 24 位；`AccountLease.generationId` 是 App 会话租约 UUID。两者用于不同的身份域和失效边界。

`OwnerTruthInterviewNaturalInputRequestAuthority.init?` 原来要求这两个字符串相等。生产中前者是 24 位摘要，后者是 36 位 UUID，因此即使策略允许、账号和 lease 都合法，authority 仍会构造失败，真实写链在 transport 之前停止。旧测试把 FeatureGate generation 人工设成 lease UUID，因而掩盖了生产缺陷。

该源码缺陷已确认；它与最近真机“正文仍在本机、重启后显示待确认但没有本场候选”的表现一致，但缺少当次手机上的完整原始阶段日志，不能宣称它是那次现场的唯一根因。

## 3. 修改内容

1. `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
   - `OwnerTruthInterviewNaturalInputRequestAuthority.init?` 删除跨身份域的字符串相等比较。
   - 保留 FeatureDecision 的 feature、purpose、allowed、expiry、operationGeneration 和 trace 校验。
   - AccountLease 仍由请求前和提交阶段独立验证；BackendClient 仍在 transport 前用真实 FeatureGate source 重验 decision。
2. `DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`
   - 提取 `FeatureGateService.accountGeneration(forIdentitySource:)`，生产属性和组合测试共用同一实现，避免测试复写哈希算法。
3. `DreamJourneyTests/OwnerTruthContractsTests.swift`
   - 三个真实组合用例改用生产账号代次生成器、真实 FeatureGate evaluator/revalidator、合成认证 session、受控 URLProtocol 和临时磁盘。
   - 覆盖新场一问一答完整关闭、ACK 旧 route 过期后刷新、逻辑 20 分钟多次 TTL。

没有放宽账号、vault、lease、FeatureGate、回执绑定、版本或未知写边界；没有新增业务写重试。

## 4. 红绿证据

有效修前红例：`evidence/pre-fix/P01-production-generation-red-v2.xcresult`

- 同一组合用例使用生产账号代次输入并临时恢复旧跨域比较。
- 结果：1 FAIL；current session 读取发生，但 start/append/end/ACK/admit/status 写链计数均为 0，状态停在 `syncPaused`，未到 `pendingReview`。

修后同断言：`evidence/post-fix/P01-production-generation-green-v2.xcresult`

- 结果：1 PASS；current GET=2（首个受控网络失败后恢复）、start=1、append=2、end=1、ACK=1、admit=1、status GET=1，最终 `pendingReview`。

最初的 `P01-production-generation-red.xcresult` 与 `P01-production-generation-green.xcresult` 因测试固定策略时间已经过期，均属于夹具漂移，不作为业务红绿证据，原文件保留未覆盖。

## 5. 验证结果

- P01-P04 最终生产生成器组合：3/3 PASS。
- P05-P13 定向边界回归：16/16 PASS。
- OwnerTruth 完整回归：509/509 PASS。
- Live 音频、SDK 内置播放、迟到事件、打断和持续聆听保持性：56/56 PASS。
- 后端会话及 PostgreSQL smoke 合同单测：22/22 PASS；后端工作树保持无本轮修改。
- 模拟器通用目标编译：PASS。
- 通用 iOS 设备目标无签名编译：PASS。

完整 P01-P13 映射见 `reports/P01-P13-执行清单.md`。

## 6. 指纹与证据

- `OwnerTruthContracts.swift`：`48749d9530e450ca69bc169780645ce3ab25d00f0a4dba3bfacd575aa39f8c0d`
- `DreamJourneyBackendClient.swift`：`be04336f0c35a3c54a4fa0b28619d9223022e668099d0bfeec82126933c09d58`
- `OwnerTruthContractsTests.swift`：`30e7a5ddf2f768853078529eb4ab0d95ab912813af1bb1e1963642e371f8562b`
- 模拟器 App 可执行文件：`0d5609139c5bbe3b47631607b9a47630777e01e79380507846dc0ae3226842ee`
- 通用 iOS 设备目标 App 可执行文件：`08684c2e1a5f081e3b33bf07a04a20905a8c20ab11e914f5757bdabe085793cf`
- 最终 OwnerTruth：`evidence/post-fix/OwnerTruth-full-regression-shared-generator.xcresult`
- 最终音频保持性：`evidence/post-fix/Live-audio-preservation-shared-generator.xcresult`
- 最终模拟器构建：`evidence/post-fix/Simulator-build-shared-generator.xcresult`
- 最终设备构建：`evidence/post-fix/Generic-iOS-build-shared-generator.xcresult`

## 7. 未执行项与发布判断

- 隔离 PostgreSQL 真实保存链：BLOCKED。本机无 `DATABASE_URL`，也没有 `docker`/`psql`；未尝试连接生产或未知数据库。
- 真实 Provider SDK 顺序、物理 20 分钟、iPhone 最小闭环：NOT_RUN。
- 生产后端部署：不需要。本轮没有后端代码或合同变化。
- iOS 发布：尚不可据此直接宣布现场缺陷关闭；需先完成隔离 PostgreSQL 保存链和修复版真机最小闭环。

## 8. 最小真机复测（待授权）

1. 保留 App 数据，以修复版原位安装并先就绪脱敏日志。
2. 新开一场 Live，说一条全新合成事实，等待有声回复后手动停止。
3. 同场追踪 current/start、两条 append、end、ACK、admit、status；任一未知写只允许只读核实。
4. 页面原地应到“已进入待确认记忆”，不依赖切页或重启。
5. 进入待确认记忆执行真实 GET，核对本场唯一候选及来源关联；不自动审核。
6. 任一步失败即保存 trace、attempt、最后成功阶段和请求计数，停止依赖该结果的后续验收。

## 9. 局部回退

仅回退本轮三个代码块：恢复 authority 中的旧跨域比较、删除生产账号代次共享函数并恢复原内联实现、撤销三个测试的生产身份装配。该回退会重新引入生产写链必然被拒绝的问题，因此只能用于对照诊断，不能作为发布方案。不得回退现有 lease、FeatureGate revalidation、回执绑定、检查点或未知写只读核实机制。
