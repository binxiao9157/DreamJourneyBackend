# DreamJourney DJ-B8-DEVICE-01 本地修复报告

日期：2026-09-16\
状态：**本地验证通过，真机待验收**\
范围：仅修复“文字会话结束后，在 acknowledgement 成功响应之后、candidate admission 完成之前停止”。未修改 Live 未封存、Echo 历史状态归属、B7 过滤、长回答播放、主动打断或车机/CarPlay 行为。

## 1. 基线与边界

- iOS 工程：`/Users/gaominge/Documents/Codex/Video/DreamJourney_dev`
- 分支：`feature/prd-stitch-ui-adaptation`
- 开工 HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 后端工程：`/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend`
- 后端 HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`
- 后端工作树在本轮结束时为空；本轮未修改后端，也不需要新增后端部署。
- iOS 开工前已有多项未提交修改。本轮未 reset、clean、commit、push，也未覆盖或回退前序成果。原始差异保存在 `evidence/baseline/ios-working-tree-before.diff`。
- 未安装或测试 iPhone，未部署，未访问生产业务数据，未清理历史任务或 Dead Letter。

## 2. 根因结论

### 2.1 已确认的源码缺陷

1. candidate admission 在刷新权限缓存后，上层可能判定允许，但下层请求仍复用旧的 route decision。结果是新策略已经允许，业务请求仍可能在 BackendClient/requestJSON 的二次校验处被旧授权拒绝。
2. acknowledgement 或 admission 的写请求已经暴露给网络并收到 HTTP 201/typed 回执后，如果回执无法通过 command、session、batch、vault 或版本绑定校验，旧路径可能将其归为 `notSent` 或允许再次提交。该结果实际应为 `outcomeUnknown`，只能只读核实原命令。
3. 旧诊断无法完整区分 typed 解码、业务绑定、检查点写入、admission 命令准备、授权检查与实际发送；部分发送前失败还会误记 typed decode 失败。

### 2.2 仍待真机确认

上述缺陷能够解释 ack 后 admission 未完成的代码风险，但现有现场日志不足以证明它们是那次真机故障的唯一根因。现场仍需用同一脱敏 trace 同时取得客户端阶段、检查点和经授权的服务端只读请求证据。

## 3. 修改内容

### `OwnerTruthContracts.swift`

- acknowledgement use case 将同一诊断 trace 贯穿命令准备、策略、账号租约、请求、typed 回执、业务绑定和最终分类。
- acknowledgement 的 HTTP 成功回执若绑定不一致或版本关系不合法，归类为 `outcomeUnknown`，保留原 command，不允许写入重试。
- 新增不可变 `OwnerTruthInterviewCandidateProposalAdmissionAuthority`，绑定 fresh `FeatureDecision`、`AccountLease`、operation generation 和 trace。
- admission 的有界策略刷新成功后捕获 fresh authority，并将同一 authority 传到 BackendClient；不再只依赖上层 Bool。
- admission 的已曝光写入发生回执错配、账号变化或合法凭据轮换时，保持 `outcomeUnknown`；重复操作不会创建新 command 或新增 POST。
- 保留策略刷新上限、超时、账号作用域、session/batch/version 和旧回调隔离。

### `DreamJourneyBackendClient.swift`

- acknowledgement/admission 支持接收调用链 trace。
- admission 精确使用上层传入的 fresh decision 完成 requestJSON 二次校验；审核写关闭认证/策略隐式重发。
- 增加安全的 `clientLeaseChecked`、`requestRuntimeChecked`、`requestFeatureChecked`、`typedDecodeSucceeded/Failed` 等阶段事件。
- 只有 requestJSON 实际返回 JSON 后才记录 typed decode；发送前的 private UI、认证、租约、运行策略或 FeatureGate 拒绝不再伪装为解码失败。
- 日志只包含白名单阶段、枚举、attempt、时间及脱敏关联，不记录正文、凭据、完整响应或原始业务哈希。

### `EchoViewController.swift`

- 同一文字会话关闭流程使用一个 `completionTraceID` 贯穿 end、ack、ack checkpoint、admission 和 admission checkpoint。
- 增加 ack 检查点提交、admission 启动、回执验收和检查点提交的明确诊断。
- policy refresh completion 只表示缓存刷新完成；实际 admission authority 在刷新后重新捕获并贯穿下层。
- ack 检查点写入失败时保留已准备坐标，停止 admission，不伪称已完成。

### `OwnerTruthContractsTests.swift`

增加或强化真实 BackendClient、FeatureGate、账号租约、URLProtocol、磁盘检查点和 UIKit 接线的定向反例与保护测试。

## 4. 红绿证据

| 场景 | 修复前 | 修复后 | 原始证据 | 状态 |
|---|---|---|---|---|
| 201 回执绑定失败 | acknowledgement 被归成 contractMismatch；admission 可再次提交 | 两者均为 outcomeUnknown，原命令只读核实且 POST 保持一次 | `evidence/red/B8ReceiptBindingRed.xcresult`；`evidence/green/B8BindingAndExposureGreen.xcresult` | PASS |
| 旧 route denied，刷新后 fresh allow | 下层仍使用旧授权，admission 失败 | exact fresh decision 贯穿 BackendClient/requestJSON，POST 一次并进入 admitted | `evidence/red/B8FreshAuthorityRed.xcresult`；`evidence/green/B8FreshFeatureGateGreen.xcresult` | PASS |
| HTTP 201 但 ack/admission 回执无法绑定 | 可能被当作未发送或允许重试 | 保留 outcomeUnknown，重复操作和 use case 重建均无新增 POST | `evidence/green/B8RealAck201Green-03.xcresult`；`evidence/green/B8DiagnosticSemanticsGreen.xcresult` | PASS |
| fresh policy 明确拒绝 | 无完整真实链路证明 | 零网络请求、状态明确 unavailable/releasePolicyDisabled | `evidence/green/B8FreshPolicyDenialGreen.xcresult` | PASS |
| private UI/auth/subject/app lease/runtime policy 发送前拒绝 | 失败阶段不够精确，可能出现伪 typedDecodeFailed | 各阶段均为 notSent、零 URLProtocol 请求、无 typed decode 事件 | `evidence/green/B8DiagnosticSemanticsGreen.xcresult` | PASS |
| 同主体合法凭据轮换发生在已曝光写入之后 | 结果边界不明确 | 原主体记录保持 outcomeUnknown，无第二个 command/POST | `evidence/green/B8CredentialRotationGreen.xcresult` | PASS |
| ack checkpoint 磁盘写入失败 | admission 进入边界缺少组合证明 | 保留 prepared 坐标，admission 请求为零 | `evidence/green/B8AckCheckpointFailureGreen.xcresult` | PASS |
| admission prepare/checkpoint 存储失败、刷新超时、迟到回调、401 | 既有保护测试 | 保持有界停止和零自动写重放 | `evidence/green/B8DirectedAndUIKitGreen.xcresult` | PASS |

说明：`B8RealAck201Green.xcresult` 和 `B8RealAck201Green-02.xcresult` 是测试搭建期间的编译/全局策略配置失败，不是业务红测；最终相同业务断言在 `B8RealAck201Green-03.xcresult` 通过。

## 5. 验收矩阵

| 编号 | 要求 | 状态 | 证据 |
|---|---|---|---|
| B8-D01 | 旧策略过期/拒绝，fresh policy 允许后仅发送一次原 admission command | PASS | `B8FreshFeatureGateGreen.xcresult` |
| B8-D02 | 旧 route 保持拒绝，新授权只服务当前合法请求 | PASS | `B8FreshFeatureGateGreen.xcresult` |
| B8-D03 | 上层刷新后的 exact decision 到达 BackendClient/requestJSON | PASS | `B8FreshFeatureGateGreen.xcresult`、`B8DiagnosticSemanticsGreen.xcresult` |
| B8-D04 | HTTP 201 typed success但回执 ID/版本绑定错误 | PASS | `B8BindingAndExposureGreen.xcresult`、`B8RealAck201Green-03.xcresult` |
| B8-D05 | ack checkpoint/admission prepare 磁盘失败准确停止 | PASS | `B8AckCheckpointFailureGreen.xcresult`、`B8DirectedAndUIKitGreen.xcresult` |
| B8-D06 | 真拒绝、刷新超时、迟到回调、账号/凭据变化 | PASS | `B8DirectedAndUIKitGreen.xcresult`、`B8FreshPolicyDenialGreen.xcresult`、`B8CredentialRotationGreen.xcresult` |
| B8-D07 | private UI/auth/CAS/app lease/runtime policy preflight 零写入并定位阶段 | PASS | `B8DiagnosticSemanticsGreen.xcresult` |
| B8-D08 | admission 丢失/201 不可绑定后，重复操作或重建不重发 POST | PASS | `B8BindingAndExposureGreen.xcresult`、`B8RealAck201Green-03.xcresult` |
| B8-D09 | 旧未知任务与新文字会话的全局历史状态归属 | NOT_RUN | 属于用户明确排除的第 3 项，不在本轮修改范围 |

## 6. 实际执行结果

### 定向与组合测试

- B8 定向及 UIKit 组合：20 tests，0 failures。结果包：`evidence/green/B8DirectedAndUIKitGreen.xcresult`
- 201 回执绑定、真实 BackendClient、fresh policy、账号轮换及发送前门禁：全部通过。结果包见第 4 节。
- OwnerTruth 完整回归（最终源码）：469 tests，0 failures。结果包：`evidence/green/OwnerTruthFullGreen-06.xcresult`
- Echo/音频保持性：5 tests，0 failures。结果包：`evidence/green/AudioPreservationGreen.xcresult`

主要命令：

```text
xcodebuild test -workspace DreamJourney.xcworkspace -scheme DreamJourney -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' -only-testing:DreamJourneyTests/OwnerTruthContractsTests -resultBundlePath .../OwnerTruthFullGreen-06.xcresult
xcodebuild build -workspace DreamJourney.xcworkspace -scheme DreamJourney -destination 'platform=iOS Simulator,id=67D3337E-0623-4578-9479-31F4CD9033DA' -configuration Debug -resultBundlePath .../SimulatorBuildFinal.xcresult
xcodebuild build -workspace DreamJourney.xcworkspace -scheme DreamJourney -destination 'generic/platform=iOS' -configuration Debug CODE_SIGNING_ALLOWED=NO -resultBundlePath .../GenericDeviceBuildFinal.xcresult
git diff --check
```

### 构建与差异

- iOS 模拟器 Debug：PASS。`evidence/green/SimulatorBuildFinal.xcresult`
- 通用 iOS 设备 Debug（关闭签名）：PASS。`evidence/green/GenericDeviceBuildFinal.xcresult`
- `git diff --check`：PASS，无空白错误。
- 后端修改/隔离 PostgreSQL：NOT_RUN；本轮未修改后端合同或事务，因此无新增后端测试与部署要求。
- 真机安装、复测、生产链路：NOT_RUN。

## 7. 指纹

| 对象 | SHA-256 |
|---|---|
| `OwnerTruthContracts.swift` | `b4f4a73899a9e3effebfa82b2fff792c5d26199ea02565bde5679d68c8803a72` |
| `DreamJourneyBackendClient.swift` | `b51caaf7649bf3471e842f31b572a8809d62b3882ac960df43533676ba168e9e` |
| `EchoViewController.swift` | `7f9e7a3a0c69075523de1c9cefa556468776aabad6f47323b315b9891af29842` |
| `OwnerTruthContractsTests.swift` | `463dc25b8919aeb9e80ad9c50b3d1b07df5976f5935245a803c89e4ebba4d23a` |
| 模拟器 app 主二进制 | `14f8497e4a4eee137c5a1178d8c13ba76afdf9419e4d5ff14c57e5c92f99dd69` |
| 通用设备 app 主二进制 | `1d95938fa73e867cd94d0f575c26d7bd00100592b6763f4c1be48eca89055792` |

## 8. 最小真机复测（待授权）

1. 原位覆盖安装本报告指纹对应构建，保留登录和 App 数据；安装前先准备设备日志和经授权的服务端只读观察。
2. 从正常文字回响入口开始一场新会话，输入一条新的合成事实，只执行一次结束。
3. 核对同一 trace 的阶段顺序：end 回执/检查点、ack 请求/HTTP/JSON/typed/binding/检查点、admission command/fresh policy/preflight/请求/HTTP/typed/binding/检查点。
4. 进入待确认记忆执行真实 GET，确认只有一条与该 session/batch 对应的候选；不能只凭数量或 HTTP 201 判定通过。
5. 若出现 `outcomeUnknown`、`unavailable`、检查点缺失或 trace 断裂，立即停止，不点击重试、不重建会话来补发写请求；保存脱敏 trace、最后成功阶段、客户端检查点和服务端请求次数。

停止条件：发现第二个 command、同一阶段第二次业务 POST、新旧 session 混用、HTTP 201 后绑定未通过、旧回调覆盖当前状态，均立即终止后续依赖步骤。

## 9. 残余风险与回退

- 真机现场唯一根因尚未定位；本地测试证明两个源码缺陷已关闭，不等于 B8 现场通过。
- 本轮未验证真实网络、真实策略时序及设备磁盘异常的现场组合。
- 局部回退仅撤销本轮三处生产代码中的 fresh admission authority、回执 exposure 分类和阶段诊断代码块，同时保留前序账号隔离、检查点、unknown-write 禁止重放和 B6 只读恢复。不得通过恢复自动 POST、删除未决记录或放宽 Binding 来回退。

最终结论：**本地验证通过，真机待验收**。DJ-B8-DEVICE-01 现场状态仍为 FAIL；修复版本真机复测为 NOT_RUN。
