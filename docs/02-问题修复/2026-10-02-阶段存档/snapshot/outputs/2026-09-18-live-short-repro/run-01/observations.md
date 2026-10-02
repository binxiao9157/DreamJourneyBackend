# 短对话复现：用户观察与阶段事实

日期：2026-09-18。用户主动发起，保留当前安装与数据，无业务代码修改。

- 测试前待确认记忆：46 条。
- 用户完成两轮 Live，等待回答结束并手动停止。
- 用户观察：对话已保存，等待整理（约2秒）→正在整理（约2秒）→本次整理失败，原对话已保留，可稍后重试；按钮为文字回响。
- 用户澄清：看到本场内容指回响页对话气泡，并非候选列表；第二轮声音确实回答第二轮内容，但气泡文字仍停在第一轮。
- 测试后用户确认候选仍46条；23:22:09、23:22:11两次候选GET和UI提交也为46。
- 本场workflow日志关联：sha256:0f46a9c3dfd5f672。完成链trace：sha256:610a23ded88debd4。
- 23:19:47关闭时：2条用户正文已落盘；4/4登记内容服务端确认；无partial、无缺正文。
- 同场end/ACK/admit成功及检查点落盘；23:19:53第6次状态GET后终态观察落盘。结合用户文案，为terminalFailure，不是budgetExhausted。
- 只读提取follow-up元数据后，本场短会话记录已不存在，不能从该文件确认短场failureCode。
- 文件保留的既有长场sha256:e716db2a62d198a3在23:18:48恢复时为terminalFailure，failureCode=candidateExtraction.responseContract.invalid。这个码只属于已匹配的长场，不能冒充短场失败码。
- 当前仅诊断，未重试写入、未清理手机历史、未部署、未commit/push。
- 后续获授权服务器只读查询已精确关联本次短场：Source包含2条user和1条assistant，共162字，后台job首次处理terminalFailed、候选0，failureCode=candidateExtraction.responseContract.invalid。
- 长场亦完成精确关联：Source包含16条user和15条assistant，共2018字，首次处理failed、候选0。原报告本场pendingReview结论需纠正。
- 整理接口实际为api.deepseek.com，Live语音为openspeech.bytedance.com；两场均未触发已发现的长文本输入上限。
