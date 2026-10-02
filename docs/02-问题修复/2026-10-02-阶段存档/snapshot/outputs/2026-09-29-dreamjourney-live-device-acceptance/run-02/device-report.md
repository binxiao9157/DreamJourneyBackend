# DreamJourney 安装与真机验收 run-02

日期：2026-09-29。范围：用户授权当前测试/演示账号、静音数字 PCM 经真实 iPhone SDK、先短场完整闭环再物理 20 分钟。声学麦克风/扬声器另验。

## 结论

**DEVICE_SHORT_FAIL / DEVICE_20M_NOT_RUN / DEVICE_65M_NOT_RUN。**

这次的直接断点是首轮回复已合成、文字已显示后，未观察到原生播放器完成回调，页面持续为“回响正在抵达”。本场 ASR 正确、两条正文已确认；测试停止后，后台 end、ACK、admission 及候选发布成功，产生 **2 条 pending 候选**。这与“本场完全没有保存或整理”不同。

但尚未验证候选在 iOS 待确认列表中的可见性、内容与事实精确匹配、人工/自动审核、正式记忆及冷启动回查。不能把服务器两条候选直接视为短场完整 PASS。短场失败后未发送第二轮、未启动长场、未强制审核。

## 安装与运行身份

- 原始 iOS 业务代码本轮未改。复用 run-01 签名 stage，安装时保留 App 数据；签名验证成功，有效期至 2026-10-01 01:58:44 UTC。
- 复用前核对源码、工具、stage、可执行文件和完整签名包；详见 [reused-build.json](reused-build.json)。源码 bb29f2c44deed4595fcb5a699886f0a485e0e6b65202bff7cd7b57daa9040a1e；签名包 dc0fcc1ce50948ea0ea16a810fdb15cfe139d731401a7bd9eecdbee5f355bfaf。
- 后端沿用上一轮已部署 api-pool-isolation-20260929 修复版本，本轮只读确认 949 个运行文件与交付指纹完全匹配，没有重复部署。schema 0124。
- 测前九次 /live、/ready、release-policy 读取均 200；测后本机 /live 为 200、0.002822 秒。这是本次观测，不是持续可用性 SLA 或所有历史停摆关闭证明。
- 工具本地安全测试 16/16；数字音频通过实际 iPhone STREAM SDK。未注入识别结果、播放完成回调或候选数据。

## 三次独立尝试

| 目录 | 结果 | 边界 |
|---|---|---|
| SHORT | 开麦后音量变为 30%，静音门禁失败 | 已启动 Live，但未发送用户语音；同 trace 服务器 ticket 298ms 返回200，手机约4.109秒进入聆听 |
| SHORT02 | 音量预检仍为30%，失败 | 未启动 Live，未发送用户语音 |
| SHORT03 | 用户在 App 前台实体键调零后，静音门禁通过；首轮回复完成门禁失败 | 真实 ASR/文字/解码音频存在，播放器完成回调0；已停止 Live |

原始证据分别保存在各目录 result.json 和对应 runner log，不用后一次结果覆盖先前失败。SHORT03 的失败为 timeout_ASR_answer_TTS_listening，总耗时105.604秒。

## 第一轮实时性与停止后保存

SHORT03 runID：lab-b57db76c69914f748a3aeae2c217d992。

productSession：echo_live_84fc659254818075ed9d43113e0837f1。

| 观察点 | 结果 |
|---|---|
| 语音输入开始/结束 | 场次第4.149 / 12.684秒 |
| ASR final | 第14.578秒，距语音输入结束1.894秒；两个必要词均匹配 |
| 用户文字显示 | 第14.601秒，距语音输入结束1.917秒；必要词匹配 |
| 助手文字显示 | 第14.605秒，距语音输入结束1.921秒；存在有效显示记录 |
| 首次回复解码 PCM | 第14.599秒，距语音输入结束1.915秒 |
| 非静音解码 PCM | 885456字节，包含问候238416字节；不等于物理扬声器播放完成 |
| TTS ended / 原生完成 | 共2次合成结束（含问候），播放完成0次 |
| 上行帧最大间隔 | 43.982ms；按20ms目标喂入，不能据此宣称无抖动 |
| 失败前手机采集快照 | 用户1轮已落本地，成员2条，服务器确认2条，排队0条 |
| 停止后服务器只读核对 | session ended；owner 1 + assistant 1；batch acknowledged、through_sequence=2；admission 1 |
| 候选整理 | live run published；planned units=2，provider_request_count=3，recovery=0，failure_code=null |
| 待确认/正式记忆 | 同一 Source 的 pending候选2条，正式记忆0条；没有审核 |

服务器只读证据采集于 2026-09-29 06:26:50 UTC，筛选仅限本场身份，不回传原始对话、密钥或其他历史数据。正式记忆0条是本轮没有审核的结果，不是已证实的正式记忆写入故障。

原始 result.json 在异步停止链完成前取样，其 closeIntentPersisted=false / endRequested=false 不证明停止持久化失败；后续服务器已证明 end、ACK、admission 与候选发布完成，两份证据必须一起读。

## 归因及待核验

已确认：不是本场 ticket 无响应；不是本场正文完全未落库；不是本场候选整理超时或无产出。真实模型交互已经发生，因此不能整轮写成 REAL_PROVIDER_NOT_RUN；但完整供应商合同、长会话容量、各类语义与耗时仍未验收。

直接断点：真实 SDK 当前场诊断没有 3019/3020 原生播放器开始/结束事件；产品仍保持 speaking，工具也未收到 onTTSFinished。合成结束不能替代声音播放排空，因此没有绕过门禁。

根因尚未确定：需进一步区分 SDK 参数/事件契约、STREAM模式与解码观察带来的行为、静音音频路由、产品回调归属和状态转换。当前不能断言为火山服务故障、手机硬件故障或用户音量设置错误，也不能说历史所有记忆故障均来自这一点。9/22 自动工具也记录过类似缺失，本轮只有停止后保存与候选发布取得了新的成功证据。

源码只读定位：DialogEngineManager 的 provider 路径使用内置播放器、player audio callback参数为false，而状态机等待 synthesisEnded 与 playerFinished；自动工具仅增加 decoder PCM 观察，并要求原生完成与恢复聆听。是否因该参数组合抑制3019/3020必须用SDK契约和最小对照证实，不能仅凭参数名称下结论。

后续修复应聚焦此播放完成契约和工具等价性，不重写本次已成功的保存链、不把TTS ended强行当播放结束。取得本地对照与明确事件契约后，再新短场验证；新短场完成候选可见、审核、正式记忆及冷读回查后，才能开启物理20分钟。

## 未执行与保留

- 第二轮、短场完整候选 UI/审核/正式/冷读链：NOT_RUN。
- 20分钟仅准备110条本地PCM素材；未启动。65分钟未执行。
- 独立文字键盘入口、独立档案手动输入、物理麦克风/扬声器：NOT_RUN。
- 未重发未知业务写入，未处理历史失败任务，未清空/删除数据，未 commit/push。
- 两条本场候选保留 pending；未借自动化绕过用户审核语义。
- 历史seq5、9/24首次慢启动与池耗尽首发组合仍保持未决。

## 证据入口

[本轮摘要](result-summary.json) · [签名与源码身份](reused-build.json) · [SHORT03原始失败](SHORT03/result.json) · [本场SDK诊断](SHORT03/diagnostic-events.json) · [停止后服务器查询](SHORT03/post-stop-server-summary.json) · [查询脚本](current-scene-readonly.py) · [测后健康](backend-after-short-health.json) · [工具检查](tool-tests.log)
