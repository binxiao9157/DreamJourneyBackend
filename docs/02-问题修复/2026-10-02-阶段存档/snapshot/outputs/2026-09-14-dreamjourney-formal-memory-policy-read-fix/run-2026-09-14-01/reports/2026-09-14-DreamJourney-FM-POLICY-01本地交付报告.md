# DreamJourney FM-POLICY-01 正式记忆只读策略恢复本地交付报告

## 1. 结论

**本地状态：A_LOCAL_PASS / READY_FOR_FM_POLICY_DEVICE_RETEST。**

正式记忆列表、详情、人物记忆归纳已接入同一套有界只读策略恢复；加载中搜索/筛选不再误判账号变化；策略刷新后会为当前合法读取重新捕获 route decision。M01-M28 与 W0-W6 的本地门禁均已足。

这不代表生产或真机问题已经关闭。FM-POLICY-01 的生产真机复测仍为 **NOT_RUN**；B4 整体和 B4-8 状态不变。此前更正写入、投影、向量、文字及新 Live 回查的既有 PASS 不受影响。

本轮未部署后端、未安装或测试 iPhone、未访问生产业务数据、未 commit/push。

## 2. 基线与独立核查

- iOS：`feature/prd-stitch-ui-adaptation`，基线 HEAD `11d0d0051b9be3cce57822dd059472d1e2536866`。
- Backend：`main`，基线 HEAD `a25b993922fc90dde1e689d19e51becb68fccdbf`。
- Xcode：26.6（17F113）；模拟器：iPhone 17 Pro / iOS 26.5（23F77）。
- PostgreSQL：17.10；pgvector：0.8.1；仅本机临时隔离实例，测试后已停止。
- 前序未提交修改已保留。Backend 的 correction/decision-result 相关脏文件早于本轮，不归因于 FM-POLICY-01。

独立核查确认了三个业务问题，而非直接接受设计推测：

1. 三个正式记忆入口在初始 FeatureGate 拒绝时直接结束，资源 GET 尚未创建，候选读取已有的有界策略恢复未被复用。
2. 正式记忆列表将 `isLoading` 与账号合法性写在同一失败分支，加载中再次搜索会进入“账号变化”处理。
3. 策略刷新后，旧 route decision 的重新校验不能把历史拒绝变为新策略下的允许，必须重新捕获本次请求授权。

基线详情见 `evidence/pre-fix/baseline.md`。

## 3. 实际修改

### 3.1 统一只读意图合同

文件：`DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthFormalMemory.swift`

- 新增 `OwnerTruthReadResource`、`OwnerTruthReadContext`、`OwnerTruthReadOutcome` 与脱敏诊断事件。
- 一个读取意图固定 trace、账号作用域、开始时间和整体期限；策略、认证、运行时恢复共享预算，attempt 单调递增。
- `OwnerTruthReadHandle` 使用显式取消。修复过程中发现并用红测证明：若在 `deinit` 自动取消，未持有新返回值的旧调用入口会被意外终止，因此兼容入口不再因返回值被丢弃而取消。
- 列表、详情、人物记忆归纳协议保留旧调用适配，生产 controller 使用新 typed read context。

### 3.2 复用候选读取的恢复基础能力

文件：`DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`

- 将候选读取已有的策略 single-flight、共享恢复预算、整体 deadline 和旧 flight 隔离用于正式记忆只读入口。
- 新增统一 `boundedOwnerTruthRead`，覆盖 list/detail/profile，不为三个页面复制恢复规则。
- 策略缓存成功写入后调用 `freshServerPolicyManagedRequestDecision`，重新捕获当前合法 route decision，旧拒绝不再被复用。
- 策略、认证及资源请求维持同一 trace，不同网络尝试使用递增 attempt；取消、deadline 和迟到结果只完成一次。
- HTTP 响应不存在时不记录 `responseReceived/statusCode`；诊断只保留随机关联标识、资源类别、阶段、白名单原因、耗时和存在时的状态码。

### 3.3 三个 UIKit 入口

文件：`DreamJourney/Sources/Modules/Archive/OwnerTruthFormalMemoryViewControllers.swift`

- 列表、详情、人物记忆归纳各自持有 active read ID、handle 和 generation。
- 列表的搜索/筛选 reset 会明确替换旧读取，取消旧 handle 并启动新读取，不再进入账号变化分支。
- 旧回调必须同时匹配 read ID、generation 和当前 account lease 才能提交 UI。
- 分页在已有读取期间保持单飞；生命周期结束、账号/authority 变化时 fail closed。
- 兼容同步 completion，避免请求在 handle 安装前完成造成错误 loading 状态。

### 3.4 测试

文件：`DreamJourneyTests/OwnerTruthContractsTests.swift`

新增 M01/M02/M03 主反例与覆盖三入口、deadline、乱序搜索、DNS 诊断、真实策略 HTTP 解码/缓存、认证组合恢复、共享策略 flight/取消、旧入口兼容性的测试。所有测试使用隔离缓存、受控 URLProtocol 或模拟器 UIQA；未访问生产内容。

## 4. 红绿证据

| 反例 | 修复前 | 修复后 | 结论 |
| --- | --- | --- | --- |
| M01 初始策略过期 | `evidence/pre-fix/M01-M02-red.xcresult`：资源 GET 未恢复 | `evidence/post-fix/M01-M02-green-02.xcresult` | PASS |
| M02 加载中搜索 | `evidence/pre-fix/M02-red.xcresult`：进入错误状态 | `evidence/post-fix/M01-M02-green-02.xcresult` | PASS |
| M03 旧 route decision | `evidence/pre-fix/M03-route-cache-red-02.xcresult`：仍复用旧拒绝 | `evidence/post-fix/M03-route-cache-green.xcresult` | PASS |
| M04 旧入口丢弃 handle | `evidence/pre-fix/M04-legacy-handle-red.xcresult`：请求被隐式取消并超时 | `evidence/post-fix/M04-legacy-handle-green.xcresult` | PASS |

第一次 M02 红测因测试页面尚未创建 UISearchBar 而失败，属于 fixture 错误，不作为业务红证据；修正 fixture 后的 `M02-red.xcresult` 才是有效红测。第一次 M03 结果包存在测试代码编译错误，也未冒充业务红测，采用 `M03-route-cache-red-02.xcresult`。

## 5. 自动化、UI 与编译

### 定向与真实组合

- M01-M03 重检：`evidence/post-fix/M01-M03-recheck.xcresult`，PASS。
- M17/M25：`evidence/post-fix/M17-M25-targeted-02.xcresult`，PASS。
- 真实 FeatureGateService + evaluator + 隔离策略缓存 + BackendClient + URLProtocol：`evidence/post-fix/M01-M14-real-integration-01.xcresult`，3/3 PASS。
- 旧入口兼容：`evidence/post-fix/M04-legacy-handle-green.xcresult`，1/1 PASS。

测试调用使用 `DreamJourney.xcworkspace`、`DreamJourney` scheme 和上述 iPhone 17 Pro 模拟器，通过 `-only-testing:DreamJourneyTests/OwnerTruthContractsTests/<test name>` 固定定向场景；结果均保留为 xcresult，而非只保留终端摘要。

### 完整受影响回归

执行 OwnerTruthContractsTests 与 AudioOwnerLeaseModelTests：

- 结果：418/418 PASS，0 FAIL，0 SKIP。
- 证据：`evidence/post-fix/ownertruth-audio-full-regression-02.xcresult` 及 `ownertruth-audio-full-regression-02-summary.json`。
- 后端相关门禁：62/62 PASS，证据 `evidence/post-fix/backend-formal-memory-policy-gates.log`。

后端门禁覆盖 `test_owner_truth_formal_memory`、`test_owner_truth_formal_memory_api`、`test_owner_truth_person_memory_profile` 和 `test_release_policy`。

### 模拟器 UIKit/UIQA

目录：`evidence/post-fix/formal-memory-uiqa/fm-policy-20260914-03/`

- 正式记忆列表、详情、当前版本、3 条历史版本：PASS。
- 人物发布编排与预览保持性：PASS。
- 截图：`01-formal-memory-list.png`、`02-formal-memory-detail.png`、`03-publication-composer.png`、`04-publication-preview.png`。
- 结果 JSON 明确记录页面可见性；人工检查截图为非空、布局无重叠。

### 编译

- 模拟器 Debug + `UI_QA_SIMULATOR`：BUILD SUCCEEDED，证据 `formal-memory-uiqa/fm-policy-20260914-03/install/build.log`。
- 通用 iOS 设备目标：BUILD SUCCEEDED，证据 `evidence/post-fix/generic-ios-device-build-02.xcresult`。
- 未向 iPhone 安装构建物。

## 6. 隔离 PostgreSQL 只读合同

- 正式记忆既有 PostgreSQL smoke：PASS，证据 `formal-memory-postgres-smoke.log`。
- list/detail/profile 使用合成账号和隔离数据库读取；读取前后对 OwnerTruth 与发布业务 schema 的表做快照，结果一致，`zeroWrite=true`：PASS，证据 `formal-memory-read-only-postgres-contract.log`。
- 数据库 schema head 为 0121，已应用 121 个迁移。

一次额外的数据库级 `default_transaction_read_only=on` 尝试失败，因为现有权限读取会执行 `SELECT FOR SHARE`，PostgreSQL 将行锁也禁止在严格只读事务中。该结果保留在 `formal-memory-read-only-strict-transaction-not-applicable.log`，未伪装成业务失败；最终验收使用业务表前后快照证明零写入，同时保留权限行锁合同。

## 7. 日志与指纹

- 脱敏扫描：PASS，见 `evidence/post-fix/redaction-scan.md`。
- 源码与构建指纹：`evidence/post-fix/source-build-sha256.txt`。
- iOS 源码关键 SHA-256：
  - `OwnerTruthFormalMemory.swift`：`1f998f71b9dfd556d40365c930ccff1e7c391df9e2ae298bbd819b84385b3ea7`
  - `OwnerTruthFormalMemoryViewControllers.swift`：`d3c92dcd754a7de40dcf6cae558d43907e5d7b1cc76353fdba033b58ad60f610`
  - `DreamJourneyBackendClient.swift`：`307aa0ddea5a3c21b0311dd134f7d89ded18057e973916028d107ddfe5607b8c`
  - `OwnerTruthContractsTests.swift`：`a201a0f287eaf887b7c93fa55dfb4a1e831a89bd8461d0b3af46c1e42b14bfe1`
- 模拟器二进制：`b349544a9ea11f58af797965e2eea9f1b270a7fb7c4cea584e774fedd66abbbd`。
- 通用设备二进制：`1d95938fa73e867cd94d0f575c26d7bd00100592b6763f4c1be48eca89055792`。

## 8. M01-M28 / W0-W6

完整逐项映射见同目录 `M01-M28-W0-W6执行清单.md`。M01-M28 与 W0-W6 本地项均为 PASS；其中 M18、M19、M28 的结论来自真实 controller 接线、组合保持性回归与 UIQA，报告中明确区分于单独命名的定向用例。

## 9. 部署判断与未完成项

- 本轮没有后端 FM 业务代码变更，不需要为 FM-POLICY-01 新增迁移或部署后端。
- iOS 变更尚未形成生产安装版本；生产真机复测必须在后续明确授权后执行。
- 前序 B4 的两个 decision-result 接口仍属于待部署依赖，本轮未改变其生产状态。
- 生产中此前出现的“发布策略拦截”历史失败层仍需真机安全日志和服务端只读观测复核；本地通过只证明代码路径满足合同，不宣称历史根因已在现场复现。
- FM-POLICY-01 生产真机：NOT_RUN。
- B4-8 与 B4 整体：保持此前状态，不升级。

## 10. 局部回退

如需回退，仅撤销本轮四类 FM-POLICY-01 修改：正式记忆 read context/handle、BackendClient 的 bounded formal read 适配、三个 controller 的读取所有权接线、M 系列测试。不要回退前序脏文件整体，也不得用 `git reset` 或覆盖文件；应按代码块逐项反向修改，以保留候选审核、关联组、更正预览、typed 差异、CAS、Binding 及诊断链路的既有成果。

回退后会恢复已知风险：策略过期时三入口直接失败、加载中搜索误报账号变化、刷新后旧 route decision 继续生效。因此除非出现新的高优先级回归，不建议回退。

## 11. 下一步停止点

当前停在 **A_LOCAL_PASS / READY_FOR_FM_POLICY_DEVICE_RETEST**。下一步需单独授权安装与真机复测，并保持步骤可见：依次验证正式记忆列表、详情、人物记忆归纳在首次策略过期时自动恢复，再覆盖加载中搜索/筛选和策略+认证组合。未取得设备与服务端联合证据前，FM-POLICY-01 不标记现场 PASS。
