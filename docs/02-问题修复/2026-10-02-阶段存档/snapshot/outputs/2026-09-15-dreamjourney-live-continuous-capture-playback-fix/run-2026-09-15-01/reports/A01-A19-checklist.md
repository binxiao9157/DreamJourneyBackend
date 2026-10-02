# A01 至 A19 本地执行清单

状态口径同 L1 清单。真实 SDK、可听声音和车机结果必须来自对应环境，helper 或截图不能替代。

| ID | 状态 | 本轮证据与说明 |
| --- | --- | --- |
| A01 | NOT_RUN | `testLateSameQuestionEventsCannotClaimClientInterruptDuringItsReply` 验证核心状态机；尚未以真实 manager + SDK 三种迟到事件完整执行。 |
| A02 | NOT_RUN | 新 Q 身份与重复事件状态机已有覆盖；SDK 的 ID-only 新 Q 正向打断未实测。 |
| A03 | NOT_RUN | UIQA 只验证文字与状态，不提供 SDK 可听 drain 证据。 |
| A04 | PASS | `testLateReplyCannotRewindCurrentProviderQuestionBeforeCorrelation`：Q2 活动时迟到 Q1 reply 不能回退当前身份。 |
| A05 | PASS | 多 segment 状态机保持整答未完成，单句 finish 不结束 reply。 |
| A06 | PASS | `testProviderReplyOnlyDrainsAfterSynthesisAndAllPlayerSegmentsFinish` 覆盖两种先后顺序，双方满足后只 drain 一次。 |
| A07 | PASS | `testLatePlaybackEventCannotDrainCurrentReply`：旧 epoch/reply 的晚到 finish 不推进当前回复。 |
| A08 | NOT_RUN | canonical 累计文本状态机已绿；真实 ViewModel 在 speaking 中连续更新的装配测试未单列。 |
| A09 | NOT_RUN | 生产代码仅在 listening 状态 arm，并在 TTS/用户活动时取消；未提供可控时钟推进 60 秒的 manager 组合证据。 |
| A10 | NOT_RUN | generation、lifecycle、lease、turnPhase 均在定时回调复核；未执行可控时钟的旧 timer 排队反例。 |
| A11 | NOT_RUN | 失败保持 unknown、无 App PCM 降级的代码路径已审计；真实 provider 断网、缺 ID、无 drain 未运行。 |
| A12 | NOT_RUN | 生命周期与账号 fence 由 620 项回归保护；真实 manager 后台/退出/账号切换组合未单列执行。 |
| A13 | NOT_RUN | 未连接或控制原车机。 |
| A14 | NOT_RUN | 未执行扬声器/蓝牙连接、断连、重连和系统中断。 |
| A15 | PASS | DreamJourneyTests 全目标 620/620；既有 delegated、普通 Echo、短答与播放器策略未回归失败，且未恢复旧串行链路。 |
| A16 | NOT_RUN | owner/assistant canonical 状态机及 durable outbox 分别有绿测；真实 manager 到 durable 接缝的单次性组合未完整执行。 |
| A17 | NOT_RUN | 文本 finality 与 playback outcome 已分离；文本完成后真实音频中断和未完成 SDK 错误的组合未运行。 |
| A18 | PASS | pending owner gap、assistant complete 后缀、freezing 中原 ID final 和重建后 gap 均由磁盘测试覆盖；下一场事件由 product session/epoch fence 保持。 |
| A19 | PASS | ACK 后 canonical 去重/冲突及 close intent 后 store 重建均有确定性绿测。 |

## 汇总

- PASS：A04、A05、A06、A07、A15、A18、A19。
- 真实 SDK/可听/车机相关项目：NOT_RUN。
- 历史停音触发机制仍未由 SDK 事件和车机证据唯一定位。
