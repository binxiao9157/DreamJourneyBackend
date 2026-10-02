# Live韧性保存部署与真机验收 · 2026-09-30

本轮已停止继续开场，进入只读取证与讨论。当前测试/演示账号仅操作本场合成数据。静音不是硬要求。物理麦克风/扬声器声学效果另验。

## 已完成

- 后端发布 `live-resilient-20260930`；备份任务成功，回滚镜像保留；0125–0128迁移通过。
- API及六Worker各965运行文件与本地一致，schema0128、恢复开关启用、重建后重启计数0；健康检查通过。
- iPhone已安装STAGE02签名包，签名有效至2026-10-01 01:58:44 UTC。原App数据保留。
- 短场A：2用户轮/4正文，1张主题卡、3事实；真实界面读取、Controller确认、正式记忆及新PID冷回查通过。
- 物理20分钟目标长场FAIL：17分19秒、36轮完成，第37次输入时audioSchedulerStalled；短B/40分钟NOT_RUN。
- 后续正文自动补到72条，但两次恢复快照均themeValidate.noReliableThemes失败，无主题发布；长场正式记忆NOT_RUN。
- [现场问题与证据记录](现场问题与证据记录.md)：保留失败瞬间及后续推进，先分析讨论，再设计修改。
- [原因分析及证据边界](analysis/原因分析.md)：批内自引用结果过早完成、安全复核JSON模式请求不合规已定位；手机性能首因仍未决。本轮只读分析，无修改或新模型调用。
- [后续补充修复方案](../../../02-问题修复/记忆系统/2026-09-30-长场模型合同与异常收尾/2026-09-30-长场模型合同与异常收尾补充修复方案.md)：仅设计；包含错误后继续观察补传、尾批、候选及正式记忆的独立验收。本场原FAIL不变。

## 工具与边界

使用实际iPhone SpeechEngine STREAM输入、火山ASR/文字/原生播放器及真实整理模型。未伪造转写、正文POST、候选或模型结果。主题列表与详情通过真实Controller，测试桥接代替手动点确认，非XCUITest触摸命中测试。

为新协议适配主题卡读取/确认、40分钟220轮及会后等待预算。确认前以只读服务器核对绑定本场session/productSession/account/source和每条原文hash，再允许本场纯新增候选通过实际Controller提交；旧记忆变更提案拒绝。未审核历史候选、未重处理历史任务。

20分钟110轮、40分钟220轮均为最低物理时长，等待实际回答可能延长。长场内容以同主题补充/重复及末尾补充为主，不宣称等同220个独立事实或所有维度质量测试。

## 证据

- [部署日志](deploy.log)、[运行版本核对](deployment-verification.log)、[源指纹预检](source-preflight.json)
- [签名有效期](signature-preflight.json)、[工具检查](tool-tests-final.log)
- [短场A](SHORT_A/result.json)、[冷回查](SHORT_A/cold-readback.json)、[逐消息来源绑定](SHORT_A/scene-binding.json)、[时延](SHORT_A/timing-summary.json)
- [20分钟过程](LONG20/result.json)

STAGE01因工具Info.plist路径假设错误未构建，保留失败目录；纠正为实际Resources/Info.plist后STAGE02签名成功。产品源码没有因此修改，旧本地验收证据仍保留。
