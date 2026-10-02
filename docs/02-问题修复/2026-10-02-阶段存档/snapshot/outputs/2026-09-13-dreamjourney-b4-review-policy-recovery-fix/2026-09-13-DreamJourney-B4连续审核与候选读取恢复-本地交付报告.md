# DreamJourney B4 连续审核与候选读取恢复本地交付报告

日期：2026-09-13\
结论：`A_LOCAL_PASS / READY_FOR_B4_RETEST`\
范围：仅本地开发、自动化、模拟器、隔离 PostgreSQL 与编译验证

## 1. 边界执行结果

- 未部署生产。
- 未安装或测试 iPhone。
- 未读取、修改、确认、拒绝或清理生产候选、正式记忆、审核历史和 Dead Letter。
- 未 commit、未 push。
- 未改变火山原生 Live 的声音、低延迟、持续聆听、打断和正式记忆快照绑定。
- 未恢复 ASR -> DeepSeek -> TTS 旧串行 Live；文字问答仍由 DeepSeek 生成且不朗读。

## 2. 基线

### iOS

- 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- 分支：`feature/prd-stitch-ui-adaptation`
- HEAD：`5fd061fd869edbe1fc13e8535a47880826581934`
- 当前 tracked diff SHA-256：`32ea64b0313e11eb0fb5bbd6dbae576585812bd89c85249e9d5e8c8b0293b70c`
- 工作区在本轮前已包含大量未提交的 B4/Live/OwnerTruth 修改；本轮未 reset、stash、checkout 或覆盖这些修改。

### Backend

- 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`
- 分支：`main`
- HEAD：`be9670b6ec05e73ab9562943f402e5a9e1346988`
- 当前 tracked diff SHA-256：`3862527d75ae3c622f3d47eca83a3effcf236202bbfb8a3a00810be1b686ad04`
- 当前工作区还包含前序 W00-W09、迁移和 Worker 修改；本轮后端发布范围不能等同于整个脏工作区。

两端 `git diff --check` 均通过。

## 3. 根因与修复

### F1 连续审核假成功

根因证据：修复前 `B4-F1-red.xcresult` 中，第一条审核完成后没有读取新提案：读取次数 `1 != 2`、第二条 base revision `9 != 10`、proposal hash 未改变、第二条提交仍携带 revision 9。旧页面还可能在本地失败时先关闭详情，使用户误以为第二条已成功。

修复：

- 第一条取得持久成功回执后，列表自动刷新剩余候选，要求服务端 proposal 的 `baseMemoryRevision` 至少达到最新正式记忆版本。
- 第二条必须重新打开新 Proposal/Binding，用户再次确认后才生成独立 command 并提交。
- 读取 requestID 与写操作代次分离；旧 GET、旧详情闭包及迟到回调不能覆盖新状态。
- 审核详情只在 `.committed` 后关闭；发送前失败、明确拒绝和 unknown 均保留详情并显示对应状态。
- 每个审核操作拥有独立 command、trace、持久 pending record 和回执归属。

核心位置：

- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
  - `OwnerTruthCandidateReviewUseCase.refresh/receiveInbox/submit`
  - 最低正式记忆修订约束、读取所有权、独立审核上下文和 unknown 核实
- `DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift`
  - 候选详情提交状态、成功关闭条件、第二条新预览及失败保留

### F2 首次策略过期

现场红证据：历史真机日志明确记录候选 GET 在发送前被 `expiredPolicyCache` 拒绝，随后 UI 显示候选不可读；日志中没有候选 GET。该证据保存在 `evidence/baseline/`，没有把后续手工刷新成功写成自动恢复成功。

修复：

- 首次读取遇到经过白名单分类的策略过期时，使用真实 FeatureGate evaluator/store 发起一次有界、单飞策略刷新。
- 同一读取意图保留 trace；每个实际尝试使用单调递增 attempt 和独立 requestID。
- 策略与认证组合恢复共享预算，不能相乘形成无限恢复。
- 恢复失败、取消、超时或主体变化均释放读取所有权；旧回调不能提交 UI。
- 合法策略或凭据更新后，旧 Candidate/Proposal/Binding/选择、关联组预览和确认闭包全部失效。
- 审核写请求不参加自动认证或策略重发。

核心位置：

- `DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`
  - `fetchOwnerTruthCandidateInbox`
  - 认证/策略恢复、attempt 传播、taskCreated/taskResumed/responseReceived 安全语义
- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
  - `OwnerTruthCandidateInboxReadContext` 和最终有效上下文提交
- `DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift`
  - UI trace/attempt 贯通和旧上下文失效

### 不确定写入的只读核实

修复：

- 新增严格鉴权的只读接口：`GET /v2/vaults/{vaultId}/candidates/{candidateId}/decision-result?commandId=...`。
- 服务端按 Owner/vault/candidate/command 查询持久审核结果；`found` 返回已持久回执，`notObserved` 只表示当前未观察到，不能证明写请求未发生。
- 查询路径不调用 decide/activate/effect writer，不产生 INSERT/UPDATE，不补建投影。
- iOS 在 POST 可能已发送但结果未知时只调用该 GET；进程重建后读取本地受保护 pending record，仍不重发 POST。
- 本地 pending record 无法安全保存时，审核 POST 不发送。

核心位置：

- `app/services/owner_truth_candidate_review.py`
  - `OwnerTruthCandidateDecisionLookupResult`
  - memory/PostgreSQL `lookup_decision_result`
- `app/main.py`
  - decision-result GET 路由及 no-store 响应
- `DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`
  - `lookupOwnerTruthCandidateDecisionResult`
- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
  - unknown、持久 pending record 和只读核实状态机

### F3 历史检查点

修复：

- completion checkpoint、closing outbox 和 follow-up 统一扫描并按 product session 去重。
- 缺失、不可读、JSON 损坏、旧 schema 和 scope 不匹配分别分类。
- 每个工作流独立阻断；历史坏记录不删除、不补写、不重放，也不占用新 Live 或候选读取。
- 恢复关闭中任务与开始新 Live 分离，不开启麦克风，不索取新语音会话，不把新表达接入旧 session。

核心位置：

- `DreamJourney/Sources/Modules/Echo/EchoViewController.swift`
  - checkpoint store 扫描
  - recovery service 工作流发现、分类、隔离和恢复

## 4. 自动化与证据

### F1 修复前

- 结果包：`evidence/red/B4-F1-red.xcresult`
- 失败用例：`testCandidateReviewRefreshesRemainingProposalAfterOneV5Decision`
- 原断言：读取次数 1 而非 2；revision 9 而非 10；proposal hash 未刷新；第二条仍提交 revision 9。

### T01/T02 真实 UIKit 组合

- 结果包：`evidence/green/B4-T01-T02-uikit-green-11.xcresult`
- 环境：iPhone 17 Pro Simulator，iOS 26.5，arm64。
- 结果：2/2 PASS。
- T01 页面证据：`evidence/uiqa/B4-T01-T02-uikit-attachments-final/BA233BDD-E887-4CEE-9A18-933662B86FB5.png`，第二条显示 revision 10。
- T02 页面证据：`evidence/uiqa/B4-T01-T02-uikit-attachments-final/6A81F64A-15E1-45F8-AF3C-6CB7B13A2259.png`，第二条详情保留并显示“本条尚未完成提交”，第二个 POST 数为 0。

### iOS 完整回归

- `evidence/green/B4-ownertruth-full-green-serial-final-3.xcresult`：353/353 PASS。
- `evidence/green/B4-audio-owner-lease-green-final.xcresult`：5/5 PASS，验证本轮没有破坏 Live 音频所有权与账户租约保护。
- UIQA：候选列表、V5 详情、关联组、Echo 连续会话均保存在 `evidence/uiqa/`。
- 完整用例列表：`evidence/green/B4-ownertruth-full-tests-green-final-3.json`。

### 隔离 PostgreSQL

- 日志：`evidence/postgres/B4-PG-decision-result-green.log`。
- schema head：0121，测试库和合成 Owner/vault/source/candidate。
- 结果：作用域拒绝、未提交不可见、回滚原子、并发幂等、GET 只读、历史回执稳定全部 PASS。
- 测试未连接生产数据库，未使用真实用户事实。

### 后端完整门禁

- 日志：`evidence/green/B4-backend-full-gate-green-final.log`。
- `Ran 2581 tests in 65.567s`，`OK`；后续合同和 smoke 门禁通过。
- 日志中的合成 evaluator Traceback 属于预期失败路径测试，整体门禁退出成功。

### 编译

- 模拟器通用目标：`evidence/build/B4-build-generic-simulator-final.xcresult`，`buildResult.status=succeeded`。
- iOS 通用设备目标：`evidence/build/B4-build-generic-device-final.xcresult`，`buildResult.status=succeeded`。
- 只有既有依赖和弃用警告，没有新增编译错误。

完整 T01-T27 对应关系见 [T01-T27 执行清单](T01-T27-执行清单.md)。

## 5. 代码及产物指纹

### iOS 关键文件 SHA-256

- `OwnerTruthContracts.swift`：`7070dbbc7209b71b159583000bb476840bbf02ffb6de2df962f22549d9277ac9`
- `MemoryArchiveViewController.swift`：`c3dbcc8ab419437985b3d1ca866f83dcf72f150f26e3d3cf624789635afab842`
- `DreamJourneyBackendClient.swift`：`7c45b11e72890a438412eaf87879fcc32b1c5c1a75b1a8e0b8e26ed05befbdfb`
- `EchoViewController.swift`：`0be3df58611c62dc1a2f6bffd32e22ee21b5792f1c52230967f4960470fe686e`
- `OwnerTruthContractsTests.swift`：`e8bf982f8198ea70b8275349f85aaa76b020f0e38bfe83f15a1057b70c0e599e`

### Backend 关键文件 SHA-256

- `app/main.py`：`dbd2479f913762f07c1cebdfdf89db0e5907314f98ebc567811ba44cab45c55c`
- `owner_truth_candidate_review.py`：`bbbbe27e5fa45fb64ec77316256aa860877e5bc2cb01e70df45bb91eeb896c97`
- PG smoke：`ca4216f45ccdc7753e3aabf38a9604aa38d93de2daae0bca2898fccf9268d310`
- API tests：`e4461139616535fdf2c82e2a13afc4107ba80ede551684552c2605e676d01237`
- PG smoke contract test：`6c890c40a690fce9538d3bc2d2745dafa6842322d623d6920769926e84e0dc1b`

### 编译可执行文件 SHA-256

- 通用设备构建：`1d95938fa73e867cd94d0f575c26d7bd00100592b6763f4c1be48eca89055792`
- 模拟器构建：`d9c038935b27763e0963f76ab16567280c05d6ac6a34cbd6f7e0d09b30022873`

## 6. 部署判断

本轮新增了后端 decision-result 只读接口，因此后续真机复测 unknown 分支前需要部署对应 API 代码。预计无新迁移，也不需要修改 Worker 业务。

本轮未获部署授权，所以没有部署。未来发布前必须：

1. 从当前后端脏工作区中准确识别本轮 API/service/test 差异，不把所有未提交 Worker 和迁移未经核对一起发布。
2. 核对生产 schema、现有 API 镜像和回退构件。
3. 部署后验证路由鉴权、no-store、作用域拒绝和查询只读性，再安装匹配 iOS 版本。

API 未部署时，客户端 unknown 分支会保持“暂无法核实”，不会回退为重发审核 POST或显示假成功。

## 7. 当前验收状态

- B4-1 至 B4-7：保留既有 `PASS`。
- B4-8：`FAIL`，本地通过不能替代同场真机闭环。
- 连续审核第二条真实写入：`FAIL`，等待 D3-D5。
- 首次策略过期自动恢复：`FAIL`，等待自然过期条件下的 D6。
- 已成功一条正式记忆及投影、向量、文字和新 Live 回查：保留既有 `PASS`。
- 历史三条候选重复性：`NOT_RUN`。
- 新查询产生候选的语义处理：`NOT_RUN`。
- F3 生产历史实际来源与全面影响：`NOT_RUN`；仅本地分类、隔离和恢复回归为 `PASS`。

因此本轮准确状态是：`A_LOCAL_PASS / READY_FOR_B4_RETEST`，不是 B4 整体完成。

## 8. 后续真机步骤

获得后端部署及 iPhone 安装授权后，严格按设计 D0-D9 一步一问执行，步骤不隐藏：

1. D0：核对设备、安装包指纹、生产配置和日志，原位覆盖安装，保留 App 数据。
2. D1：进入待确认记忆，只读刷新并核对真实 GET 全链。
3. D2：由用户选择两条愿意审核的候选，只查看第一条。
4. D3：用户确认第一条，核对独立 POST、持久回执和 MemoryVersion，随后刷新第二条提案。
5. D4：用户查看第二条的新 revision/差异，再决定确认；核对第二个独立 POST 和回执。
6. D5：返回列表、审核历史和正式记忆，只读核对两条持久结果。
7. D6：策略自然过期后重新进入，验证无需手动刷新即可完成一次策略恢复并读取候选。
8. D7：验证重新进入/刷新单飞、占用释放和旧回调隔离。
9. D8：用文字及新 Live 回查刚确认事实；文字不朗读，Live 保持声音、持续聆听和打断。
10. D9：停止 Live，核对会后整理不回退、不重复，F3 历史任务不污染新 session。

真实审核仍必须由用户本人查看并点击；测试人员不代替用户确认或修改事实。

## 9. 残余风险与回退

- unknown 查询接口未部署前，真机无法完成其真实服务端闭环。
- F1/F2 的生产现场根因已有证据，但修复是否在真实设备和当前生产策略条件下关闭，仍需 D3-D6 验证。
- F3 只证明坏历史工作流被准确分类并隔离，没有推断或修复生产历史来源。
- 回退仅限本轮 OwnerTruth 审核状态、读取恢复和 decision-result 接线；不得 reset 整个工作区。
- 若策略自动恢复出现循环，应局部关闭自动恢复并准确提示读取失败，不能常开 gate。
- 若审核状态回归，应暂时禁用受影响确认入口并保留候选，不能重发未知写或回滚生产正式事实。
- 无论回退与否，必须保留 typed 判等、显示精度、八操作、关联组原子性、CAS、不可变 Binding、账户隔离和安全日志。
