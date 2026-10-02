# Live 开麦续修独立复核与剩余任务

日期：2026-09-29。问题编号：DJ-LIVE-START-20260924。核对对象：run-03 的 closure-report-04、mic-matrix-closure-04、all-ios-final-07 和 four-scene-final-04。

**结论：修复方向正确，R1 的最终检查被忽略问题已局部修正；上轮全部本地要求尚未完成，LOCAL_INCOMPLETE 正确。** 不能因最终套件零失败、普通四场保存通过，就把未执行或未到达目标阶段的故障组合算作通过。也不能因此认定已通过的短长保存链又被改坏。

本轮仅核对源码、原始测试产物、指纹和既有本地 PG 探针，更新复核材料与登记册。未改业务代码，未运行 App/完整测试束，未启动 API/PG，未连接手机、调用真实 Provider、部署、处理生产/历史或 commit/push。

依据：[上轮 R1—R4 要求](../../2026-09-28-dreamjourney-live-mic-start-review/run-03/复核结论与Sol收尾提示词.md)、[Sol 续修报告](../../2026-09-27-dreamjourney-live-mic-start-repair/run-03/closure-report-04.md)、[续修 MIC 矩阵](../../2026-09-27-dreamjourney-live-mic-start-repair/run-03/mic-matrix-closure-04.md)。

## 1. 已核实的进展

| 项目 | 本轮核对结果与证据边界 |
|---|---|
| R1 最终提交守卫 | 生产 Manager 已调用共用 `performStartSubmission`。该方法在安装 ingress 后再次检查 operation/截止，失败不执行 send。原源码片段业务红与现行共用提交方法绿均有产物；当前 XCTest 确实推进时钟跨截止并断言发送 0 次。认可这个局部缺陷修正，不再要求重复修同一处。 |
| iOS 最终完整束 | 原始 all-ios-final-07：774 总项，771 通过、0 失败、3 跳过。不是仅按报告文字采信。 |
| 四场保存 | 四份 xcresult 各 1/1；runner 顺序为 short-A → logical20 → short-B → logical65。每个长场前独立短场两轮补充，候选可见、审核、正式记忆及 API 重建回查有证据。20 场 4 条长候选、含短场 5 条正式记忆；65 场为 17/18。模型是 loopbackHTTP，不代表真实 Provider 或物理时长。 |
| 源码与构建 | 独立重算 1,297 项源码依赖、312 个制品及两场配置，全部匹配。源码 `9b46bb06354b51349ca9a222a31e99550e10a0600b319866d4923dbefe0e0b03`，构建 `70cbc9172c508fb517495932effaeaa09beb639fc69105cf43a9903bb7e02eb6`；两仓库 diff 检查通过。 |
| 精确原因与可见提示 | MIC-03/13 原始定向产物 2/2 通过。限于命名分支，不能扩大为所有错误分支已验证。 |
| 策略等待截止 | MIC-09 原始产物 1/1，实际代码经过 Client/FeatureGate/attempt；单调截止后迟到刷新不会创建 ticket。没有执行完整 Controller 多阶段累计等待，保留该边界。 |
| 诊断磁盘不可写 | 已经过 Controller/capture，目录被普通文件占用时记录诊断失败，并保留静态 A 的磁盘记录。1/1 通过，较上轮只测 attempt 有进展；活动 A 排空仍未证明。 |
| PG 并发与身份 | 既有隔离 PG 探针代码与保存结果相符：两请求经屏障进入默认 API，200/200，最终 issued/revoked 各一，仅一张可消费，跨用户 403。认可这些命名分支，不扩大为所有事务并发或生产已验证；本轮未重跑 PG。 |

独立复核产物：[原始测试汇总](xcresult-review.json)、[当前源码、构建和配置重算](current-source-build-check.json)。通用设备构建成功仍以 Sol 保存的执行报告为来源，本轮没有重新构建。

## 2. 必须继续完成的三组本地工作

### A. R2：完整生产 Manager 编排与受控 SDK 回调

当前测试执行的是共用提交方法；其后 `failStart`、`cancel`、`complete` 仍由测试直接调用。原生 Manager 的 setup、回调代理、SessionStarted、cancel/complete、音频和 ingress 清理尚未串起来执行。

保留已通过提交方法，只在必要边界接入可控 SDK/clock/事件回调，执行生产使用的编排。按原有限场景验证：setup 跨截止、指令返回失败、SessionStarted 缺失、取消后迟到 success/error、成功移交与截止竞速、旧 operation 不影响新 operation。断言 send/stop、greeting、ingress、音频 lease 与一次终态。不要在测试里重写一套更正确的流程，也不要求手机或真实供应商。

清理副作用需直接检查。例如当前 [closeRawCanonicalIngressBinding](</Users/gaominge/Documents/Codex/Video/DreamJourney_dev/DreamJourney/Sources/Services/DialogEngineManager.swift:5545>) 内，router.close 有 expected operation 保护，但其后的 assistant ingress state reset 无条件执行。现有替换 operation 的 helper 测试不会运行这个实际清理方法。应在原“旧 operation 不伤新场”用例中检查新场缓冲/终结去重状态；目前只确认此实现差异，未证明历史现场触发或已造成正文丢失，不另立历史根因结论。

### B. R3：活动 A 保存与失败 B 的生命周期

原始 `mic19-active-a-first-04.xcresult` 为 1 失败：B 未达到 SDK started/failed 预期，newCoordinator 为空，A 自己的 end 未观察到。随后恢复的测试将 A 的两个 policy closure 设为 false，主要核对预置磁盘记录相等；它通过并不等于活动 A 场景已通过。失败发生在测试目标阶段未达成时，现有证据不足以归因产品或供应商。

先让 A 在真实 Controller/Coordinator/Client/磁盘路径到达可观察的 append/close/整理在途屏障，确认 checkpoint 等前置条件有效；再按原三种 B 失败（发前 deny、超时、创建 capture 后 SDK 失败）执行。核实 B 不替 A end/ACK/admit、不清 A 坐标或预算，A 可正常继续，B 只结束自己的 capture。使用无 B 失败的同阶段对照或归属轨迹区分 A 自然推进与 B 干扰，不能要求活动 A 的全部状态永远不变。

修正装配后保留原断言和失败产物；若暴露业务缺陷，再最小修改业务代码。不得改回静态 A、删断言或增大固定等待后把本项标 PASS。

### C. R4：原矩阵剩余有限组合

依照 mic-matrix-closure-04 的 PARTIAL 项补精确断言，不扩大为无限时序排列：

- 共用刷新中取消第一个等待者，第二个仍能完成，且不串用账号/attempt。
- runtime、policy、auth、SDK 阶段共享单调总截止；分阶段推进时钟，验证累计耗时、墙钟变化不影响截止、取消回执和迟到结果归属。
- 有限终态竞速保持一次终态/首错；账号轮换后原因和剩余预算正确；已有 ticket 未知结果保护及首/次请求响应归属不回退。
- 原健康路径的请求次数、ticket 绑定与阶段测量按原要求补证，不以全量测试数字代替。

已证明的诊断隔离、原因提示、PG 同用户并发/跨用户拒绝直接链接复用；MIC-16 的有限本地诊断机制与 MIC-17 的既有本地音频保护也不要重列为完全未做。原生 SDK 现场时序、声学及历史一分钟首因不是本地任务依赖。

## 3. 保留的异常与交付校正

- all-ios-final-06 中恢复入口用例曾在 acknowledgementPrepared 得到 2 GET，断言为 1；孤立重跑和 final-07 通过。确认失败产物存在，根因没有因此消失。后续检查该用例的实际完成屏障、请求归属和合法阶段转换；若涉及受保护 GET 预算，按原断言处理，不能通过扩大 GET 上限掩盖。不要凭一次失败宣称产品根因，也不要凭一次重跑宣称永久修复。
- `fingerprints.md` 仍记录 final-03 / 770 的旧指纹与命令，而最新身份在 closure-report-04 和 final-source-build-link-04。交付时标明历史版本并建立唯一最新入口；这是文档一致性修正，不要求重写旧证据。
- 剩余专项先通过，再冻结最后依赖并运行一次适当的受影响回归和同版四场。只改文档、依赖完全没变才按指纹复用；每个长场前独立短场门禁保持。
- 达到原本地要求即可 LOCAL_PASS，并明确既有路由基线例外和所有外部 NOT_RUN。历史近一分钟等待、23:04 单次失败、seq5 首发原因继续未决；不因它们缺证无限延长本地收尾，也不把本地通过当现场问题关闭。

## 4. 给 Sol 的提示词

```text
继续 DJ-LIVE-START-20260924 本地收尾。先完整阅读：
/Users/gaominge/Documents/liftora/outputs/2026-09-29-dreamjourney-live-mic-start-review/run-04/复核结论与Sol剩余任务.md

认可并保留 closure-04 已通过的 R1 共用提交守卫、原因/提示、策略等待截止、诊断磁盘不可写静态保护、PG 并发身份验证和 final-04 四场保存链。不要重新修已闭合部分，也不要仅重复跑全量增加通过数。

集中完成第2节 A/B/C，范围沿用上轮 R2—R4：让生产 Manager 实际编排接到受控 SDK/clock/callback，检查完整启动、截止、取消、移交及清理副作用；修正 MIC-19 活动 A/B 场景前置装配，保持原断言，证明 A 继续保存、B 不清 A 且正常结束自己；补共享刷新等待者、累计单调截止、有限竞速和现有请求归属/预算断言。旧 operation 清理要核对实际 ingress 缓冲，不只检查 helper 的 ID。未达目标阶段的测试失败先定位装配和产品各自证据，不能删除失败或换回静态场景宣称完成。

保留 final-06 的 2 GET/1 GET 失败，检查完成屏障与请求所属阶段，不扩大 GET 或写重试预算。按第3节统一最新报告、矩阵和指纹入口。专项闭合后再冻结最终源/构建/配置，完成受影响回归及 short-A→logical20→short-B→logical65，每次长场前独立短场两轮补充，候选→审核→正式记忆→重建回查不降低标准。

持续完成可独立执行的本地工作后交付具体修改、同断言红绿、精确矩阵、残余异常归因、最终指纹和准确状态，并更新登记册。不重写保存链、不全局重构、不扩为无限故障组合。原要求满足即 LOCAL_PASS，保留基线例外和外部 NOT_RUN；历史首因不伪称解决。

本轮不调用真实 Provider、不部署、不连接/检测/等待手机、不操作生产或历史、不 commit/push。手机和真实模型现场验收由用户后续主动发起，无手机不是停止本地工作的理由。
```
