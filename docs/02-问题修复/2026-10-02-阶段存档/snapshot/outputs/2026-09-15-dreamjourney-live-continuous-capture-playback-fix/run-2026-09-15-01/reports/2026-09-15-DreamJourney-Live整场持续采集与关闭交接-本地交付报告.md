# DreamJourney Live 整场持续采集与关闭交接本地交付报告

## 1. 状态

- 本地代码缺陷修复：PASS。
- iOS 全测试目标：PASS，620/620。
- 后端受影响回归：PASS，88/88。
- 模拟器 UIQA 冷启动/进程重建：PASS，但为 provider-free 合成入口。
- 模拟器与通用 iOS 设备目标编译：PASS。
- 隔离 PostgreSQL 全链：BLOCKED。
- 真实 SDK、iPhone 20 分钟长场、原车机：NOT_RUN。
- 综合结论：`A_LOCAL_INCOMPLETE`。不标 `READY_FOR_DEVICE_RETEST`，不宣布历史现场缺陷已关闭。

## 2. 基线与边界

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`，分支 `feature/prd-stitch-ui-adaptation`。
- 后端 HEAD：`a25b993922fc90dde1e689d19e51becb68fccdbf`，分支 `main`。
- 开工时两端均有 B4/B6/B7/B8、正式记忆策略等未提交修改。本轮未 reset、clean、commit 或 push，未覆盖已有工作。
- 未部署后端，未安装或运行 iPhone，未访问生产业务数据，未补发或修改历史会话。
- 基线证据：`baseline/ios-baseline.txt`、`baseline/backend-baseline.txt` 及两份 source sha256 文件。

## 3. 已确认根因与修复

### 3.1 已确认代码机制

1. 采集和网络派送原来共享失败终态，append 暂时失败会停止后续采集。
2. 关闭前没有统一的 canonical 槽位、未定稿材料和 durable close intent，无法可靠表达迟到 final、缺口与关闭水位。
3. append 可能已提交但回执丢失时，缺少按原 session、sequence、角色、时间、内容和命令绑定的精确只读状态接口。
4. UI 的旧成功/失败状态不足以区分“本机继续保存”“未知写只读核实”和“存在未定稿缺口”。
5. PostgreSQL 状态读取实现中，start/end operation receipts 查询误落在无关列表方法，真正的 `read_live_delivery_status` 未赋值 `operation_rows`。内存仓储测试无法暴露该错误。

### 3.2 iOS 修改

- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
  - 新增 `NativeLiveCanonicalTranscriptEvent` 与 finality。
  - Outbox V2 固定 capture ordinal、canonical ID、未定稿槽位、close intent 和连续 seal 规则。
  - 同 ID 同内容幂等，异内容冲突；ACK 后保留去重元数据。
  - 新增 typed `OwnerTruthLiveDeliveryStatus`，按完整本地 delivery 语义匹配，不用裸文本 hash 代替。
- `DreamJourney/Sources/Modules/Echo/EchoViewController.swift`
  - `EchoLiveMemoryCaptureCoordinator` 将 durable capture 与网络派送分离。
  - 关闭先写 close intent，再冻结水位；仅允许 stop 前已登记 canonical ID 的迟到 final 完成。
  - unknown append 只读查询，单飞、generation、15 秒期限和迟到回调隔离；不自动补发未知写。
  - 新增 `syncPaused` 与 `coverageGap`，不会把暂时断网伪装成整场终止或完整保存。
- `DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`
  - 接入 exact live-delivery-status GET；只读恢复不触发业务写重试。
- `DreamJourneyTests/OwnerTruthContractsTests.swift`
  - 增加派送失败继续采集、close intent、迟到 final、只读核实、超时/迟到、partial status、gap 顺序、ACK 后去重等测试。

### 3.3 后端修改

- `app/domain/owner_truth/conversation.py`
  - 新增 value-minimized status snapshot/item/operation typed 合同；写回执携带 authority epoch。
- `app/services/owner_truth_conversation.py`
  - 内存与 PostgreSQL 仓储按原 vault/owner/authority/productSession/session 精确读取 delivery 和 start/end receipts。
  - PostgreSQL operation query 已移回 `read_live_delivery_status` 的同一只读 cursor 范围。
- `app/main.py`
  - 新增 `GET /v2/vaults/{vaultId}/interview-sessions/{sessionId}/live-delivery-status`，`no-store`，返回窗口、水位、缺失序号、delivery 和 operation 摘要。
- `tests/test_owner_truth_interview_input_api.py`、`tests/test_owner_truth_conversation.py`
  - API 精确状态和 PostgreSQL 游标查询顺序/组装合同回归。

## 4. 红绿证据

- iOS 修复前：`red/ios-capture-audio-red.log`，派送失败后第二条未被保留；旧 reply 污染当前身份。
- iOS retained-gap 修复前：`red/ios-retained-capture-gap-red.xcresult`，2 条业务断言失败。旁边 `.log` 是沙箱环境失败，不作为业务红证据。
- 后端修复前：`red/backend-live-delivery-status-red.log`，新 GET 路由未分类并返回 503。
- iOS核心绿测：`green/ios-retained-capture-gap-green.xcresult`，2/2。
- 新 gap/ACK 绿测：`green/ios-canonical-gap-dedup-green.xcresult`，2/2。
- iOS 最终全测试目标：`green/ios-full-test-target-final.xcresult`，620/620，0 fail，0 skip。
- 后端最终回归：`green/backend-affected-regression-after-pg-fix.log`，88/88。
- PostgreSQL 仓储新增测试直接调用生产 repository；真实 PostgreSQL 事务仍未运行。

## 5. UI、编译与隐私

- UIQA：`green/echo-continuous-turn-uiqa-smoke/live-fix-uiqa/`。
  - 冷启动与进程重建均完成两轮 typed turn、停止回 idle、拒绝 stale reply、离开再进入。
  - 证明页面和本地接线，不证明真实 SDK 音频或服务端写入。
- 模拟器编译：`build/ios-simulator-build.log`。
- 通用 iOS 设备目标编译：`build/ios-generic-device-build.log`，`CODE_SIGNING_ALLOWED=NO`。
- app 二进制 SHA-256：模拟器 `3ebc6d74905496f86e35f9965542ed08f7d9f10ab0e3d8bc66f611d487692cb4`；通用设备 `1d95938fa73e867cd94d0f575c26d7bd00100592b6763f4c1be48eca89055792`。
- 日志扫描未发现凭据值、正文或原始业务 hash；命中的 `AccessTokenPlugin.swift` 仅为依赖文件名。

## 6. 未完成与发布影响

- L1-R01/R02/R03/R05-R09/R11-R17/R19/R23/R24 的完整设计组合仍为 NOT_RUN，详见 `reports/L1-R01-R24-checklist.md`。
- L1-R18 BLOCKED：当前无隔离数据库连接，且 `psql`、`pg_isready`、Docker、Podman 均不可用。脚本化游标测试不能替代事务、幂等、回滚和并发验证。
- B7 长输入本地回归包含纯问题、跨片纠正、长 transcript 和超长单 turn，均通过；真实 Provider 仍未运行。若组合发布包含当前未部署的 B7 Worker 修改，必须先满足 B7 独立发布门禁。

## 7. 发布顺序与回退

发布顺序：

1. 先发布后端 additive GET 与 authority 回执字段，验证鉴权、`no-store`、精确 session/status 响应和 PostgreSQL 实现。
2. 再发布 iOS；旧客户端不依赖新 GET，新客户端在接口不可用时保留 unknown，不重放写。
3. 只有组合版本包含 B7 Worker 时才另行发布 Worker，并先完成其 Provider/隔离 PG 门禁。

局部回退：

- iOS 按本报告列出的 canonical/outbox/coordinator/typed status 代码块回退；不得删除 V2 outbox、unknown 记录或恢复“网络失败即停止采集”。
- 后端 GET 为只读 additive 接口，可先保留；如必须移除，应先确认没有新 iOS 依赖，且不清理消息、回执或历史表。

## 8. 最少后续验收

1. 新合成 20 分钟原生 Live，跨策略 TTL，持续问答和事实表达，直到用户手动关闭；核对完整 Source、水位、唯一 batch 和候选来源。
2. append 响应丢失/网络切换场景只读恢复，确认无第二次 POST。
3. close 前后杀进程并重开，只读恢复原 productSession，不开麦、不复用到新 Live。
4. 在隔离 PostgreSQL 执行状态查询、并发幂等、回滚和零写副作用验证。
