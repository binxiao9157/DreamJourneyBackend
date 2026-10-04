# Run终态修复部署/真机证据

日期2026-10-04。最终结论见[部署与真机报告](../../../02-问题修复/记忆系统/2026-10-04-发布后Run终态未收敛/部署与真机验证.md)。

- `prepare-release.log` / `activate-release.log`：构建、备份、0128只读核验、API和六Worker替换及稳定性。
- `backend-app-sha256.json` / `verify-server-final.log`：323运行文件、七容器无差异，服务健康。
- `validated-ios-identity.json`：复用签名包完整身份。工具/产品iOS未变。
- `short`、`short02`：两个独立短场，result及cold-readback通过；各自short-receipt只消费一次。
- `natural10`：首次不足十分钟的用例FAIL，原因详见`fixture-failure.md`，原始记录不改。异常后15事实全发布，未正式确认。
- `natural10b`：617.076秒自然关闭、42正文、26事实全发布；正式确认因temporalChange保护停止。原始工具FAIL保留。
- 各场`*-timeline.jsonl`：手机状态/正文计数/关闭阶段逐次变化；`*-recovery-server-timeline.jsonl`：绑定本场账号会话的只读服务器快照。
- `*-publication-members.json`：所有manifest atom与已发布成员集合核对。
- `natural10b-theme-guard-metadata.json`：只读提案操作元数据；未确认涉及旧记忆的提案。第一次辅助脚本固定了旧目录，在本机因缺sourceID报错、未发远端查询；改为显式scene后成功，错误输出保留。
- `normal-relaunch.json`：结束测试后正常启动App，不再开麦。

原始状态与专项结论分开：Run修复专项真实通过；最终十分钟正式确认及冷读未完成，冷启动稳定UI无现场结论。未删除旧记忆、重试旧场、改历史Run或push。
