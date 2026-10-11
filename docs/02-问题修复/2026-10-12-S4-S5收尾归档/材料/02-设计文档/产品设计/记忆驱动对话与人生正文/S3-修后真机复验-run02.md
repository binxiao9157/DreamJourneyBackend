# S3 同句跨类型补证：修后真机复验

2026-10-09。**本次明确年份同句跨类型补证专项通过；S3 整体仍 IN_PROGRESS。** 原时期关系失败保留，未用继续确认覆盖原失败。普通 App 已恢复，可拔手机。

## 版本与验证边界

后端最终 `memory_changeset.py` SHA256：`8ab482ed2deef3dffd42403845c295de928297d68d711b8fe148345ef6a3708b`。328 个 app Python 文件与隔离部署逐一一致。只部署独立测试 API，普通 API 容器和版本未替换；无数据库迁移、未 commit/push。

沿用已验证 iOS 隔离构建，产品 Swift 不改。Live 为真实 iPhone 火山 SDK STREAM 合成语音输入、实际返回音频播放；不替代麦克风/扬声器声学验收。本轮未跑物理十分钟。文字 memory-dialogue 开关仍 false，证明既有真实检索与保存/确认兼容，不宣称新文字提示词全量质量通过。

## 结果

| 场景 | 结果 |
| --- | --- |
| 独立短场 | 新纸灯笼/周二/蓝色内容；2用户轮、4正文完整保存，2事实确认、精确版本冷读通过；开麦与两轮实际音频通过；页面稳定“整理完成，已存入待确认记忆” |
| 2016年乌龙茶 | 同一次提取生成2候选；选中经历/event候选确认与冷读通过，另一知识/preference候选未确认。不是该场全部候选已通过 |
| 2025年乌龙茶 | 同次提取也生成经历/知识2候选。原预期temporalChange，实际ADD，测试确认前FAIL。因旧正式是event、新候选是preference，没有建立时期关系 |
| 同场受控继续 | 不重输正文，精确限定原Source和一条经历候选，只允许无目标ADD，独立2025事实确认与冷读通过；不改变原时期关系FAIL，不确认另一个冗余候选 |
| 再次输入相同2025语句 | 真实提取为knowledge/preference，实际提案addEvidence，准确指向刚确认的2025版本1；确认后同MemoryID版本2，无第三条正式记忆 |
| 正文与旧时期保护 | 2025的经历类别保留，剔除来源证据后的正文完全相同；2016完整数据库行与当前版本精确不变 |
| 实际追问 | 手机文字回响问2016/2025分别喝什么；DeepSeek回答“2016年你喜欢喝乌龙茶，2025年你也喜欢喝乌龙茶。”，确有两条本组当前版本引用 |

## 尚未满足的断言

1. **同一输入只呈现必要候选**：2016和2025的首次输入各在同一个 `deepSeekTextMemoryOrganizer` 提取结果中产生经历/知识两条候选。不是两个任务晚到或重复运行；候选创建时间和 extraction_result_id 一致。原始供应商response未另行取得，不能把持久化结果进一步等同于未经规范化的原始JSON。
2. **跨时期关系识别**：2016选中事实是event/occurred，2025是preference/喜欢喝，未形成时期关系；保存无丢失、旧记录未覆盖，但关系门禁没有通过。
3. 原S3其他同义主题、持续补充、各入口异常组合按原矩阵继续，不因本专项通过宣布S3完成，不进入S4。MIC04用户批准暂缓不变。

本轮不扩大产品修改去兼容这些新变化。应先把同源多候选和事实类型规范化的影响范围纳入S3设计，再补反例；不可继续按一个测试句增加零散字符串特例。

## 执行例外与清理

清理脚本最初假设4候选，实际为5候选（2016两条、2025两条、重复一条），在任何写入前断言失败并回滚。复查证明原2016 server-audit本已含两条，先前只关注选中项导致清单少算；沟通中的“稍后出现”已更正。失败脚本/错误和完整提取时序保留。

按五个精确候选ID/决策、两个MemoryID和四个SourceID重新核验后，排除时期组及其问句来源，保留原文、版本、审核审计；非目标正式记忆指纹不变。加上短场，总计5来源/4正式测试事实排除。追问仅问句，无候选/正式新增。组内为验证关联有意保留依赖，组结束后清理。

普通App恢复成功，未卸载/登出；隔离容器和临时nginx路由已撤销，普通服务健康。无待运行设备测试。

## 原始证据

[run-02产物](</Users/gaominge/Documents/liftora/outputs/2026-10-09-memory-dialogue-s3/run-02>)：`result.json`；`device/short-01/{result,cold-readback,server-after-confirmation,cleanup}.json`；各period目录的result、cold-readback、relation-preview、server-audit；`period-final-proof.json`、`period-group-after-repeat.json`、`period-extraction-audit.json`、`period-group-precleanup-candidates.json`、`period-group-cleanup.json`、`isolation-cleanup.json`。原FAIL位于`period-2025/result.json`，继续验证另存`period-2025-resume`。
