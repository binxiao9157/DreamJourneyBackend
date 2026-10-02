# 测试、UIQA 与构建摘要

记录时间：2026-09-14T15:06:48+0800

## 环境

- Xcode 26.6 (17F113)
- 测试设备：iPhone 17 Pro Simulator，iOS 26.5 (23F77)
- iOS 基线 HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`
- Backend 基线 HEAD：`a25b993922fc90dde1e689d19e51becb68fccdbf`

## 红测

1. `evidence/pre-fix/FM-policy-five-gaps-red.xcresult`
   - 5 项中 3 FAIL、2 PASS。
   - 真实失败：P1 首个等待者取消中断其他等待者；P3 页面返回不重读；P5 详情失败后旧编辑仍可进入。
2. `evidence/pre-fix/FM-policy-strengthened-red.xcresult`
   - 3 项中 2 FAIL、1 PASS。
   - 真实失败：P3 三页面生命周期；P4 防抖窗口旧响应仍提交。
3. `evidence/pre-fix/FM-policy-runtime-deadline-red-v2.xcresult`
   - 1/1 FAIL。
   - 真实失败：整体期限过后，迟到 runtime 200 仍触发正式资源 GET。

前两版 P2 测试因 authority epoch 变化提前触发了账号门禁，不能证明整体期限问题；最终采用 `runtime-deadline-red-v2` 作为有效业务红证据。

## 绿测

- 定向组合：`evidence/post-fix/FM-policy-supplement-targeted-03.xcresult`
  - 11/11 PASS，0 FAIL，0 SKIP。
  - 包含 P1-P5、新旧 flight 隔离、真实策略 HTTP/缓存/GET、认证恢复、诊断 trace/attempt。
- 完整受影响回归：`evidence/post-fix/ownertruth-audio-full-regression-03.xcresult`
  - 434/434 PASS，0 FAIL，0 SKIP。
  - 范围：`OwnerTruthContractsTests`、`AudioOwnerLeaseModelTests`。
- 后端保持性门禁：62/62 PASS。
  - 命令：`STORE_BACKEND=memory PYTHONPATH=. .venv/bin/python -m unittest tests.test_owner_truth_formal_memory tests.test_owner_truth_formal_memory_api tests.test_owner_truth_person_memory_profile tests.test_release_policy`
  - 第一次误用 pytest 时 `.venv` 缺少 pytest，属于工具入口错误；改用仓库 unittest 入口后通过，不作为业务红测。

## 模拟器 UIQA

最终目录：`evidence/post-fix/formal-memory-uiqa/fm-policy-supplement-20260914-03/`

- 正式记忆列表：PASS。
- 正式记忆详情及 3 条历史快照：PASS。
- 人物公开副本编辑：PASS。
- 人物公开副本预览：PASS。
- 四个结果 JSON 均为 `completed=true`，人工检查截图非空、可读、无布局重叠。

前两次 UIQA 保留为失败证据：发布选择页在 UIKit 触发未变化搜索回调后清空选择，导致下一步不可用。生产列表增加“搜索文字实际变化”判定后，第三次通过。

## 构建

- 模拟器 Debug：`build/simulator-build.xcresult`，`status=succeeded`，0 error。
- 通用 iOS 设备目标：`build/generic-ios-device-build.xcresult`，`status=succeeded`，0 error。
- UIQA 模拟器构建：`evidence/post-fix/formal-memory-uiqa/fm-policy-supplement-20260914-03/install/build.log`，`BUILD SUCCEEDED`。
- 现有第三方 SDK 与旧源码警告仍存在，但没有新增编译错误。

## 差异与脱敏

- iOS 与 Backend `git diff --check`：PASS。
- 本 run 输出扫描未发现 Bearer 凭据、`sk-` 密钥或序列化 access token。
- 诊断只保存随机 trace、attempt、阶段、白名单原因、耗时和请求计数；没有正文、完整响应、请求头或业务原始 hash。
