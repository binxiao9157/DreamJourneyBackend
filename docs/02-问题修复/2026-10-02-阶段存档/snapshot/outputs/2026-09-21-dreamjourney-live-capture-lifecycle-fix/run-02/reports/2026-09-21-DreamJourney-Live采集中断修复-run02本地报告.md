# DreamJourney Live 采集中断修复 run-02 本地报告

## 结论

`LOCAL_PASS / PROVIDER_NOT_RUN / DEVICE_SHORT_NOT_RUN / DEVICE_20M_NOT_RUN / DEVICE_65M_NOT_RUN / DEPLOY_NOT_RUN / HISTORICAL_REPROCESS_NOT_RUN`

截至 2026-09-21，本轮复核要求的 R01-R08、CAP-01 至 CAP-15、KEEP-01 至 KEEP-10 已完成本地实现与验证。火山账号已经由用户调整，但本轮没有调用真实 Provider，因此不能把账号调整或受控网络结果写成真实 SDK/Provider 通过。

## 实际根因与修改

1. `DialogEngineManager.swift`
   - 原始 SDK 消息入口冻结账号、场次、generation、question/reply 身份与接收 ordinal。
   - latest observation 与 latest trusted final 分离；interim 不再覆盖可信 final。
   - QueryConfirmed 只处理可绑定文字请求，不再封存语音 member。
   - 助手 Chat reply 分身份缓存；TTS 保持播放来源，不进入记忆 canonical 正文。
2. `OwnerTruthContracts.swift`
   - canonical 观察、封存、delivery eligibility 和 immutable command 分层。
   - close intent 保存精确 accepted-handoff manifest；逐项排空，水位外事件拒绝。
   - conflict、partial、overflow gap 和首错成为可恢复的磁盘事实；不因一个差异关闭整场采集。
3. `EchoViewController.swift`
   - Controller→Coordinator 的冻结交接保持原场所有权；停止先保存 manifest，再等排空后冻结 close sequence。
   - conflict/缺口/磁盘故障阻止 end/ACK/admit，但后续正文仍可持久化。
   - 账号租约在测试/生产装配加载视图前建立，旧回调不能写入替换场。
4. `ConversationMemoryManager.swift`
   - 活动场首错在全局诊断配额竞争中受保护；日志只保留阶段、枚举、ordinal 与脱敏关联。
5. Backend Worker/Live long-memory pipeline
   - 默认 Worker→SourceExtractor→LiveExtractor 传递持久 retry_context。
   - 分批整理的真实响应、证据索引、finish_reason、逐 job 诊断及作用域检查保持严格。
   - admission 冻结并验证 `authority_epoch`；非法/陈旧 epoch 在任何 Source/admission/effect 副作用前拒绝。

## 红绿与证据校正

- R01-R03 修前：`red/r01-r03-production-red.log`、`red/r01-r03-production-red.xcresult`。
- R01-R03 修后：`green/r01-r03-production-green.log`、`green/r01-r03-production-green.xcresult`。
- R05/R06：停止 manifest、overflow、首错配额的红绿包位于 `red/r06-session-quota-red.xcresult` 与 `green/r05-r06-*.xcresult`。
- 全量回归中曾有一条旧断言要求“冻结交接前 close intent 必须为空”。这与权威 CAP-09 合同冲突；正确行为是先原子持久化 close intent + 3 项 manifest，同时 end 为 0。校正后同一测试证明 manifest 排空前不发送 end、冻结正文只落原场，最终全量通过。该项是证据校正，不是把生产代码倒退到旧时序。
- CAP-15 旧证据不能证明真实响应返回原 iOS 客户端。本轮新增双向链，Python 重绑客户端命令数为 0；见 `green/r07-cap15-*.json`。

## 实际测试

- CAP 核心：21/21 PASS，含 100 用户轮逻辑 20 分钟与 150 用户轮逻辑 65 分钟。
- OwnerTruth 全量：554 total，553 PASS，1 SKIP，0 FAIL。唯一 SKIP 是全量进程没有 CAP-15 外部配置；同一 CAP-15 测试已单独真实双向执行并 PASS。
- Echo/音频保持性：42/42 PASS。
- Backend 受影响门禁：192/192 PASS。
- 隔离 PostgreSQL：并发/事务回滚、持久合同重试、短长场候选→审核→正式记忆→Store 重建三组均 PASS。
- 构建：test build、Simulator、generic iOS unsigned 均 `BUILD SUCCEEDED`。
- `git diff --check`：iOS 与 Backend 均 PASS。
- 脱敏扫描：没有发现真实 token、密钥或私人正文；命中项仅为测试源码中的合成 token 字段名/固定 synthetic key。

## PostgreSQL 结果

- schema head：`0122`。
- 并发 Worker 仅一份 extraction/candidate；中途 commit 故障无局部数据；同 job attempt 2 可恢复。
- Provider 等待期间 authority epoch 改变时候选为 0，job 明确 blocked。
- 合同重试保留 attempt-one feedback，预算耗尽不提交候选，未批准历史错误类型被拒绝。
- 正式链：短场 1 候选；长场 40 候选；301 回合/150 用户轮生成 149 候选；审核后共 190 条正式记忆；重建 Store 后读取一致且命令幂等。

## 源码与部署判断

- 指纹：`fingerprints/source-build-sha256.txt`。
- iOS 和 Backend 工作区仍包含此前多轮未提交修改；本轮未 reset/clean、未 commit/push。
- Backend 有 schema `0122` 和 Worker/contract 代码差异，未来发布必须按既定顺序先迁移/后端，再发布 iOS；本轮未部署。
- 回退应按本报告列出的相关代码块和 `0122` 发布单元整体执行，不能只回退 iOS ingress，也不能恢复未知写重放、删除 close manifest 或放宽 B7/Binding/CAS/hash/revision。

## 未执行与残余风险

- 真实火山 Provider：NOT_RUN。账号调整后仍需在后续授权轮验证真实 SDK 事件顺序。
- iPhone 短场、物理 20 分钟、物理 65 分钟：NOT_RUN。
- 部署、生产数据、历史 run04 重处理：NOT_RUN。
- 历史现场首个触发点仍不能唯一归因；本地已经关闭可确定的代码缺口，但不得据此宣称历史故障原因唯一确定。

## 后续真机清单

1. 短场两轮，第二轮补充第一轮；验证候选可见→用户确认→正式记忆可查→重启仍在且不重复。
2. 普通物理 20 分钟约 33 轮，覆盖首/中/后半/尾段和一次纠正；完成同样四段闭环。
3. 密集物理 20 分钟至少 100 用户轮，覆盖重复、补充、纠正、撤回、纯问题与助手排除。
4. 未来物理 65 分钟至少 150 用户轮，至少五项首中尾事实，含后半补充和纠正。
5. 独立故障恢复：短断网、一次认证恢复、账号切换、退出重进；未知写只读核实且业务 POST 不增加。
6. 音频：长回答自然播完、主动打断、恢复聆听和停止后不自动开麦。

以上清单只是准备，不代表已开始或完成真机测试。
