# 主题草稿重组修复：部署及真机证据

2026-10-04。结论：[部署与真机验证](../../../02-问题修复/记忆系统/2026-10-04-会中草稿重组重复合并/部署与真机验证.md)。

**DEPLOY_PASS / DEVICE_REGROUP_PASS / DEVICE_FULL_CHAIN_INCOMPLETE**。短场闭环通过；长场6主题29事实全发布、12正式active、17待确认，自动全场确认因旧记忆提案保护FAIL，未掩盖。

- `execution-plan.md`：授权范围、短场门禁、自然十分钟、纯add保护。
- `prepare-release.sh/log`、`activate-release.sh/log`：构建、备份、0128核验、API和六Worker替换。
- `backend-app-sha256.json`、`server-preflight.json`、`verify-server-final.log`：七容器各323文件一致、服务健康。
- `validated-ios-identity.json`、`device-apps.json`、`device-lock-state.json`：未改iOS及原签名包复用依据。
- `short/manifest.json`、`natural10/manifest.json`：全新天文与摄影合成语音；本次短场凭据只消费一次。
- `short/result.json`、`short/cold-readback.json`、`short/short-receipt.json`：短场3事实正式确认及冷读通过。
- `natural10/result.json`：原工具FAIL、22轮、真实音频/正文、自然关麦、保护拦截；保留原值。
- `*-timeline.jsonl`、`*-recovery-server-timeline.jsonl`：手机与服务器逐阶段证据，含结束意图与发布。
- `natural10-server-organizing.json`：本次实际命中会中regroup-split-v2，四个组织/审核子单元完成。
- `natural10-server-final.json`、`natural10-server-settled.json`：约109秒观察窗内published及82请求保持不变。
- `*-publication-members.json`：manifest与实际发布成员集合一致，无omitted。
- `natural10-theme-guard-metadata.json`：审核操作类型、候选状态及按本場来源统计的12 active正式记忆；未读旧记忆正文。
- `*-metrics.json`：原工具结果派生观测；派生摘要已区分formalReadbackProofCount=0与formalActiveCountVerifiedInDatabase=12，避免把未形成完整回读凭证误写为零条正式记忆；完整正式回读未执行。
- `audio-start-metrics.json`：2/2、22/22非静音/播放完成与开始送音时点；不是空气声学测试。
- `normal-relaunch.json`：测试后正常启动，无新Live。
- `prepare.log`、`fixture-sandbox-rejected/`、`prepare-elevated.log`：开测前语音合成装配错误与重建记录。
- `natural10-metadata-query-error.log`：只读诊断LIKE百分号错误，修正后取证成功，不是业务失败。

未提交/push；未删除历史数据或重试旧任务。测试已结束。
