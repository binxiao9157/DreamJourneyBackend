# 十分钟告别同音色真机验证

2026-10-04，用户授权安装、真机测试。当前状态 RUNNING。

顺序：同版短场→物理十分钟自然收尾。实际 iPhone / 火山 SDK STREAM合成语音输入 / 真实播放器 / 已部署API与Worker / 真实模型及数据库。只确认本场纯新增候选，旧记忆关系保护继续生效。用户已允许有声测试。不卸载、不清数据、不登出、不重放旧场。

检查：开麦耗时、逐轮音频/文字/保存、当前会话火山告别与播放排空、告别不污染正文、自动关麦、候选发布、确认与冷启动回查、页面状态。物理麦克风输入与人工听感独立记录。

使用当前源码隔离签名构建。首次 STAGE 因新告别处理多出同名 SDK case，测试注入锚点拒绝歧义；保留失败副本。仅将本场工具副本的普通 decoder 回调锚点收紧，未改产品代码、断言或失败处理；重新 STAGE02。

服务器只读校验7容器各323文件一致且 /live 200，不重部署后端。

## 最终结果

短場PASS；长场自然关闭/48正文/1主题25事实发布通过，正式确认保护拦截，原工具FAIL保留。告别代码走火山同会话，完整播放和人工听感未闭环。

- `final-summary.json`：分层状态。
- `short/result.json`、`short/cold-readback.json`、`short/short-receipt.json`：新短场及同版本长场门禁。
- `natural10/result.json`、`natural10-timeline.jsonl`：原始FAIL和全过程。
- `natural10-publication-members.json`：25事实发布集合精确一致，遗漏0。
- `natural10-theme-guard-metadata.json`：21add/3dispute/1temporalChange、全部pending、本场正式0。
- `natural10-server-final.json`、`natural10-server-settled.json`：published/62请求稳定。
- `natural10/native-diagnostics/`：只读补取的本场脱敏SDK日志；自动导出超时，专用告别事件缺失。
- `source-sha256.json`、`STAGE02/lab-build.json`、`STAGE02/binary.json`、`short/identity.json`：源码、工具、包及执行指纹。
- `normal-relaunch.json`：测试后普通启动成功，无新Live。

完整解读：[安装与真机报告](../../../02-问题修复/Live语音与开麦/2026-10-04-十分钟告别音色不一致/部署与真机验证.md)。
