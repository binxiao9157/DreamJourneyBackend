# Live 持久化回归根因核查证据

日期：2026-09-17。范围：源码与历史修改只读分析、隔离源码反例。未修改 App/后端业务代码，未运行完整 App 测试，未访问真机或生产数据。

主文档：[Live 持久化回归溯源与真实保存链修复设计](../../02-问题修复/记忆系统/采集与会后保存/2026-09-17-Astra-Live持久化回归溯源与真实保存链修复设计.md)。

确定缺陷：生产 FeatureGate accountGeneration 是 24 位认证来源摘要，新增 Live authority 却要求它等于 AccountLease UUID。合格生产授权被拒；部分组合测试注入 UUID，掩盖差异。

## 文件

- `authority-guard-introduction.patch`：2026-09-16 11:35:24 北京时间加入错误比较的原始补丁，**只供历史对照，不能当修复应用**。
- `authority-guard-patch-result.json`：原始工具成功回执，包含当时 diff。
- `authority-guard-introduction.txt`：补丁调用及 UTC 时间。
- `authority-introduction-index.json`：对该已知开发记录的定点检索索引。
- `run_authority_probe.py`：提取当前源码片段并执行 Swift 反例，使用合成认证输入，不使用网络/真实账号。
- `authority-source-probe.swift`：实际生成和运行的 Swift 程序。外围端口为最小替身。
- `source-manifest.json`：实际提取的来源、行号、文件及片段 SHA-256。
- `authority-probe.stdout.json` / `authority-probe.stderr.txt`：本轮运行结果，正常退出，stderr 为空。
- `workspace-baseline.json`：本轮工作区分支、HEAD、相关源码指纹。

复现命令：

```sh
python3 /Users/gaominge/Documents/liftora/outputs/2026-09-17-live-persistence-root-cause-audit/run_authority_probe.py
```

`writeBoundaryReached` 是隔离反例回调 spy 次数，不是 HTTP 计数。结果证明生产身份合同不兼容及测试输入掩盖问题，不证明 Controller、网络、数据库、候选或真机修复通过。生产源码修正后，原脚本中“当前错误必须复现”的断言可能失败；应保留这份缺陷证据并用正式红绿测试验证修复，不要改写历史结果。
