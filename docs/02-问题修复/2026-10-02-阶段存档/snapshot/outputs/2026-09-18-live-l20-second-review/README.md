# Live 长会话独立复核证据

本目录由 Astra 于 2026-09-18 生成。正式 iOS 工作区没有被本次修改；实验源文件位于 `isolated-probe-workspace.json` 指定的临时副本。未进行真机、生产数据或部署操作。

交接文档：[独立复核与局部修复指导](../../02-问题修复/记忆系统/长对话整理/2026-09-18-Astra-Live长会话反复失败-独立复核与局部修复指导.md)。

## 有效结论与结果包

1. `fixture-fresh-at-refresh.xcresult`：只改测试刷新器，原 70/48 方法通过；业务源码与用户工作区相同。摘要 `fixture-only-result.json`。
2. `fixture-corrections-and-recovery-negative.xcresult`：另 8 个原失败方法校正装配后通过；新增认证准备失败反例失败。摘要 `followup-probes-result.json`。此阶段仍未修改业务源码。
3. `recovery-eventual-completion-red-bound.xcresult`：有效的“认证准备先失败、再恢复并关闭原场次”业务反例，原业务源码失败。摘要 `resume-red-bound-result.json`。
4. `recovery-prototype-targeted-green.xcresult`：同一有效反例转绿，8 个定向用例全部通过。摘要 `prototype-targeted-result.json`。
5. `recovery-prototype-affected-regression.xcresult`：518 个 OwnerTruth + 51 个明确选择的账号/Echo/音频测试全部通过。摘要及方法清单 `prototype-regression-result.json`。

`recovery-eventual-completion-red.xcresult` 与 `recovery-eventual-completion-red-rendezvous.xcresult` 是准备探针期间未正确命中预定失败阶段的结果，分别暴露测试事件同步/注入时机问题，**不作为业务红证据**。只使用 `red-bound` 与 `targeted-green` 的相同有效断言作修前修后对照。

## 集成参考

- `isolated-fixture-only.patch`：第一个 70/48 凭据签发时间校正。
- `isolated-final-prototype.patch`：相对于复核基线的最终实验差异，包含两个业务文件和一个测试文件。
- `isolated-recovery-prototype.patch`：仅两个业务文件的局部实验修复。
- `final-source-verification.json`：原工作区四个关键文件未变，临时副本 Swift 差异仅上述三个文件。
- `prepare-isolated-*.py` 和中间 patch：隔离实验过程，不是对正式工作区直接执行的安装脚本。
- `auth-fixture-expiry-probe.swift`：从实际认证合同提取的辅助探针；它不替代 Controller 组合测试。

原型通过上述范围的本地验证，不代表已集成到 Sol 工作区，也不代表真机验收完成。最终集成还应按交接文档验证重试期间明确 deny、迟到准备回调、曝光后 notSent 等直接受影响的边界，并运行最终源码的本地构建检查。真机仅由用户之后主动发起。
