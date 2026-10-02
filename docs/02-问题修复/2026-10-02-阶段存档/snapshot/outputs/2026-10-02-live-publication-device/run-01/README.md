# 2026-10-02 发布修复部署与真机验收

用户本轮明确授权部署及真机测试。后端仅3份产品源码变更，旧工作区改动保留，不commit/push，不重试历史失败任务。

## 冻结与预检

- 真实DeepSeek检查：8次合成调用，组织/独立审核/历史关系链通过；2个不同事件保留2主题。数据库写入0。原始证据：[真实模型日志](/Users/gaominge/Documents/liftora/outputs/2026-10-02-live-publication-fallback/run-01/real-provider.log)。本次没有触发deferred分支，不能替代本地受控退路测试。
- 新release：`live-publication-fallback-20261002`，前版`live-ten-minute-20261002`。备份、迁移verify、API与6个启用Worker指纹全部核对后才启动真机。
- `runtime-manifest.json`含968文件，只有3个产品文件与前版不同，无schema变更。
- `stage-reuse.json`：复用上一轮已签名iOS包；重新验证当前源码、工具、隔离源码及完整签名bundle指纹，全部一致。
- `prepare-scenes.py`：本次专用新合成语音，短场社区陶艺，长场阳台种植；没有调整产品代码、工具成功断言或Gate。
- `short/`：第一次本地语音准备因沙箱语音服务返回0字节而停止，未安装/启动/请求模型；证据保留。`short-new/`与`natural10/`为重新准备的新场。

## 验收顺序

短场完整链（Live→持久化→待确认→确认→正式记忆→新进程冷读）通过后，才消耗其short receipt启动自然十分钟。十分钟以产品自己的收尾告别为准，完成当前轮后关闭，不由工具在600秒强切。发生错误继续记录产品既有收尾和有限恢复，不人为重发业务请求。

仅操作本次新建合成场景。旧正式记忆及历史任务不修改；音量不作为阻断。PCM注入真实iPhone SDK，输出真实播放；不声称覆盖空气传播的麦克风/扬声器声学验收。

## 状态

部署PASS；短场数据链PASS（用户反馈提示问题未决）；自然十分钟FAIL，后台发布2主题36成员，但手机状态未知/错误提示，正式确认与冷读NOT_RUN。1200秒恢复观察以OBSERVATION_TIMEOUT结束，没有新增测试或修改。详见final-status.json及归档报告。
