# final-07 冷启动负例计数失败定位

## 已证实

`final-07/logs/logical65-short.log:317` 的 `3 > 2` 出现在短场已进入候选后的冷启动错误绑定负例，未证明短场保存链再次失败。

现有负例按 wrong-command、wrong-thread、wrong-session 顺序使用同一个真实 BackendClient/本地服务器，并以全局 `deliveryStatusReadCount` 前后差作为单变体计数。原标准允许每个变体 1–2 次 GET，业务 POST 必须为 0。

日志显示：wrong-thread 的首次读取在行 249 开始，263 返回 bindingMismatch；其异步策略完成回调在 266 又启动只读 GET，296 才完成。与此同时下一 wrong-session 已在 276 开始、282 发起自己的 GET，303 再次核实、315 完成。因此前一变体的迟到请求与下一计数区间重叠。

真实触发调用是 `viewDidLoad` 调用 `refreshOwnerTruthInterviewNaturalInputProductEntryPolicy`，其异步完成处理遍历 retained 恢复器执行 `verifyStatus(startsNewRound: true)`。依据本次证据，不将标准原本允许的第二次读取定性为产品缺陷；先修正测试的跨变体生命周期隔离。不得把上限 2 改为 3。

## 第一种隔离尝试及失败证据

只修改测试：在每个变体已达到预期业务结果后释放 Controller，等待 weak 引用变 nil，再等待 Coordinator 离开 checking 并 suspend，最后读取请求计数。

`short-check/green/logical65-short.xcresult` 保留此尝试：1 个测试失败，0 构建错误。wrong-command 实际是 2 GET、0 业务写入；wrong-command 与 wrong-thread 均因 UIKit Controller 未在 15 秒内释放而失败，两次等待进而触发服务器 30 秒候选可见性超时。故 weak 对象销毁不是此测试可依赖的完成信号，这份结果不能标为 PASS。

没有修改产品恢复策略，没有修改 GET 上限，没有删掉零 POST 或磁盘原 command 断言。后续应使用明确的测试生命周期关闭信号或不可串场的传输归属计数，保留本次失败，不用重跑偶然通过掩盖。
