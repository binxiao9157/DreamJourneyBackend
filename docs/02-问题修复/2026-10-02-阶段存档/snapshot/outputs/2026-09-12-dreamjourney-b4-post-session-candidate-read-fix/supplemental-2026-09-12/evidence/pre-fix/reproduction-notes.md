# 受控修复前反例说明

日期：2026-09-12

为避免回退或污染仍包含其他任务修改的主工作区，本次在 `/tmp/DreamJourney_b4_prefx_repro` 建立当前源码隔离副本，只恢复与两个缺口对应的旧行为：

1. 页面恢复入口只枚举 admission 后的 follow-up，不枚举 completion checkpoint 和 outbox。
2. 网络任务完成时无论是否存在 HTTP 响应头，都记录 `responseReceived`。

随后运行当前新增的真实恢复入口与真实 `DreamJourneyBackendClient` 回归测试。结果保存在 `red-recovery-diagnostics.log`：

- 六个 end/ack/admit 阶段均未发现可恢复协调器。
- DNS 失败仍出现 `responseReceived`，违反“无 HTTP 响应时状态码和响应事件均缺失”的合同。
- 共执行 2 项测试，出现 13 个断言失败，`TEST FAILED`。

该隔离副本不含生产数据，不安装设备，不修改主工作区，也不作为交付源码。修复后的对应绿测见 `../post-fix/green-recovery-related-late.log` 和 `../post-fix/green-diagnostics.log`。
