# S2 Live能力初探与真机门禁

2026-10-08。状态：PROVIDER_UPDATE_NEXT_TURN_PASS / SDK_NOT_RUN / DEVICE_NOT_RUN。仅能力初探，不是S2完成，未改iOS或部署。

## 已查证的入口

本地SpeechEngineToB头文件有UpdateConfig指令3011及ConfigUpdated回调3022；产品DialogEngineManager当前仍采用sessionSnapshot。现有submitTurnKnowledgeContext只用于隔离RAG探针，不能直接打开当成本轮安全注入。

[火山官方实时语音接口](https://docs.volcengine.com/docs/DoubaoVoice/End-to-endreal-timespeechlargemodelAPIaccessdocument?lang=zh)描述UpdateConfig(201)及ConfigUpdated(251)，配置相关字段需完整覆盖。ChatRAGText会影响音频回答，不能将它当成静默记忆更新。文档并未据此证明正在生成的这一轮会使用更新。

## 已做的隔离实验

执行脚本及原始事件：[run-01](</Users/gaominge/Documents/liftora/outputs/2026-10-08-memory-dialogue-biography/run-01>)中的live-update-capability-probe.py、run-live-probe.py、s2-live-update-provider.jsonl。

使用现有自有服务器火山配置，model1.2.1.1，2个独立会话、4次纯合成文本查询，没有连接手机、查询业务DB或写入记忆。

1. 新会话无标记事实：回答“不知道”。
2. 更新完整SP为标记“晨蓝松”，收到251后才发问题：回答“晨蓝松”。
3. 更新为“浅绿竹”，收到251后才发问题：回答“浅绿竹”。
4. 完成旧会话，再开无事实会话：回答“不知道”。
5. 两次更新到回执约59ms，回执前没有350/352音频事件；两次会话都收到152，连接收到52。

因此只证明**更新回执之后新发起的问题**可采用新内容。没有测试ASR结束后正在生成的同轮、生效预算、插话、旧请求晚到、满容量、手机SDK或真实麦克风路径。

音频包有返回，但初探用format=pcm而脚本把峰值按16bit解释，位深未独立核实，`pcmPeak`不作为可解码/音色/声学通过证据。本轮仅裁定配置和文本采用，保留原始产物，不把峰值误当音频质量证明。

## 真机前后的实施约束

- 保持原生火山回答、既有音色和收尾策略；不恢复旧的逐轮外部答案播报。
- 先在隔离SDK实验入口验证完整UpdateConfig与回调匹配，不把回执视为同轮采用，不对已有生产场次操作。
- 更新载荷应由服务端当前账号/档案/权限及正式版本构建，沿既有角色预算；不能允许客户端任意替换已授权SP。保护原音色、速度及上下文身份。
- 接入前必须检查当前relay的StartSession contextHash/lease校验与会中更新的授权一致性；不能把绕过首包绑定作为实现。
- 实验逐项覆盖：无重复音频、下一轮采用、插话、切题、停止/重开、迟到、总预算和容量；不通过就保留快照能力、回到方案讨论。
- 最小接入后，才执行独立短场→自然十分钟保护回归；包含保存/候选可见/正式确认/实际召回/状态自动变化及音频。不以本初探代替这些门禁。

下一次需要用户接入并解锁iPhone，先做SDK能力短测。此时还不能把整体功能写成可发布，S3—S5仍依赖S2通过。

## 手机已接入后的进度

2026-10-08：手机可用，隔离 SDK 探针已完成无签名构建；签名过期且 Xcode 未读到登录账号，未能安装。见[真机准备与签名阻塞记录](<S2-真机准备与签名阻塞记录.md>)。本地签名阻塞不改变上文能力边界，SDK_NOT_RUN / DEVICE_NOT_RUN 保持。

## 后续更新：签名和信任阻塞已解除

2026-10-08，用户恢复Xcode登录并信任开发者后，已完成两轮iPhone SDK原配置更新实验，普通App已恢复。见[真机实验报告](<S2-SDK原配置更新真机实验报告.md>)。上文NOT_RUN是实验前的历史状态；当前为SDK_AUTHORIZED_SNAPSHOT_REPLAY_PASS / S2_PARTIAL，不等于新记忆注入通过。
