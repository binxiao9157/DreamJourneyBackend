# DreamJourney Live 逐轮模拟 run-05 验收收尾报告

## 结论

`LOCAL_PASS / DEVICE_NOT_RUN`

run-04 已确认的保存系统成果继续保留。本次只完成 A-D 验收缺口，并在真实本地装配暴露产品缺陷时做最小修复。最终版本分别按 `short→logical20` 和 `short→logical65` 执行，全部本地门禁通过。

## 本轮局部修改

### iOS 产品代码

- `DreamJourney/Sources/Modules/Echo/EchoViewController.swift`
  - 同账号认证恢复后，用新鲜 authority epoch 继续原 append 命令。
  - 保留账号、thread、session、sequence、正文、版本和生命周期绑定；不放宽账号切换。
- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
  - delivery-status 使用同场与单调版本校验，不要求合法刷新后的 epoch 与旧 receipt 完全相等。
  - Live turn content hash 改为与后端一致的 Unicode code-point canonical JSON。
  - 绑定拒绝日志只输出安全布尔阶段，不输出正文、身份、token 或原始 hash。
- `DreamJourneyTests/OwnerTruthContractsTests.swift`
  - 增加后端固定向量 hash 回归，并补强认证、未知写和逐轮装配断言。

### 后端产品代码

- `app/services/owner_truth_conversation.py`
  - 正式记忆重建读取 SQL 使用正确的 `current_thread_id`。

### 本地验收工具

- `run-05/tools/cap15_round_simulation_server.py`
- `run-05/tools/run_round_simulation.py`
  - 统一磁盘配置、完整源码/宿主构建绑定和 fixed-order gate。
  - 增加 raw→disk→HTTP→Source 逐轮账本、真实 iOS 候选读回和隐藏候选负例。
  - 受控模型校验实际请求正文和证据索引。
  - 注入生产共用逻辑时钟、真实 HTTP 401/认证刷新、已保存响应丢失与只读恢复。

其余 Git 工作区修改均为前序未提交成果，本轮没有 reset、clean、整文件覆盖或回退。

## 主链结果

- logical20-short：2 用户回合/4 总回合，1 条候选，PASS。
- logical20：110 用户回合/220 总回合，4 条候选；组内正式记忆总计 5 条，PASS。
- logical65-short：2 用户回合/4 总回合，1 条候选，PASS。
- logical65：150 用户回合/300 总回合，17 条候选；组内正式记忆总计 18 条，PASS。
- 两个 long 均完成跨批重复、补充、纠正、撤回和后半场证据验证。
- 两个 long 均经过真实 iOS controller/BackendClient 读取候选、隔离审核、正式记忆和 store 重建读取。

## 故障注入

- logical20：最后一条 append 在业务 handler 前真实返回 401；实际调用 `/auth/refresh` 一次，以原 command 重试并得到 201。
- logical65：第 300 条 append 服务端已保存后丢失响应；客户端只读 delivery-status GET 核实，业务 POST 计数保持 1。
- 429/timeout：后端 typed transient、预算和恢复测试通过。
- 负例：short receipt、配置/构建变更、漏中尾段、错 Source、隐藏候选和模型输入篡改均被确定性拒绝。

## 回归与构建

- OwnerTruth iOS：564 executed，0 failed，3 skipped。
- Echo/音频/账号：95/95 PASS。
- 后端 Live pipeline + candidate worker：92/92 PASS，5.917 秒。
- 固定 canonical hash 定向测试：1/1 PASS。
- Simulator build-for-testing：PASS。
- 通用 iOS 设备无签名 build：`BUILD SUCCEEDED`。
- `git diff --check`：iOS、后端均 PASS。

## 指纹

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- 后端 HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`
- 最终 source fingerprint：`c710f3c5cb1137c83775452c66a685ff57420b3deb14321aef8380687ad1b409`
- host build fingerprint：`ac8ffbfd09c8019bcc5ee16d54f033b5b6b5554ace4507aa2d913dfb87fc0781`
- 详细文件与构建 hash：`artifacts/source-and-build-fingerprints.json`。

## 发布与回退

- 本轮没有部署。iOS 行为修复需要后续发布新的 iOS 构建。
- 后端当前工作区包含长场 pipeline、迁移和本轮 SQL 修复；完整功能发布仍需按既定顺序迁移/后端→iOS，当前均 `NOT_RUN`。
- 局部回退应只回退上述代码块和 run-05 工具，不得恢复旧 authority 严格相等、跨语言错误 hash 或未知写重放。

## 未执行边界

- 真实 Provider：`NOT_RUN`。
- 真实 iPhone/真实 SDK 事件顺序：`NOT_RUN`。
- 物理 20 分钟、物理 65 分钟：`NOT_RUN`。
- 部署、生产数据、历史失败任务、Dead Letter：`NOT_RUN`。
- commit/push：`NOT_RUN`。

逻辑时钟与受控模型证据不能替代真实 Provider 或真机，但已满足本次本地 A-D 验收范围。
