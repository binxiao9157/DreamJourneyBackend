# DreamJourney Live 逐轮模拟 run-07 独立复核

日期：2026-09-22。

**结论：符合本轮本地收尾要求。SIM-C1-EVIDENCE-BINDING-01 可以关闭；认可 LOCAL_PASS。按既定范围，没有需要 Sol 继续修改的本地阻塞项。**

本次承接 [run-06 最后收尾要求](2026-09-22-Astra-Live逐轮模拟-run06复核与C1最后收尾.md)。不重新定义已经认可的 A3、B1/B3、C2，也不将本地通过解释为真实模型或真机通过。

## 1. 原 C1 问题已关闭

本次使用 Astra 上轮独立反例脚本，仅将其加载的验收服务器路径指向 run-07，重新执行原四组对照：

| 场景 | 受控 HTTP transport | 后置校验 | 实际 transport 次数 |
|---|---|---|---:|
| support：原事实引用正确 turn 1 | 接受 | 接受 | 1 |
| support：同一事实错引无关问题 turn 3 | 拒绝 | 拒绝 | 1 |
| relation：原事实引用正确 turn 1 | 接受 | 接受 | 1 |
| relation：同一事实错引无关问题 turn 3 | 拒绝 | 拒绝 | 1 |

修前两个错误引用均被接受的证据仍保留。修后拒绝发生在原受控接口及后置检查，没有通过输入超限、未调用接口或改写反例获得假通过。

代码现在按独立场景真值维护事实与允许证据 turn 的关联，校验不再止于“事实存在”和“引用属于本场”。正常批量关系处理先校验 incoming/existing，再为已识别的重复、补充、纠正、撤回继承证据。既有分页责任范围与原文 range/hash 检查保留。

独立重跑的既有探针也通过：伪造事实、缺必要证据、合法 unit 内正文替换、同角色错 canonical 和错误服务器确认 identity 均被识别；正常对照通过。

## 2. 最终版本及短场门禁已核对

- 当前 25 项受保护依赖的整体指纹与 run-07 交付相同；宿主、测试可执行文件及 xctestrun 三项 hash 均匹配。
- 对比 run-06 已保存的依赖摘要，共核对 23 项同路径文件。20 项产品业务/迁移文件均未变化；其中唯一变化文件是后端受控验收脚本。没有看到本轮重新修改保存链、音频、B7 或认证业务的证据。
- 直接读取四份原始 xcresult，均为 1 个测试、0 失败、0 跳过。原始开始/结束时间确认顺序为 short→logical20→short→logical65。

| 本地组合 | 原始链路结果 |
|---|---|
| short→logical20 | 短场 1 条候选；长场 110 用户轮＋110 助手轮，220 轮跨阶段匹配；长场 4 条候选，含短场共 5 条正式记忆 |
| short→logical65 | 独立短场 1 条候选；长场 150 用户轮＋150 助手轮，300 轮跨阶段匹配；长场 17 条候选，含短场共 18 条正式记忆 |

两条链均记录短场凭证在 long 前校验、真实 iOS 客户端候选可见、审核及 Store 重建读回。重复事实分别保留 turn 7/189、7/221 两处证据并各合并为一条候选；logical20 的合法纠正链保留 1/19/119。上述数量是合成场景预期结果，不是产品候选上限。

后端最终回归原始日志为 92 项、OK；独立隔离 PostgreSQL 正式链原始结果为 passed，并记录审核、正式记忆、重建读取与去重。

## 3. 复核范围与后续状态

本次独立执行了轻量离线受控接口/身份探针、当前文件 hash 检查，并读取 Sol 原始测试结果与 PostgreSQL 证据。没有重新启动完整模拟器/HTTP/PG 组合、重跑 92 项回归或重新构建；这些部分属于原始交付证据复核。

当前准确状态继续为：

`LOCAL_PASS / REAL_PROVIDER_NOT_RUN / DEVICE_NOT_RUN / DEPLOY_NOT_RUN`

本地收尾可结束，不需要因本次审查再修改产品。后续真实 Provider、发布及真机属于独立阶段；真机由用户主动发起，不连接或等待手机。后续每次长场仍须先通过同一版本的独立短场待确认记忆闭环，再按用户安排进行物理 20 分钟及后续 65 分钟验收，继续覆盖审核、正式记忆和重建读取。

本次未修改产品源码、未调用真实 Provider、未操作手机、未部署、未访问生产或历史数据、未 commit/push。

## 4. 可核对的证据

- [本次当前指纹、四份原始 xcresult 与探针汇总](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-07/evidence/current-verification.json)
- [原 C1 反例独立重跑结果](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-07/evidence/independent-wrong-binding-result.json)
- [既有 Provider 输入探针重跑](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-07/evidence/provider-input-rerun.json)
- [跨阶段身份探针重跑](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-07/evidence/cross-stage-rerun.json)
- [run-06 与当前依赖文件对比](/Users/gaominge/Documents/liftora/outputs/2026-09-22-astra-live-round-simulation-review/run-07/evidence/run06-run07-dependency-comparison.json)
- [Sol run-07 主报告](../../outputs/2026-09-21-dreamjourney-live-round-simulation-e2e/run-07/reports/2026-09-22-DreamJourney-Live逐轮模拟-run07-C1最后收尾报告.md)
